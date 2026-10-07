"""Berechnung und Darstellung der sperrenbezogenen Einsatz-Anfahrt."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from math import cos, radians

from sqlalchemy.orm import Session

from app.config import settings
from app.models.incident import Incident
from app.models.master import OrgSettings
from app.models.road_closure import RESTRICTION_TYPES, IncidentRoadClosure, IncidentRoute, RoadClosure
from app.services import einsatz_routing, road_closure_geo_service, road_closure_service
from app.services.broadcast import broadcast_org, manager
from app.services.einsatz_routing import RoutingError

logger = logging.getLogger("einsatzleiter.einsatz_route")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def routing_start_for_org(db: Session, org_id: int) -> tuple[float, float] | None:
    """Liefert ausschließlich den explizit konfigurierten Fahrzeug-Startpunkt."""
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if row is None or row.routing_start_lat is None or row.routing_start_lng is None:
        return None
    return row.routing_start_lat, row.routing_start_lng


def _active_visible_closures(db: Session, org_id: int, now: datetime) -> list[RoadClosure]:
    return [
        closure
        for closure in road_closure_service.visible_closures_q(db, org_id)
        .filter(RoadClosure.geometry_geojson.isnot(None))
        .all()
        if road_closure_service.compute_status(closure, now) == "active"
    ]


def closure_fingerprint_for_org(db: Session, org_id: int, now: datetime | None = None) -> str:
    now = now or _now()
    return road_closure_geo_service.fingerprint(
        [(closure.id, closure.updated_at) for closure in _active_visible_closures(db, org_id, now)]
    )


def relevant_closures(
    db: Session,
    org_id: int,
    route_geom: dict | None,
    dest: tuple[float, float],
    now: datetime | None = None,
) -> tuple[list[RoadClosure], str]:
    now = now or _now()
    if route_geom:
        min_lat, min_lng, max_lat, max_lng = road_closure_geo_service.bbox(route_geom)
    else:
        min_lat = max_lat = dest[0]
        min_lng = max_lng = dest[1]
    centre_lat = (min_lat + max_lat) / 2
    lat_pad = 500 / 111320
    lng_pad = 500 / (111320 * cos(radians(centre_lat)))
    closures = road_closure_service.active_closures_in_bbox(
        db, org_id, min_lat - lat_pad, min_lng - lng_pad, max_lat + lat_pad, max_lng + lng_pad, now
    )
    return closures, closure_fingerprint_for_org(db, org_id, now)


def _clear_associations(db: Session, route_row: IncidentRoute) -> None:
    rows = (
        db.query(IncidentRoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(
            IncidentRoadClosure.incident_route_id == route_row.id,
            IncidentRoadClosure.org_id == route_row.org_id,
        )
        .all()
    )
    for row in rows:
        db.delete(row)


def _clear_route(route_row: IncidentRoute) -> None:
    route_row.route_geojson = None
    route_row.route_distance_m = None
    route_row.route_duration_s = None
    _clear_alternative(route_row)


def _clear_alternative(route_row: IncidentRoute) -> None:
    route_row.alternative_route_geojson = None
    route_row.alternative_distance_m = None
    route_row.alternative_duration_s = None
    route_row.alternative_streets_json = None
    route_row.detour_distance_m = None
    route_row.detour_duration_s = None


def _error_text(error: BaseException) -> str:
    return str(error)[:500]


async def compute_incident_route(
    db: Session, route_row: IncidentRoute, *, provider=None, now: datetime | None = None
) -> IncidentRoute:
    """Berechnet eine Route; persistiert nur im aktuellen Unit of Work."""
    now = now or _now()
    if route_row.org_id is None:
        raise ValueError("Einsatzroute ohne Organisation.")
    route_row.calculated_at = now
    route_row.closure_fingerprint = closure_fingerprint_for_org(db, route_row.org_id, now)
    route_row.routing_error = None
    incident = (
        db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(Incident.id == route_row.incident_id)
        .first()
    )
    logger.info("incident_route.started", extra={"incident_id": route_row.incident_id, "org_id": route_row.org_id})
    route_row.lease_until = None
    route_row.stale = False
    if incident is None or incident.status != "active":
        route_row.status = "disabled"
        db.flush()
        return route_row
    if incident.lat is None or incident.lng is None:
        route_row.status = "no_location"
        route_row.dest_lat = route_row.dest_lng = None
        _clear_route(route_row)
        _clear_associations(db, route_row)
        db.flush()
        return route_row
    destination = (incident.lat, incident.lng)
    route_row.dest_lat, route_row.dest_lng = destination
    start = routing_start_for_org(db, route_row.org_id)
    if start is None:
        # Ohne Startpunkt keine Route: alte Route und Sperren-Zuordnungen nicht weiter anzeigen.
        route_row.status = "no_start"
        route_row.start_lat = route_row.start_lng = None
        _clear_route(route_row)
        _clear_associations(db, route_row)
        db.flush()
        return route_row
    route_row.start_lat, route_row.start_lng = start
    try:
        provider = provider or einsatz_routing.get_provider()
    except RoutingError as error:
        route_row.status = "disabled" if error.kind == "disabled" else "error"
        route_row.routing_error = _error_text(error)
        if route_row.status == "error":
            route_row.attempts += 1
        _clear_route(route_row)
        _clear_associations(db, route_row)
        logger.info("incident_route.failed", extra={"incident_id": incident.id, "kind": error.kind})
        db.flush()
        return route_row
    route_row.routing_provider = provider.name
    try:
        response = await asyncio.wait_for(
            provider.calculate_route(start, destination, alternatives=False),
            timeout=settings.EINSATZ_ROUTING_TIMEOUT_SECONDS + 1,
        )
    except (RoutingError, TimeoutError) as error:
        route_row.status = "error"
        route_row.routing_error = _error_text(error) or "Der Routingdienst hat nicht rechtzeitig geantwortet."
        route_row.attempts += 1
        _clear_route(route_row)
        _clear_associations(db, route_row)
        logger.info("incident_route.failed", extra={"incident_id": incident.id, "error": route_row.routing_error})
        db.flush()
        return route_row

    primary = response.primary
    route_row.route_geojson = json.dumps(primary.geometry, ensure_ascii=False)
    route_row.route_distance_m = primary.distance_m
    route_row.route_duration_s = primary.duration_s
    closures, route_row.closure_fingerprint = relevant_closures(
        db, route_row.org_id, primary.geometry, destination, now
    )
    geometries = {closure.id: json.loads(closure.geometry_geojson) for closure in closures if closure.geometry_geojson}
    relevance = road_closure_geo_service.classify(primary.geometry, destination, list(geometries.items()))
    closure_by_id = {closure.id: closure for closure in closures}
    _clear_associations(db, route_row)
    for item in relevance:
        closure = closure_by_id[item.closure_id]
        db.add(
            IncidentRoadClosure(
                org_id=route_row.org_id,
                incident_route_id=route_row.id,
                incident_id=incident.id,
                road_closure_id=closure.id,
                relevance=item.relevance,
                distance_to_route_m=item.distance_to_route_m,
                distance_to_destination_m=item.distance_to_destination_m,
                title_snapshot=closure.title,
                restriction_type_snapshot=closure.restriction_type,
                geometry_snapshot=closure.geometry_geojson,
                geometry_status_snapshot=closure.geometry_status,
            )
        )
    route_closures = [item for item in relevance if item.relevance == "route"]
    route_row.status = "affected" if route_closures else "ok"
    if route_closures:
        logger.info("incident_route.closure_detected", extra={"incident_id": incident.id, "count": len(route_closures)})
    avoid_closures = [
        closure_by_id[item.closure_id]
        for item in route_closures
        if closure_by_id[item.closure_id].restriction_type == "closed"
        and closure_by_id[item.closure_id].geometry_status == "ok"
    ]
    _clear_alternative(route_row)
    route_row.alternative_status = "none"
    if avoid_closures:
        closed_geometries = [geometries[closure.id] for closure in avoid_closures]
        try:
            if provider.supports_avoid_polygons:
                alt_response = await asyncio.wait_for(
                    provider.calculate_route(
                        start,
                        destination,
                        avoid_polygons=[road_closure_geo_service.avoid_polygon(geom) for geom in closed_geometries],
                        alternatives=False,
                    ),
                    timeout=settings.EINSATZ_ROUTING_TIMEOUT_SECONDS + 1,
                )
            else:
                alt_response = await asyncio.wait_for(
                    provider.calculate_route(start, destination, alternatives=True),
                    timeout=settings.EINSATZ_ROUTING_TIMEOUT_SECONDS + 1,
                )
            alternative = einsatz_routing.choose_alternative(alt_response, closed_geometries)
            if alternative is None:
                route_row.alternative_status = "unavailable"
            else:
                route_row.alternative_status = "ok"
                route_row.alternative_route_geojson = json.dumps(alternative.geometry, ensure_ascii=False)
                route_row.alternative_distance_m = alternative.distance_m
                route_row.alternative_duration_s = alternative.duration_s
                route_row.alternative_streets_json = json.dumps(alternative.street_names, ensure_ascii=False)
                route_row.detour_distance_m = alternative.distance_m - primary.distance_m
                route_row.detour_duration_s = alternative.duration_s - primary.duration_s
                logger.info("incident_route.alternative_found", extra={"incident_id": incident.id})
        except (RoutingError, TimeoutError):
            route_row.alternative_status = "unavailable"
    route_row.calculated_at = now
    route_row.attempts = 0
    route_row.next_attempt_at = None
    route_row.routing_error = None
    logger.info("incident_route.success", extra={"incident_id": incident.id, "status": route_row.status})
    db.flush()
    return route_row


async def notify_route_updated(incident_id: int, org_id: int) -> None:
    event = {"type": "incident_route_updated", "incident_id": incident_id, "org_id": org_id}
    try:
        await manager.broadcast(incident_id, event)
    except Exception:
        logger.exception("incident_route.notify_failed", extra={"incident_id": incident_id, "org_id": org_id})
    try:
        await broadcast_org(org_id, event)
    except Exception:
        logger.exception("incident_route.notify_failed", extra={"incident_id": incident_id, "org_id": org_id})


def _iso_z(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def route_payload(db: Session, incident: Incident, org_id: int) -> dict:
    route = (
        db.query(IncidentRoute)
        .execution_options(include_all_tenants=True)
        .filter(IncidentRoute.incident_id == incident.id, IncidentRoute.org_id == org_id)
        .first()
    )
    if route is None:
        return {"status": "pending"}
    rows = (
        db.query(IncidentRoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(IncidentRoadClosure.incident_route_id == route.id, IncidentRoadClosure.org_id == org_id)
        .all()
    )
    selected = [row for row in rows if row.relevance in {"route", "destination"}]
    closures = [
        {
            "id": row.road_closure_id,
            "title": row.title_snapshot,
            "restriction_type": row.restriction_type_snapshot,
            "restriction_label": RESTRICTION_TYPES.get(
                row.restriction_type_snapshot or "", row.restriction_type_snapshot
            ),
            "relevance": row.relevance,
            "distance_to_route_m": row.distance_to_route_m,
            "distance_to_destination_m": row.distance_to_destination_m,
            "geometry": json.loads(row.geometry_snapshot) if row.geometry_snapshot else None,
            "geometry_status": row.geometry_status_snapshot,
        }
        for row in selected
    ]
    return {
        "status": route.status,
        "calculated_at": _iso_z(route.calculated_at),
        "start": {"lat": route.start_lat, "lng": route.start_lng, "label": _start_label(db, org_id)},
        "distance_m": route.route_distance_m,
        "duration_s": route.route_duration_s,
        "route": json.loads(route.route_geojson) if route.route_geojson else None,
        "closures": closures,
        "nearby_count": sum(row.relevance == "nearby" for row in rows),
        "alternative_status": route.alternative_status,
        "alternative_route": json.loads(route.alternative_route_geojson) if route.alternative_route_geojson else None,
        "alternative_distance_m": route.alternative_distance_m,
        "alternative_duration_s": route.alternative_duration_s,
        "alternative_streets": json.loads(route.alternative_streets_json) if route.alternative_streets_json else [],
        "detour_distance_m": route.detour_distance_m,
        "detour_duration_s": route.detour_duration_s,
        "error": route.routing_error if route.status == "error" else None,
    }


def _start_label(db: Session, org_id: int) -> str | None:
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return row.routing_start_label if row else None
