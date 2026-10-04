"""Aufbereitung der explizit erlaubten Einsatz-Feed-Daten."""
from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT
from app.schemas.feed import FeedEinsatzBasis, FeedObjektBasis


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _phase(incident: Incident) -> str:
    statuses = [
        vehicle.unit_status for vehicle in incident.vehicles
        if vehicle.removed_at is None
    ]
    if not statuses:
        return "alarmiert"
    if all(status == "Einsatzbereit" for status in statuses):
        return "abschluss"
    if any(status == "Am Einsatzort" for status in statuses):
        return "einsatzstelle"
    return "anfahrt"


def _confirmed_objekt(incident: Incident) -> FeedObjektBasis | None:
    links = sorted(incident.objekt_links, key=lambda link: link.erstellt_am)
    for link in links:
        if link.status == OBJEKT_EINSATZ_BESTAETIGT and link.objekt is not None:
            return FeedObjektBasis(id=link.objekt.id, name=link.objekt.name)
    return None


def build_feed_einsatz_basis(incident: Incident, org: FireDept) -> FeedEinsatzBasis:
    """Baut einen Basis-DTO ohne ORM-Dump und ohne personenbezogene Daten."""
    started_at = _utc_iso(incident.started_at)
    assert started_at is not None
    return FeedEinsatzBasis(
        id=incident.id,
        nummer=incident.nummer,
        alarm_type_code=incident.alarm_type_code,
        status=incident.status,
        is_exercise=incident.is_exercise,
        phase=_phase(incident),
        started_at=started_at,
        closed_at=_utc_iso(incident.closed_at),
        taken_over_at=_utc_iso(incident.taken_over_at),
        departed_at=_utc_iso(incident.departed_at),
        on_scene_at=_utc_iso(incident.on_scene_at),
        ready_again_at=_utc_iso(incident.ready_again_at),
        address_street=incident.address_street,
        address_no=incident.address_no,
        address_city=incident.address_city,
        lat=incident.lat,
        lng=incident.lng,
        objekt=_confirmed_objekt(incident),
        unit_count=sum(1 for vehicle in incident.vehicles if vehicle.removed_at is None),
        timezone=org.timezone or settings.DEFAULT_TIMEZONE,
    )
