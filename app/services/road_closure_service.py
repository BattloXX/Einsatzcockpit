"""Persistenz und mandantenbewusste Operationen fuer Strassensperren."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.incident import Incident
from app.models.invitation import OrgPartner
from app.models.master import FireDept
from app.models.road_closure import (
    DIRECTIONS,
    GEOMETRY_STATUS,
    PRIORITIES,
    RESTRICTION_TYPES,
    IncidentRoute,
    RoadClosure,
    RoadClosureChange,
    RoadClosureShare,
)
from app.services import road_closure_geo_service as geo

logger = logging.getLogger("einsatzleiter.road_closure")
EDITABLE_FIELDS = {
    "title",
    "description",
    "city",
    "reference_number",
    "exceptions",
    "authority",
    "street",
    "from_text",
    "to_text",
    "direction",
    "valid_from",
    "valid_until",
    "restriction_type",
    "priority",
    "max_weight_t",
    "max_height_m",
    "max_width_m",
    "max_length_m",
    "geometry_geojson",
    "geometry_status",
    "geometry_quality",
    "geometry_meta_json",
    "source",
    "source_url",
}
_MEASURES = {"max_weight_t": 60, "max_height_m": 10, "max_width_m": 10, "max_length_m": 50}
RESTRICTION_TYPE_ALIASES: dict[str, str] = {
    "full": "closed", "vollsperre": "closed", "voll": "closed", "gesperrt": "closed", "sperre": "closed",
    "closure": "closed", "road_closed": "closed", "teilsperre": "partial", "halbseitig": "partial",
    "halbseitige sperre": "partial", "partial_closure": "partial", "lane_closed": "partial",
    "baustelle": "construction", "bau": "construction", "roadworks": "construction", "einbahn": "one_way",
    "einbahnregelung": "one_way", "oneway": "one_way", "gewicht": "weight_limit",
    "gewichtsbeschrankung": "weight_limit", "tonnage": "weight_limit", "hohe": "height_limit",
    "hoehe": "height_limit", "hohenbeschrankung": "height_limit", "breite": "width_limit",
    "breitenbeschrankung": "width_limit", "anrainer": "residents_only", "anrainerverkehr": "residents_only",
    "residents": "residents_only", "erschwert": "difficult_passage", "erschwerte durchfahrt": "difficult_passage",
    "sonstige": "other", "sonstiges": "other",
}
PRIORITY_ALIASES: dict[str, str] = {"niedrig": "low", "normal": "normal", "hoch": "high", "kritisch": "critical"}
DIRECTION_ALIASES: dict[str, str] = {
    "beide": "both", "beide richtungen": "both", "both_directions": "both", "hinrichtung": "forward",
    "vorwarts": "forward", "gegenrichtung": "backward", "ruckwarts": "backward",
}


def _alias_key(value: str) -> str:
    return value.strip().casefold().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")


def _allowed(values: dict[str, str]) -> str:
    return ", ".join(f"{key} ({label})" for key, label in values.items())


def normalize_restriction_type(value: str) -> str:
    key = _alias_key(value) if isinstance(value, str) else ""
    aliases = RESTRICTION_TYPE_ALIASES | {_alias_key(k): k for k in RESTRICTION_TYPES} | {
        _alias_key(label): key for key, label in RESTRICTION_TYPES.items()
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unbekannter restriction_type '{value}'. Zulässig: {_allowed(RESTRICTION_TYPES)}.")


def normalize_priority(value: str) -> str:
    key = _alias_key(value) if isinstance(value, str) else ""
    aliases = PRIORITY_ALIASES | {_alias_key(k): k for k in PRIORITIES} | {
        _alias_key(label): key for key, label in PRIORITIES.items()
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unbekannte priority '{value}'. Zulässig: {_allowed(PRIORITIES)}.")


def normalize_direction(value: str | None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    key = _alias_key(value) if isinstance(value, str) else ""
    aliases = DIRECTION_ALIASES | {_alias_key(k): k for k in DIRECTIONS} | {
        _alias_key(label): key for key, label in DIRECTIONS.items()
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unbekannte direction '{value}'. Zulässig: {_allowed(DIRECTIONS)}.")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=_iso, sort_keys=True)


def compute_status(closure: RoadClosure, now: datetime | None = None) -> str:
    now = now or _now()
    if closure.cancelled_at is not None:
        return "cancelled"
    if now < closure.valid_from:
        return "planned"
    if closure.valid_until is not None and now > closure.valid_until:
        return "expired"
    return "active"


def _merged(data: dict, existing: RoadClosure | None) -> dict:
    result = {name: getattr(existing, name) for name in EDITABLE_FIELDS} if existing else {}
    result.update(data)
    return result


def validate_closure_data(
    data: dict,
    *,
    partial: bool,
    existing: RoadClosure | None = None,
) -> dict:
    unknown = set(data) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unbekanntes Feld: {sorted(unknown)[0]}.")
    if partial and existing is None:
        raise ValueError("Teilaktualisierung benötigt eine bestehende Sperre.")
    data = dict(data)
    if "restriction_type" in data:
        data["restriction_type"] = normalize_restriction_type(data["restriction_type"])
    if "priority" in data and data["priority"] is not None:
        data["priority"] = normalize_priority(data["priority"])
    if "direction" in data:
        data["direction"] = normalize_direction(data["direction"])
    values = _merged(data, existing)
    if not partial or "title" in data:
        title = values.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValueError("Titel ist erforderlich und darf höchstens 200 Zeichen lang sein.")
        values["title"] = title.strip()
    valid_from = values.get("valid_from")
    if not isinstance(valid_from, datetime) or valid_from.tzinfo is not None:
        raise ValueError("Beginn muss ein naiver UTC-Zeitpunkt sein.")
    valid_until = values.get("valid_until")
    if valid_until is not None and (
        not isinstance(valid_until, datetime) or valid_until.tzinfo is not None or valid_until <= valid_from
    ):
        raise ValueError("Ende muss nach dem Beginn liegen.")
    if values.get("restriction_type") not in RESTRICTION_TYPES:
        raise ValueError("Ungültiger Einschränkungstyp.")
    if (not partial or existing is None) and values.get("priority") is None:
        values["priority"] = "normal"
    if values.get("priority") not in PRIORITIES:
        raise ValueError("Ungültige Priorität.")
    if values.get("direction") is not None and values["direction"] not in DIRECTIONS:
        raise ValueError("Ungültige Fahrtrichtung.")
    if values.get("geometry_quality") is not None and values["geometry_quality"] not in {
        "hoch", "mittel", "niedrig", "manuell"
    }:
        raise ValueError("Ungültige Geometriequalität.")
    meta = values.get("geometry_meta_json")
    if meta is not None:
        if not isinstance(meta, str) or len(meta) >= 20000:
            raise ValueError("Ungültige Geometriemetadaten.")
        try:
            json.loads(meta)
        except json.JSONDecodeError as exc:
            raise ValueError("Ungültige Geometriemetadaten.") from exc
    string_limits = {"city": 120, "reference_number": 120, "authority": 200, "source": 200, "source_url": 1000}
    for field, maximum in string_limits.items():
        value = values.get(field)
        if value is not None and (not isinstance(value, str) or len(value.strip()) > maximum):
            raise ValueError(f"Ungültiger Wert für {field}.")
        if isinstance(value, str):
            values[field] = value.strip() or None
    for field in ("description", "exceptions", "from_text", "to_text"):
        if isinstance(values.get(field), str):
            values[field] = values[field].strip() or None
    for field, maximum in _MEASURES.items():
        value = values.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum
        ):
            raise ValueError(f"Ungültiger Wert für {field}.")
    geometry = values.get("geometry_geojson")
    if geometry in (None, ""):
        values["geometry_geojson"] = None
        geometry_dict = None
    else:
        geometry_dict = geo.validate_geometry(geometry)
        values["geometry_geojson"] = json.dumps(geometry_dict, ensure_ascii=False, separators=(",", ":"))
    if isinstance(values.get("street"), str):
        values["street"] = values["street"].strip() or None
    if not values.get("street") and geometry_dict is None:
        raise ValueError("Mindestens Straße oder Geometrie ist erforderlich.")
    requested_status = values.get("geometry_status")
    if geometry_dict is None:
        values["geometry_status"] = "missing"
    elif "geometry_status" not in data and (not partial or getattr(existing, "geometry_status", None) == "missing"):
        values["geometry_status"] = "ok"
    elif requested_status not in GEOMETRY_STATUS:
        raise ValueError("Ungültiger Geometriestatus.")
    fields = {field for field in EDITABLE_FIELDS if field in data or not partial}
    if "geometry_geojson" in data and "geometry_status" not in data:
        fields.add("geometry_status")
    return {field: values.get(field) for field in fields}


def _set_bbox(closure: RoadClosure) -> None:
    if closure.geometry_geojson:
        (
            closure.bbox_min_lat,
            closure.bbox_min_lng,
            closure.bbox_max_lat,
            closure.bbox_max_lng,
        ) = geo.bbox(json.loads(closure.geometry_geojson))
    else:
        closure.bbox_min_lat = closure.bbox_min_lng = closure.bbox_max_lat = closure.bbox_max_lng = None


def _change(
    db: Session,
    closure: RoadClosure,
    action: str,
    user_id: int | None,
    source: str,
    mcp_tool: str | None,
    field: str | None = None,
    before: Any = None,
    after: Any = None,
) -> None:
    db.add(
        RoadClosureChange(
            org_id=closure.org_id,
            road_closure_id=closure.id,
            action=action,
            field=field,
            before_json=_json(before),
            after_json=_json(after),
            source=source,
            mcp_tool=mcp_tool,
            user_id=user_id,
        )
    )


def _audit(
    db: Session,
    action: str,
    closure: RoadClosure,
    user_id: int | None,
    **additional_payload: Any,
) -> None:
    payload = {
        "title": closure.title,
        "street": closure.street,
        "restriction_type": closure.restriction_type,
        "valid_from": _iso(closure.valid_from),
        "valid_until": _iso(closure.valid_until),
        **additional_payload,
    }
    write_audit(
        db,
        action,
        org_id=closure.org_id,
        user_id=user_id,
        entity_type="road_closure",
        entity_id=closure.id,
        payload=payload,
    )


def create_closure(
    db: Session,
    org_id: int,
    user_id: int | None,
    data: dict,
    *,
    source: str = "ui",
    mcp_tool: str | None = None,
) -> RoadClosure:
    values = validate_closure_data(data, partial=False)
    closure = RoadClosure(
        org_id=org_id,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
        created_via=source,
        **values,
    )
    _set_bbox(closure)
    db.add(closure)
    db.flush()
    _change(
        db,
        closure,
        "created",
        user_id,
        source,
        mcp_tool,
        after={field: getattr(closure, field) for field in EDITABLE_FIELDS},
    )
    _audit(db, "road_closure.created", closure, user_id)
    mark_routes_stale_for_closure(db, closure)
    logger.info("road_closure.created", extra={"closure_id": closure.id, "org_id": org_id})
    return closure


def update_closure(
    db: Session,
    closure: RoadClosure,
    user_id: int | None,
    changes: dict,
    *,
    expected_version: int | None = None,
    source: str = "ui",
    mcp_tool: str | None = None,
) -> list[dict]:
    if expected_version is not None and expected_version != closure.version:
        raise ValueError("Die Sperre wurde inzwischen geändert. Bitte laden Sie sie neu.")
    values = validate_closure_data(changes, partial=True, existing=closure)
    changed: list[dict] = []
    for field, after in values.items():
        before = getattr(closure, field)
        if before != after:
            setattr(closure, field, after)
            _change(db, closure, "updated", user_id, source, mcp_tool, field, before, after)
            changed.append({"feld": field, "vorher": _iso(before), "nachher": _iso(after)})
    if changed:
        _set_bbox(closure)
        closure.updated_by_user_id = user_id
        closure.version += 1
        _audit(
            db,
            "road_closure.updated",
            closure,
            user_id,
            felder=[item["feld"] for item in changed],
        )
        mark_routes_stale_for_closure(db, closure)
        logger.info("road_closure.updated", extra={"closure_id": closure.id, "org_id": closure.org_id})
    return changed


def confirm_geometry(
    db: Session, closure: RoadClosure, user_id: int | None, geometry: dict | str | None = None,
    expected_version: int | None = None, source: str = "ui", mcp_tool: str | None = None,
) -> None:
    if expected_version is not None and expected_version != closure.version:
        raise ValueError("Die Sperre wurde inzwischen geändert. Bitte laden Sie sie neu.")
    if geometry is not None:
        closure.geometry_geojson = json.dumps(
            geo.validate_geometry(geometry), ensure_ascii=False, separators=(",", ":")
        )
        closure.geometry_quality = "manuell"
        _set_bbox(closure)
    if not closure.geometry_geojson:
        raise ValueError(
            "Keine Geometrie vorhanden – zuerst strassensperre_geometrie_ermitteln oder geometry_geojson übergeben."
        )
    before = {"geometry_status": closure.geometry_status, "geometry_quality": closure.geometry_quality}
    closure.geometry_status = "ok"
    closure.updated_by_user_id, closure.version = user_id, closure.version + 1
    _change(db, closure, "geometry_confirmed", user_id, source, mcp_tool, before=before,
            after={"geometry_status": "ok", "geometry_quality": closure.geometry_quality})
    _audit(db, "road_closure.geometry_confirmed", closure, user_id)
    mark_routes_stale_for_closure(db, closure)


def deactivate_closure(
    db: Session,
    closure: RoadClosure,
    user_id: int | None,
    reason: str,
    *,
    source: str = "ui",
    mcp_tool: str | None = None,
) -> None:
    if closure.cancelled_at is not None:
        raise ValueError("Die Sperre ist bereits deaktiviert.")
    before = {"cancelled_at": closure.cancelled_at, "cancel_reason": closure.cancel_reason}
    closure.cancelled_at, closure.cancel_reason = _now(), reason
    closure.updated_by_user_id, closure.version = user_id, closure.version + 1
    _change(
        db,
        closure,
        "deactivated",
        user_id,
        source,
        mcp_tool,
        after={"cancelled_at": closure.cancelled_at, "cancel_reason": reason},
        before=before,
    )
    _audit(db, "road_closure.deactivated", closure, user_id, grund=reason)
    mark_routes_stale_for_closure(db, closure)


def reactivate_closure(
    db: Session,
    closure: RoadClosure,
    user_id: int | None,
    *,
    source: str = "ui",
    mcp_tool: str | None = None,
) -> None:
    if closure.cancelled_at is None:
        raise ValueError("Die Sperre ist nicht deaktiviert.")
    before = {"cancelled_at": closure.cancelled_at, "cancel_reason": closure.cancel_reason}
    closure.cancelled_at = closure.cancel_reason = None
    closure.updated_by_user_id, closure.version = user_id, closure.version + 1
    _change(db, closure, "reactivated", user_id, source, mcp_tool, before=before)
    _audit(db, "road_closure.reactivated", closure, user_id)
    mark_routes_stale_for_closure(db, closure)


def delete_closure(db: Session, closure: RoadClosure, user_id: int | None) -> None:
    mark_routes_stale_for_closure(db, closure)
    _audit(db, "road_closure.deleted", closure, user_id)
    logger.info("road_closure.deleted", extra={"closure_id": closure.id, "org_id": closure.org_id})
    db.delete(closure)


def partner_orgs_for(db: Session, org_id: int) -> list[FireDept]:
    return (
        db.query(FireDept)
        .join(OrgPartner, OrgPartner.partner_org_id == FireDept.id)
        .filter(
            OrgPartner.org_id == org_id,
            FireDept.is_active.is_(True),
            FireDept.deleted_at.is_(None),
        )
        .order_by(FireDept.name)
        .all()
    )


def shared_org_ids(db: Session, closure: RoadClosure) -> list[int]:
    return sorted(
        row[0]
        for row in db.query(RoadClosureShare.org_id)
        .filter(RoadClosureShare.road_closure_id == closure.id)
        .all()
    )


def set_shares(
    db: Session,
    closure: RoadClosure,
    org_ids: list[int],
    user_id: int | None,
    *,
    source: str = "ui",
    mcp_tool: str | None = None,
) -> tuple[list[int], list[int]]:
    owner_org_id = closure.org_id
    if owner_org_id is None:
        raise ValueError("Straßensperre ohne Besitzer-Organisation.")
    requested = {org_id for org_id in org_ids if org_id != owner_org_id}
    allowed = {org.id for org in partner_orgs_for(db, owner_org_id)}
    if not requested.issubset(allowed):
        raise ValueError("Freigabe nur an Partner-Organisationen möglich.")

    before = shared_org_ids(db, closure)
    before_set = set(before)
    added = sorted(requested - before_set)
    removed = sorted(before_set - requested)
    if not added and not removed:
        return [], []

    for org_id in added:
        db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_id))
    db.flush()
    mark_routes_stale_for_closure(db, closure, extra_org_ids=removed)
    for org_id in removed:
        share = db.get(RoadClosureShare, (closure.id, org_id))
        if share is not None:
            db.delete(share)

    after = sorted(requested)
    closure.updated_by_user_id = user_id
    closure.version += 1
    _change(db, closure, "shared", user_id, source, mcp_tool, "freigaben", before, after)
    _audit(db, "road_closure.shared", closure, user_id, hinzugefuegt=added, entfernt=removed)
    return added, removed


def visible_closures_q(db: Session, org_id: int):
    shares = select(RoadClosureShare.road_closure_id).where(RoadClosureShare.org_id == org_id)
    return (
        db.query(RoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(or_(RoadClosure.org_id == org_id, RoadClosure.id.in_(shares)))
    )


def get_closure_for_org(db: Session, org_id: int, closure_id: int, *, writable: bool) -> RoadClosure:
    closure = visible_closures_q(db, org_id).filter(RoadClosure.id == closure_id).first()
    if closure is None or (writable and closure.org_id != org_id):
        raise ValueError("Straßensperre nicht gefunden.")
    return closure


def list_closures(
    db: Session,
    org_id: int,
    *,
    status: str | None = None,
    von: datetime | None = None,
    bis: datetime | None = None,
    text: str | None = None,
    restriction_type: str | None = None,
    scope: str = "all",
    limit: int | None = None,
    now: datetime | None = None,
    geometrie: str | None = None,
) -> list[RoadClosure]:
    if status not in {None, "active", "planned", "expired", "cancelled", "current"} or scope not in {
        "all",
        "own",
        "shared",
    }:
        raise ValueError("Ungültiger Filter.")
    now = now or _now()
    q = visible_closures_q(db, org_id)
    if scope == "own":
        q = q.filter(RoadClosure.org_id == org_id)
    if scope == "shared":
        q = q.filter(RoadClosure.org_id != org_id)
    if status == "cancelled":
        q = q.filter(RoadClosure.cancelled_at.isnot(None))
    elif status == "active":
        q = q.filter(
            RoadClosure.cancelled_at.is_(None),
            RoadClosure.valid_from <= now,
            or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= now),
        )
    elif status == "current":
        q = q.filter(
            RoadClosure.cancelled_at.is_(None),
            or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= now),
        )
    elif status == "planned":
        q = q.filter(RoadClosure.cancelled_at.is_(None), RoadClosure.valid_from > now)
    elif status == "expired":
        q = q.filter(
            RoadClosure.cancelled_at.is_(None), RoadClosure.valid_until.isnot(None), RoadClosure.valid_until < now
        )
    if von:
        q = q.filter(or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= von))
    if bis:
        q = q.filter(RoadClosure.valid_from <= bis)
    if text:
        term = f"%{text}%"
        q = q.filter(
            or_(
                *[
                    field.ilike(term)
                    for field in (
                        RoadClosure.title,
                        RoadClosure.street,
                        RoadClosure.description,
                        RoadClosure.from_text,
                        RoadClosure.to_text,
                    )
                ]
            )
        )
    if restriction_type:
        q = q.filter(RoadClosure.restriction_type == restriction_type)
    if geometrie == "pruefen":
        q = q.filter(RoadClosure.geometry_status == "needs_review")
    elif geometrie == "ok":
        q = q.filter(RoadClosure.geometry_status == "ok")
    elif geometrie == "fehlt":
        q = q.filter(RoadClosure.geometry_status == "missing")
    elif geometrie not in {None, ""}:
        raise ValueError("Ungültiger Geometriefilter.")
    q = q.order_by(RoadClosure.valid_from)
    return q.limit(limit).all() if limit else q.all()


def active_closures_in_bbox(
    db: Session,
    org_id: int,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
    now: datetime | None = None,
) -> list[RoadClosure]:
    now = now or _now()
    return (
        visible_closures_q(db, org_id)
        .filter(
            RoadClosure.cancelled_at.is_(None),
            RoadClosure.valid_from <= now,
            or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= now),
            RoadClosure.geometry_geojson.isnot(None),
            RoadClosure.bbox_min_lat <= max_lat,
            RoadClosure.bbox_max_lat >= min_lat,
            RoadClosure.bbox_min_lng <= max_lng,
            RoadClosure.bbox_max_lng >= min_lng,
        )
        .all()
    )


def _normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower().replace("str.", "straße").replace("strasse", "straße"))


def find_duplicates(
    db: Session,
    org_id: int,
    *,
    street: str | None,
    from_text: str | None,
    to_text: str | None,
    valid_from: datetime,
    valid_until: datetime | None,
    geometry: dict | None = None,
    exclude_id: int | None = None,
) -> list[RoadClosure]:
    q = (
        db.query(RoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(
            RoadClosure.org_id == org_id,
            RoadClosure.cancelled_at.is_(None),
            or_(RoadClosure.valid_until.is_(None), RoadClosure.valid_until >= valid_from),
        )
    )
    if valid_until is not None:
        q = q.filter(RoadClosure.valid_from <= valid_until)
    if exclude_id:
        q = q.filter(RoadClosure.id != exclude_id)
    result = []
    for item in q.all():
        if _normal(item.street) != _normal(street):
            continue
        endpoints = (_normal(item.from_text) == _normal(from_text) and _normal(item.to_text) == _normal(to_text)) or (
            not _normal(item.from_text)
            and not _normal(item.to_text)
            and not _normal(from_text)
            and not _normal(to_text)
        )
        close = False
        if geometry is not None and item.geometry_geojson:
            min_lat, min_lng, max_lat, max_lng = geo.bbox(geometry)
            transformer = geo._projector((min_lng + max_lng) / 2, (min_lat + max_lat) / 2)
            close = (
                geo.to_metric(geometry, transformer).distance(
                    geo.to_metric(json.loads(item.geometry_geojson), transformer)
                )
                <= 50
            )
        if endpoints:
            result.append(item)
        elif close:
            result.append(item)
    return result


def mark_routes_stale_for_closure(
    db: Session, closure: RoadClosure, *, extra_org_ids: list[int] | None = None
) -> None:
    shared_org_ids = db.query(RoadClosureShare.org_id).filter(RoadClosureShare.road_closure_id == closure.id).all()
    org_ids = [closure.org_id] + [row[0] for row in shared_org_ids] + (extra_org_ids or [])
    rows = (
        db.query(IncidentRoute, Incident)
        .join(Incident, Incident.id == IncidentRoute.incident_id)
        .execution_options(include_all_tenants=True)
        .filter(IncidentRoute.org_id.in_(org_ids), Incident.status == "active")
        .all()
    )
    for route, incident in rows:
        mark = (
            not closure.geometry_geojson or not route.route_geojson or route.dest_lat is None or route.dest_lng is None
        )
        if not mark:
            assert closure.geometry_geojson is not None
            assert route.route_geojson is not None
            assert route.dest_lat is not None
            assert route.dest_lng is not None
            try:
                cbox = geo.bbox(json.loads(closure.geometry_geojson))
                rbox = geo.bbox(geo.validate_geometry(route.route_geojson))
                expand = 500 / 111320
                mark = not (
                    rbox[2] < cbox[0] - expand
                    or rbox[0] > cbox[2] + expand
                    or rbox[3] < cbox[1] - expand
                    or rbox[1] > cbox[3] + expand
                )
                mark = (
                    mark
                    or geo.distance_point_to_geometry_m(
                        route.dest_lat,
                        route.dest_lng,
                        json.loads(closure.geometry_geojson),
                    )
                    <= 500
                )
            except ValueError, json.JSONDecodeError:
                mark = True
        if mark:
            route.stale = True
