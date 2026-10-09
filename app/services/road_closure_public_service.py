"""Whitelisted public representation of road closures."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.core.timezones import format_local_datetime
from app.models.master import FireDept
from app.models.road_closure import CLOSURE_STATUS, DIRECTIONS, RESTRICTION_TYPES, RoadClosure
from app.services.road_closure_service import compute_status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def color(closure: RoadClosure) -> str:
    status = compute_status(closure)
    if status in {"planned", "expired", "cancelled"}:
        return "grey"
    if closure.restriction_type == "closed":
        return "red"
    if closure.restriction_type in {"partial", "one_way", "weight_limit", "height_limit", "width_limit"}:
        return "orange"
    return "yellow"


def public_closures_q(db: Session, org_id: int, *, now: datetime | None = None, include_planned: bool = True):
    now = now or _now()
    query = (
        db.query(RoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(
            RoadClosure.org_id == org_id,
            RoadClosure.cancelled_at.is_(None),
            or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= now),
        )
    )
    if not include_planned:
        query = query.filter(RoadClosure.valid_from <= now)
    return query.order_by(case((RoadClosure.valid_from <= now, 0), else_=1), RoadClosure.valid_from)


def einsatzgebiet(org: FireDept, closure: RoadClosure) -> str:
    return f"{org.name} – {closure.city}" if closure.city and closure.city not in org.name else org.name


def abschnitt(closure: RoadClosure) -> str | None:
    """Straße mit Von/Bis, ohne Platzhalter für fehlende Endpunkte."""
    if not closure.street:
        return None
    if closure.from_text and closure.to_text:
        return f"{closure.street}: {closure.from_text} – {closure.to_text}"
    if closure.from_text or closure.to_text:
        return f"{closure.street}: {closure.from_text or closure.to_text}"
    return closure.street


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def public_closure_dict(closure: RoadClosure, org: FireDept, *, now: datetime | None = None) -> dict:
    measures = []
    for field, label in (
        ("max_weight_t", "t"),
        ("max_height_m", "m Höhe"),
        ("max_width_m", "m Breite"),
        ("max_length_m", "m Länge"),
    ):
        value = getattr(closure, field)
        if value is not None:
            measures.append(f"max. {value:g} {label}")
    try:
        geometry = json.loads(closure.geometry_geojson) if closure.geometry_geojson else None
    except TypeError, json.JSONDecodeError:
        geometry = None
    bbox = [closure.bbox_min_lng, closure.bbox_min_lat, closure.bbox_max_lng, closure.bbox_max_lat]
    state = compute_status(closure, now)
    return {
        "id": closure.id,
        "title": closure.title,
        "city": closure.city,
        "street": closure.street,
        "from_text": closure.from_text,
        "to_text": closure.to_text,
        "abschnitt": abschnitt(closure),
        "direction": closure.direction,
        "direction_label": DIRECTIONS.get(closure.direction or "", closure.direction),
        "restriction_type": closure.restriction_type,
        "restriction_label": RESTRICTION_TYPES.get(closure.restriction_type, closure.restriction_type),
        "max_weight_t": closure.max_weight_t,
        "max_height_m": closure.max_height_m,
        "max_width_m": closure.max_width_m,
        "max_length_m": closure.max_length_m,
        "einschraenkungen": measures,
        "exceptions": closure.exceptions,
        "reason": closure.reason,
        "authority": closure.authority,
        "reference_number": closure.reference_number,
        "valid_from": _iso(closure.valid_from),
        "valid_until": _iso(closure.valid_until),
        "valid_from_local": format_local_datetime(closure.valid_from, org),
        "valid_until_local": format_local_datetime(closure.valid_until, org),
        "status": state,
        "status_label": CLOSURE_STATUS[state],
        "geometry": geometry,
        "bbox": bbox if all(value is not None for value in bbox) else None,
        "color": color(closure),
        "einsatzgebiet": einsatzgebiet(org, closure),
    }


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
