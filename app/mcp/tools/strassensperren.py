"""Lesewerkzeuge fuer sichtbare Strassensperren und Einsatz-Anfahrten."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.core.timezones import format_local_iso, local_date_to_utc, local_input_to_utc
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.incident import Incident, IncidentOrg
from app.models.master import FireDept
from app.models.road_closure import CLOSURE_STATUS, RESTRICTION_TYPES, RoadClosure, RoadClosureShare
from app.models.user import User
from app.services import einsatz_routing, road_closure_geo_service, road_closure_incident_service, road_closure_service
from app.services.einsatz_routing import RoutingError
from app.services.road_closure_flags import strassensperren_effective_enabled


def _org(context: MCPContext) -> FireDept | None:
    return context.db.get(FireDept, context.org_id)


def _iso(value: datetime | None, org: FireDept | None) -> str | None:
    return format_local_iso(value, org) or None


def _closure_dict(closure: RoadClosure, org_id: int, *, voll: bool = False, db: Any = None) -> dict[str, object]:
    org = db.get(FireDept, org_id) if db is not None else None
    own = closure.org_id == org_id
    result: dict[str, object] = {
        "id": closure.id,
        "title": closure.title,
        "street": closure.street,
        "from_text": closure.from_text,
        "to_text": closure.to_text,
        "restriction_type": closure.restriction_type,
        "restriction_label": closure.restriction_label,
        "priority": closure.priority,
        "status": road_closure_service.compute_status(closure),
        "status_label": CLOSURE_STATUS.get(road_closure_service.compute_status(closure), ""),
        "valid_from": _iso(closure.valid_from, org),
        "valid_until": _iso(closure.valid_until, org),
        "geometry_status": closure.geometry_status,
        "eigene": own,
        "besitzer_org": None
        if own or db is None
        else (db.get(FireDept, closure.org_id).name if db.get(FireDept, closure.org_id) else None),
    }
    if voll:
        creator = db.get(User, closure.created_by_user_id) if db is not None and closure.created_by_user_id else None
        result.update(
            {
                "description": closure.description,
                "direction": closure.direction,
                "max_weight_t": closure.max_weight_t,
                "max_height_m": closure.max_height_m,
                "max_width_m": closure.max_width_m,
                "max_length_m": closure.max_length_m,
                "source": closure.source,
                "source_url": closure.source_url,
                "geometry": json.loads(closure.geometry_geojson) if closure.geometry_geojson else None,
                "freigegeben_fuer": (
                    [
                        row.name
                        for row in db.query(FireDept)
                        .join(RoadClosureShare, RoadClosureShare.org_id == FireDept.id)
                        .filter(RoadClosureShare.road_closure_id == closure.id)
                        .all()
                    ]
                    if own and db is not None
                    else []
                ),
                "erstellt_von": getattr(creator, "display_name", None) or getattr(creator, "name", None),
                "erstellt_am": _iso(closure.created_at, org),
                "aktualisiert_am": _iso(closure.updated_at, org),
                "version": closure.version,
                "cancelled_at": _iso(closure.cancelled_at, org),
                "cancel_reason": closure.cancel_reason,
                "ui_link": f"/strassensperren/{closure.id}",
            }
        )
    return result


def _limit(limit: int, maximum: int = 200) -> None:
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit muss zwischen 1 und {maximum} liegen.")


def _date(value: str, *, end: bool, org: FireDept | None) -> datetime | None:
    if not value:
        return None
    parsed = local_date_to_utc(value, end=end, org=org) if "T" not in value else local_input_to_utc(value, org)
    if parsed is None:
        raise ValueError("Datum muss YYYY-MM-DD oder ein ISO-Datum mit Uhrzeit sein.")
    return parsed


def _summary(items: list[dict[str, object]]) -> str:
    if not items:
        return "Keine passenden Straßensperren gefunden."
    names = "; ".join(str(item["title"]) for item in items[:3])
    return f"{len(items)} Straßensperren: {names}."


@register_tool(
    name="strassensperren_liste",
    description="Listet sichtbare Straßensperren.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_liste(
    context: MCPContext,
    status: str = "current",
    von: str = "",
    bis: str = "",
    strasse: str = "",
    restriction_type: str = "",
    nur_eigene: bool = False,
    limit: int = 50,
) -> dict[str, object]:
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("status muss current, active, planned, expired, cancelled oder all sein.")
    if restriction_type and restriction_type not in RESTRICTION_TYPES:
        raise ValueError("Ungültiger Einschränkungstyp.")
    _limit(limit)
    org = _org(context)
    rows = road_closure_service.list_closures(
        context.db,
        context.org_id,
        status=None if status == "all" else status,
        von=_date(von, end=False, org=org),
        bis=_date(bis, end=True, org=org),
        text=strasse or None,
        restriction_type=restriction_type or None,
        scope="own" if nur_eigene else "all",
        limit=limit,
    )
    items = [_closure_dict(row, context.org_id, db=context.db) for row in rows]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


@register_tool(
    name="strassensperre_lesen",
    description="Liest eine sichtbare Straßensperre.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperre_lesen(context: MCPContext, road_closure_id: int) -> dict[str, object]:
    closure = road_closure_service.get_closure_for_org(context.db, context.org_id, road_closure_id, writable=False)
    result = _closure_dict(closure, context.org_id, voll=True, db=context.db)
    return result | {"zusammenfassung": _summary([result])}


@register_tool(
    name="strassensperren_suchen",
    description="Sucht sichtbare Straßensperren unscharf.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_suchen(
    context: MCPContext, suchtext: str, status: str = "all", von: str = "", bis: str = "", limit: int = 20
) -> dict[str, object]:
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("Ungültiger Status.")
    _limit(limit)
    words = [road_closure_service._normal(word) for word in re.findall(r"[\wäß]+", suchtext.lower()) if len(word) >= 3]
    if not words:
        raise ValueError("suchtext muss mindestens ein Wort mit drei Zeichen enthalten.")
    org = _org(context)
    rows = road_closure_service.list_closures(
        context.db,
        context.org_id,
        status=None if status == "all" else status,
        von=_date(von, end=False, org=org),
        bis=_date(bis, end=True, org=org),
    )
    ranked = []
    for row in rows:
        haystack = road_closure_service._normal(
            " ".join(filter(None, [row.title, row.street, row.description, row.from_text, row.to_text]))
        )
        score = sum(word in haystack for word in words)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda entry: (-entry[0], entry[1].valid_from))
    items = [_closure_dict(row, context.org_id, db=context.db) | {"score": score} for score, row in ranked[:limit]]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


@register_tool(
    name="strassensperren_im_gebiet",
    description="Findet sichtbare Sperren nahe eines Punktes.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_im_gebiet(
    context: MCPContext, lat: float, lng: float, radius_m: int = 2000, status: str = "current"
) -> dict[str, object]:
    if not 50 <= radius_m <= 20000:
        raise ValueError("radius_m muss zwischen 50 und 20000 liegen.")
    if status not in {"current", "active", "planned", "expired", "cancelled", "all"}:
        raise ValueError("Ungültiger Status.")
    rows = road_closure_service.list_closures(context.db, context.org_id, status=None if status == "all" else status)
    found = []
    for row in rows:
        if not row.geometry_geojson:
            continue
        distance = round(
            road_closure_geo_service.distance_point_to_geometry_m(lat, lng, json.loads(row.geometry_geojson))
        )
        if distance <= radius_m:
            found.append((distance, row))
    found.sort(key=lambda entry: entry[0])
    items = [_closure_dict(row, context.org_id, db=context.db) | {"entfernung_m": distance} for distance, row in found]
    return {"count": len(items), "items": items, "zusammenfassung": _summary(items)}


def _incident(context: MCPContext, incident_id: int) -> Incident:
    row = (
        context.db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(Incident.id == incident_id)
        .first()
    )
    member = (
        context.db.query(IncidentOrg)
        .filter(IncidentOrg.incident_id == incident_id, IncidentOrg.org_id == context.org_id)
        .first()
    )
    if row is None or (row.primary_org_id != context.org_id and member is None):
        raise ValueError("Einsatz nicht gefunden.")
    return row


def _route_answer(payload: dict[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {
        "routing_status": payload["status"],
        "normal_route": {"distance_m": payload.get("distance_m"), "duration_s": payload.get("duration_s")},
        "closures": payload.get("closures", []),
        "alternative_route": {
            "distance_m": payload.get("alternative_distance_m"),
            "duration_s": payload.get("alternative_duration_s"),
            "streets": payload.get("alternative_streets", []),
        },
        "detour_distance_m": payload.get("detour_distance_m"),
        "detour_duration_s": payload.get("detour_duration_s"),
    }
    result["zusammenfassung"] = (
        "Die normale Anfahrt ist betroffen; eine Umfahrung ist verfügbar."
        if payload["status"] == "affected" and payload.get("alternative_status") == "ok"
        else (
            "Die normale Anfahrt ist betroffen."
            if payload["status"] == "affected"
            else "Keine Straßensperren auf der Anfahrt."
        )
    )
    return result


@register_tool(
    name="einsatz_strassensperren",
    description="Liest gespeicherte Sperren einer Einsatzroute.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def einsatz_strassensperren(context: MCPContext, incident_id: int) -> dict[str, object]:
    incident = _incident(context, incident_id)
    payload = road_closure_incident_service.route_payload(context.db, incident, context.org_id)
    return {
        "incident_id": incident_id,
        "routing_status": payload["status"],
        "closures": payload.get("closures", []),
        "nearby_count": payload.get("nearby_count", 0),
        "zusammenfassung": "Keine Straßensperren auf der gespeicherten Anfahrt."
        if not payload.get("closures")
        else "Straßensperren auf der gespeicherten Anfahrt vorhanden.",
    }


async def _live(context: MCPContext, start: tuple[float, float], destination: tuple[float, float]) -> dict[str, object]:
    try:
        provider = einsatz_routing.get_provider()
    except RoutingError as error:
        raise ValueError(str(error)) from error
    try:
        payload = await road_closure_incident_service.evaluate_route(
            context.db, context.org_id, start, destination, provider
        )
    except RoutingError as error:
        raise ValueError(str(error)) from error
    return _route_answer(payload)


@register_tool(
    name="einsatz_anfahrtsroute_pruefen",
    description="Prüft eine Einsatz-Anfahrt oder berechnet sie live.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def einsatz_anfahrtsroute_pruefen(
    context: MCPContext,
    incident_id: int | None = None,
    lat: float | None = None,
    lng: float | None = None,
    vehicle_id: int | None = None,
) -> dict[str, object]:
    if (incident_id is None) == (lat is None or lng is None):
        raise ValueError("Genau incident_id oder lat und lng müssen angegeben werden.")
    if incident_id is not None:
        result = _route_answer(
            road_closure_incident_service.route_payload(context.db, _incident(context, incident_id), context.org_id)
        )
    else:
        start = road_closure_incident_service.routing_start_for_org(context.db, context.org_id)
        if start is None:
            raise ValueError("Kein Routing-Startpunkt konfiguriert.")
        assert lat is not None and lng is not None
        result = await _live(context, start, (lat, lng))
    if vehicle_id is not None:
        result["hinweis"] = "Fahrzeugprofile werden noch nicht berücksichtigt."
    return result


@register_tool(
    name="strassensperren_entlang_route",
    description="Berechnet live Sperren entlang einer freien Route.",
    required_roles=("readonly",),
    module_check=strassensperren_effective_enabled,
)
async def strassensperren_entlang_route(
    context: MCPContext,
    start_lat: float,
    start_lng: float,
    ziel_lat: float,
    ziel_lng: float,
    vehicle_id: int | None = None,
) -> dict[str, object]:
    result = await _live(context, (start_lat, start_lng), (ziel_lat, ziel_lng))
    if vehicle_id is not None:
        result["hinweis"] = "Fahrzeugprofile werden noch nicht berücksichtigt."
    return result
