"""Direkter ActivityKit-Versand ueber APNs (HTTP/2, ES256)."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

import httpx
import jwt
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.models.incident import Incident
from app.models.live_activity import LiveActivityToken
from app.models.user import FcmToken, User

logger = logging.getLogger(__name__)
_jwt_lock = Lock()
_jwt_cache: tuple[tuple[str, str, str], float, str] | None = None
_unconfigured_logged = False


def is_configured() -> bool:
    if not all((settings.APNS_KEY_PATH, settings.APNS_KEY_ID, settings.APNS_TEAM_ID, settings.APNS_BUNDLE_ID)):
        return False
    try:
        with Path(settings.APNS_KEY_PATH).open("rb") as key:
            return bool(key.read(1))
    except OSError:
        return False


def _authorization() -> str:
    global _jwt_cache
    identity = (settings.APNS_KEY_PATH, settings.APNS_KEY_ID, settings.APNS_TEAM_ID)
    with _jwt_lock:
        now = time.time()
        if _jwt_cache is not None and _jwt_cache[0] == identity and 0 <= now - _jwt_cache[1] < 40 * 60:
            return _jwt_cache[2]
        encoded = jwt.encode(
            {"iss": settings.APNS_TEAM_ID, "iat": int(now)}, Path(settings.APNS_KEY_PATH).read_bytes(),
            algorithm="ES256", headers={"kid": settings.APNS_KEY_ID},
        )
        _jwt_cache = (identity, now, encoded)
        return encoded


def build_content_state(payload: dict) -> dict:
    started = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00"))
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return {
        "alarm_code": str(payload["alarm_type_code"]),
        "address": str(payload["address"]),
        "phase_index": int(payload["phase_index"]),
        "phase_count": int(payload["phase_count"]),
        "phase_label": str(payload["phase_label"]),
        "started_at": started.timestamp(),
        "unit_count": int(payload["unit_count"]),
        "is_exercise": bool(payload["is_exercise"]),
        "ended": False,
    }


def _send(db: Session, row: LiveActivityToken, aps: dict) -> None:
    now = int(time.time())
    host = "api.sandbox.push.apple.com" if settings.APNS_SANDBOX else "api.push.apple.com"
    headers = {
        "apns-push-type": "liveactivity",
        "apns-topic": f"{settings.APNS_BUNDLE_ID}.push-type.liveactivity",
        "apns-priority": "10",
        "apns-expiration": str(now + 3600),
        "authorization": f"bearer {_authorization()}",
    }
    with httpx.Client(http2=True, timeout=10.0) as client:
        response = client.post(f"https://{host}/3/device/{row.token}", headers=headers, json={"aps": aps})
    if response.status_code == 200:
        row.last_error = None
        return
    try:
        reason = response.json().get("reason", "")
    except (ValueError, AttributeError):
        reason = ""
    # Nur bekannte APNs-Gruende speichern: fremde Antworttexte koennten Tokens enthalten.
    known = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic", "TooManyRequests",
             "InternalServerError", "ServiceUnavailable", "ExpiredProviderToken", "InvalidProviderToken"}
    row.last_error = f"HTTP {response.status_code}" + (f": {reason}" if reason in known else "")
    logger.warning("APNs Token …%s: %s", row.token[-6:], row.last_error)
    if response.status_code == 410 or (response.status_code == 400 and reason in {
        "BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic",
    }):
        db.delete(row)


def send_start(db: Session, row: LiveActivityToken, incident: Incident, payload: dict) -> None:
    title = f"{'[UEBUNG] ' if payload['is_exercise'] else ''}{payload['alarm_type_code']}"
    _send(db, row, {
        "timestamp": int(time.time()), "event": "start", "content-state": build_content_state(payload),
        "attributes-type": "EinsatzActivityAttributes",
        "attributes": {"incident_id": incident.id, "url": payload["url"]},
        "alert": {"title": title, "body": payload["address"] or "Kein Ort angegeben"},
    })


def send_update(db: Session, row: LiveActivityToken, payload: dict) -> None:
    _send(db, row, {"timestamp": int(time.time()), "event": "update", "content-state": build_content_state(payload)})


def send_end(db: Session, row: LiveActivityToken, payload: dict) -> None:
    now = int(time.time())
    _send(db, row, {
        "timestamp": now, "event": "end", "dismissal-date": now + 900,
        "content-state": {**build_content_state(payload), "ended": True},
    })


def _log_failure(message: str, exc: Exception, *args) -> None:
    # HTTPX-Ausnahmen enthalten die komplette Request-URL samt Token. Keine
    # Exception-Texte oder Traceback-Locals an das Log weitergeben.
    logger.exception(message, *args, exc_info=(RuntimeError, RuntimeError(type(exc).__name__), None))


def dispatch_incident(db: Session, incident: Incident, org_id: int | None, payload: dict, reason: str) -> None:
    global _unconfigured_logged
    try:
        with db.begin_nested():
            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=13)
            stale = db.query(LiveActivityToken).filter(
                LiveActivityToken.kind == "activity", LiveActivityToken.updated_at < cutoff,
            ).all()
            for row in stale:
                db.delete(row)
        if not is_configured():
            if not _unconfigured_logged:
                logger.debug("APNs Live Activity ist nicht konfiguriert")
                _unconfigured_logged = True
            return
        activity = db.query(LiveActivityToken).filter(
            LiveActivityToken.kind == "activity", LiveActivityToken.incident_id == incident.id,
        ).all()
        if reason == "created":
            existing_devices = {(row.user_id, row.device_token_id) for row in activity}
            ios_users = db.query(FcmToken.user_id).filter(FcmToken.platform == "ios")
            recipients = db.query(LiveActivityToken).join(User, User.id == LiveActivityToken.user_id).filter(
                LiveActivityToken.kind == "start", User.org_id == org_id,
                LiveActivityToken.user_id.in_(ios_users),
            ).all()
            recipients = [row for row in recipients if (row.user_id, row.device_token_id) not in existing_devices]
        else:
            recipients = activity
        for row in recipients:
            try:
                with db.begin_nested():
                    if reason == "created":
                        send_start(db, row, incident, payload)
                    elif reason == "closed":
                        send_end(db, row, payload)
                    else:
                        send_update(db, row, payload)
            except Exception as exc:
                row.last_error = type(exc).__name__[:120]
                _log_failure("APNs Versand fehlgeschlagen (Token …%s)", exc, row.token[-6:])
            if reason == "closed":
                # Auch bei Transportfehlern ist die Activity abgeschlossen.
                if not inspect(row).deleted:
                    db.delete(row)
        db.flush()
    except Exception as exc:
        _log_failure("APNs Live Activity Dispatch fehlgeschlagen (Einsatz %s)", exc, incident.id)
