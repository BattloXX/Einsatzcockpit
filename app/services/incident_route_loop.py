"""Leader-Loop fuer die asynchrone Berechnung von Einsatz-Anfahrtsrouten."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentOrg
from app.models.master import OrgSettings
from app.models.road_closure import IncidentRoadClosure, IncidentRoute
from app.services.road_closure_flags import strassensperren_system_enabled
from app.services.road_closure_incident_service import (
    closure_fingerprint_for_org,
    compute_incident_route,
    notify_route_updated,
    routing_start_for_org,
)

logger = logging.getLogger("einsatzleiter.incident_route_loop")
_LEASE = timedelta(seconds=30)
_EPSILON = 1e-6


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _different_coordinate(before: float | None, current: float | None) -> bool:
    return (before is None) != (current is None) or (
        before is not None and current is not None and abs(before - current) > _EPSILON
    )


def _route_changed(db: Session, route: IncidentRoute) -> tuple[object, ...]:
    associations = (
        db.query(IncidentRoadClosure.road_closure_id, IncidentRoadClosure.relevance)
        .execution_options(include_all_tenants=True)
        .filter(
            IncidentRoadClosure.incident_route_id == route.id,
            IncidentRoadClosure.org_id == route.org_id,
        )
        .all()
    )
    route_hash = hashlib.sha256((route.route_geojson or "").encode()).hexdigest()
    return route.status, route_hash, route.alternative_status, tuple(sorted(associations))


def discover_routes(db: Session, now: datetime | None = None) -> int:
    """Legt fehlende Routen an und markiert durch Eingaben veraltete Routen."""
    if not settings.EINSATZ_ROUTING_ENABLED or not strassensperren_system_enabled(db):
        return 0
    db.flush()
    now = now or _now()
    candidate_org_ids = {
        org_id
        for (org_id,) in db.query(OrgSettings.org_id)
        .execution_options(include_all_tenants=True)
        .filter(OrgSettings.strassensperren_modul_aktiv.is_(True))
        .all()
    }
    if not candidate_org_ids:
        return 0

    incidents = (
        db.query(Incident)
        .execution_options(include_all_tenants=True)
        .filter(Incident.status == "active")
        .all()
    )
    incident_ids = [incident.id for incident in incidents]
    collaborators: dict[int, set[int]] = {}
    if incident_ids:
        for incident_id, org_id in (
            db.query(IncidentOrg.incident_id, IncidentOrg.org_id)
            .execution_options(include_all_tenants=True)
            .filter(IncidentOrg.incident_id.in_(incident_ids))
            .all()
        ):
            collaborators.setdefault(incident_id, set()).add(org_id)

    changed = 0
    fingerprints: dict[int, str] = {}
    for incident in incidents:
        org_ids = collaborators.get(incident.id, set())
        if incident.primary_org_id is not None:
            org_ids = org_ids | {incident.primary_org_id}
        for org_id in org_ids & candidate_org_ids:
            route = (
                db.query(IncidentRoute)
                .execution_options(include_all_tenants=True)
                .filter(IncidentRoute.incident_id == incident.id, IncidentRoute.org_id == org_id)
                .first()
            )
            if route is None:
                try:
                    with db.begin_nested():
                        db.add(IncidentRoute(incident_id=incident.id, org_id=org_id, status="pending"))
                        db.flush()
                    changed += 1
                except IntegrityError:
                    # Ein paralleler Leader hat die eindeutige Route bereits angelegt.
                    continue
                continue
            if route.status in {"pending", "running"}:
                continue
            start = routing_start_for_org(db, org_id)
            fingerprint = fingerprints.setdefault(org_id, closure_fingerprint_for_org(db, org_id, now))
            coordinates_changed = _different_coordinate(route.dest_lat, incident.lat) or _different_coordinate(
                route.dest_lng, incident.lng
            )
            start_changed = _different_coordinate(route.start_lat, start[0] if start else None) or (
                _different_coordinate(route.start_lng, start[1] if start else None)
            )
            if coordinates_changed or start_changed or route.closure_fingerprint != fingerprint:
                if not route.stale:
                    route.stale = True
                    changed += 1
    db.commit()
    return changed


def claim_due_routes(db: Session, now: datetime | None = None, limit: int = 3) -> list[int]:
    """Reserviert faellige Routen kurzzeitig fuer diese Leader-Iteration."""
    now = now or _now()
    due = or_(
        IncidentRoute.status == "pending",
        # "running" mit abgelaufenem Lease: Worker ist mitten in der Berechnung ausgefallen.
        IncidentRoute.status == "running",
        IncidentRoute.stale.is_(True),
        and_(
            IncidentRoute.status == "error",
            IncidentRoute.attempts < 3,
            or_(IncidentRoute.next_attempt_at.is_(None), IncidentRoute.next_attempt_at <= now),
        ),
    )
    routes = (
        db.query(IncidentRoute)
        .execution_options(include_all_tenants=True)
        .join(Incident, Incident.id == IncidentRoute.incident_id)
        .filter(
            due,
            or_(IncidentRoute.lease_until.is_(None), IncidentRoute.lease_until < now),
            Incident.status == "active",
        )
        .limit(limit)
        .all()
    )
    ids = []
    for route in routes:
        route.lease_until = now + _LEASE
        if route.status == "pending":
            route.status = "running"
        ids.append(route.id)
    db.commit()
    return ids


def _retry_after_error(route: IncidentRoute, now: datetime) -> None:
    if route.attempts == 1:
        route.next_attempt_at = now + timedelta(seconds=30)
    elif route.attempts == 2:
        route.next_attempt_at = now + timedelta(seconds=120)
    elif route.attempts >= 3:
        route.next_attempt_at = None


async def _mark_unexpected_error(route_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route = (
            db.query(IncidentRoute)
            .execution_options(include_all_tenants=True)
            .filter(IncidentRoute.id == route_id)
            .first()
        )
        if route is not None:
            route.status = "error"
            route.routing_error = "Interner Fehler bei der Routenberechnung"
            route.attempts += 1
            route.lease_until = None
            route.stale = False
            _retry_after_error(route, _now())
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("incident_route.failed")
    finally:
        db.close()


async def process_route(route_id: int) -> None:
    """Berechnet eine reservierte Route und meldet tatsächliche Änderungen."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        route = (
            db.query(IncidentRoute)
            .execution_options(include_all_tenants=True)
            .filter(IncidentRoute.id == route_id)
            .first()
        )
        if route is None:
            return
        before = _route_changed(db, route)
        await compute_incident_route(db, route)
        if route.status == "error":
            _retry_after_error(route, _now())
        db.commit()
        changed = before != _route_changed(db, route)
        if changed and route.org_id is not None:
            try:
                await notify_route_updated(route.incident_id, route.org_id)
            except Exception:
                logger.exception("incident_route.failed")
    except Exception:
        logger.exception("incident_route.failed")
        db.rollback()
        await _mark_unexpected_error(route_id)
    finally:
        db.close()


async def run_incident_route_once(now: datetime | None = None) -> int:
    """Fuehrt eine kurze Discover-/Claim-Iteration des Routen-Workers aus."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        discover_routes(db, now)
        ids = claim_due_routes(db, now, limit=3)
    finally:
        db.close()
    await asyncio.gather(*(process_route(route_id) for route_id in ids))
    return len(ids)


async def incident_route_loop() -> None:
    while True:
        try:
            await run_incident_route_once()
        except Exception:
            logger.exception("incident_route.loop_failed")
        await asyncio.sleep(5)
