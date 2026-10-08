"""Access tokens for anonymous road-closure views."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import hash_api_key
from app.models.master import FireDept
from app.models.road_closure import ACCESS_TOKEN_ARTEN, RoadClosure, RoadClosureAccessToken
from app.services.road_closure_flags import strassensperren_effective_enabled

PREFIXES = {"status": "rcs_", "infoscreen": "rci_", "detail": "rcd_"}
DEFAULTS = {
    "zeige_geplante": True,
    "zeige_karte": True,
    "zeige_einschraenkungen": True,
    "zeige_grund": True,
    "rotation_sec": 0,
    "refresh_sec": 60,
}


def normalize_berechtigungen(art: str, values: dict | None) -> dict:
    """Return the small, explicit permission set used by status screens."""
    if art not in {"status", "infoscreen"}:
        return dict(DEFAULTS | (values or {}))
    values = values or {}
    result: dict[str, bool | int] = {key: bool(values.get(key, DEFAULTS[key])) for key in (
        "zeige_geplante", "zeige_karte", "zeige_einschraenkungen", "zeige_grund",
    )}
    def number(key: str, low: int, high: int) -> int:
        try:
            value = int(values.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            value = DEFAULTS[key]
        return max(low, min(high, value))
    result["refresh_sec"] = number("refresh_sec", 30, 600)
    result["rotation_sec"] = number("rotation_sec", 0, 600) if art == "infoscreen" else 0
    return result


class TokenUngueltig(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def create_token(
    db: Session,
    org_id: int,
    art: str,
    user_id: int | None,
    *,
    road_closure_id: int | None = None,
    label: str | None = None,
    expires_at: datetime | None = None,
    berechtigungen: dict | None = None,
) -> tuple[RoadClosureAccessToken, str]:
    if art not in ACCESS_TOKEN_ARTEN or (art == "detail" and road_closure_id is None):
        raise ValueError("Ungültige Token-Art.")
    if road_closure_id is not None:
        closure = (
            db.query(RoadClosure)
            .execution_options(include_all_tenants=True)
            .filter(RoadClosure.id == road_closure_id, RoadClosure.org_id == org_id)
            .first()
        )
        if closure is None:
            raise ValueError("Sperre nicht gefunden.")
    raw = PREFIXES[art] + secrets.token_urlsafe(32)
    token = RoadClosureAccessToken(
        org_id=org_id,
        art=art,
        road_closure_id=road_closure_id,
        label=label,
        token_hash=hash_api_key(raw),
        token_enc=encrypt_secret(raw),
        berechtigungen_json=json.dumps(normalize_berechtigungen(art, berechtigungen)),
        expires_at=expires_at,
        created_by_user_id=user_id,
        created_at=_now(),
    )
    db.add(token)
    write_audit(
        db,
        "road_closure.token_created",
        org_id=org_id,
        user_id=user_id,
        entity_type="road_closure",
        entity_id=road_closure_id,
        payload={"art": art},
    )
    return token, raw


def revoke_token(db: Session, token: RoadClosureAccessToken, user_id: int | None) -> None:
    token.revoked_at = _now()
    write_audit(
        db,
        "road_closure.token_revoked",
        org_id=token.org_id,
        user_id=user_id,
        entity_type="road_closure",
        entity_id=token.road_closure_id,
        payload={"art": token.art},
    )


def token_plain(token: RoadClosureAccessToken) -> str | None:
    try:
        return decrypt_secret(token.token_enc) if token.token_enc else None
    except Exception:
        return None


def public_url(raw: str, art: str) -> str:
    base = settings.effective_public_base_url.rstrip("/")
    paths = {
        "detail": "/oeffentlich/strassensperre/",
        "status": "/oeffentlich/strassensperren/",
        "infoscreen": "/infoscreen/strassensperren/",
    }
    return base + paths[art] + raw


def active_detail_token(db: Session, closure: RoadClosure) -> RoadClosureAccessToken | None:
    now = _now()
    return (
        db.query(RoadClosureAccessToken)
        .execution_options(include_all_tenants=True)
        .filter(
            RoadClosureAccessToken.road_closure_id == closure.id,
            RoadClosureAccessToken.art == "detail",
            RoadClosureAccessToken.revoked_at.is_(None),
            (RoadClosureAccessToken.expires_at.is_(None) | (RoadClosureAccessToken.expires_at > now)),
        )
        .order_by(RoadClosureAccessToken.created_at.desc())
        .first()
    )


def get_or_create_detail_token(
    db: Session, closure: RoadClosure, user_id: int | None = None
) -> tuple[RoadClosureAccessToken, str]:
    token = active_detail_token(db, closure)
    raw = token_plain(token) if token else None
    if token and raw:
        return token, raw
    if closure.org_id is None:
        raise ValueError("Sperre ohne Organisation.")
    return create_token(db, closure.org_id, "detail", user_id, road_closure_id=closure.id)


def resolve(db: Session, raw: str, art: str, *, now: datetime | None = None) -> tuple[RoadClosureAccessToken, FireDept]:
    now = now or _now()
    if art not in PREFIXES or not raw.startswith(PREFIXES[art]):
        raise TokenUngueltig()
    token = (
        db.query(RoadClosureAccessToken)
        .execution_options(include_all_tenants=True)
        .filter(RoadClosureAccessToken.token_hash == hash_api_key(raw))
        .first()
    )
    if not token or token.art != art or token.revoked_at or (token.expires_at and token.expires_at <= now):
        raise TokenUngueltig()
    org = db.get(FireDept, token.org_id) if token.org_id is not None else None
    if not org or not strassensperren_effective_enabled(org.id, db):
        raise TokenUngueltig()
    if token.last_used_at is None or token.last_used_at < now - timedelta(seconds=60):
        token.last_used_at = now
    return token, org


def resolve_detail(db: Session, raw: str, *, now: datetime | None = None):
    now = now or _now()
    token, org = resolve(db, raw, "detail", now=now)
    closure = (
        db.query(RoadClosure)
        .execution_options(include_all_tenants=True)
        .filter(RoadClosure.id == token.road_closure_id)
        .first()
    )
    if not closure or closure.org_id != token.org_id or closure.cancelled_at:
        raise TokenUngueltig()
    beendet = closure.valid_until is not None and closure.valid_until < now
    if closure.valid_until is not None and beendet and now - closure.valid_until > timedelta(days=30):
        raise TokenUngueltig()
    return token, org, closure, beendet
