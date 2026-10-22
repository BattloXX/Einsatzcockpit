"""SEC-01: Zugriffe mit ungueltigen Geraetezugaengen erkennen und aggregiert protokollieren.

Pro (Token-Hash-Praefix, Grund, Kanal) gibt es genau eine Zeile; wiederholte Versuche erhoehen
nur den Zaehler. Damit ein Geraet, das im Sekundentakt anfragt, keine Schreibflut erzeugt,
werden Zaehler im Speicher gesammelt und hoechstens alle ``_FLUSH_INTERVALL_S`` Sekunden
geschrieben (der erste Versuch wird sofort gespeichert). Es wird nie ein Token gespeichert,
nur die ersten 10 Zeichen seines SHA-256-Hashs.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from fastapi import Request

    from app.models.user import DeviceToken

logger = logging.getLogger(__name__)

REASON_UNKNOWN = "unknown"
REASON_REVOKED = "revoked"
REASON_USER_INACTIVE = "user_inactive"
REASON_SESSION_REVOKED = "session_revoked"

# Fehlercodes fuer den Client (Header X-ECP-Auth-Error); nur "endgueltige" Gruende
CLIENT_CODE = {
    REASON_UNKNOWN: "device_unknown",
    REASON_REVOKED: "device_revoked",
    REASON_USER_INACTIVE: "user_inactive",
    REASON_SESSION_REVOKED: "device_revoked",
}
AUTH_ERROR_HEADER = "X-ECP-Auth-Error"

_FLUSH_INTERVALL_S = 60.0
_HASH_PREFIX_LEN = 10

_lock = threading.Lock()
_pending: dict[tuple[str, str, str], int] = {}
_last_flush: dict[tuple[str, str, str], float] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def client_info(request: Request) -> dict[str, str | None]:
    """Nur tatsaechlich uebermittelte Merkmale (Header, Query) - nichts wird geraten."""
    def _wert(header: str, query: str, grenze: int) -> str | None:
        wert = request.headers.get(header) or request.query_params.get(query)
        return wert.strip()[:grenze] if wert and wert.strip() else None

    return {
        "ip": (request.client.host if request.client else None),
        "user_agent": (request.headers.get("user-agent") or "")[:255] or None,
        "app_version": _wert("X-ECP-App-Version", "app_version", 30),
        "platform": _wert("X-ECP-Platform", "platform", 20),
    }


def record_failure(
    request: Request,
    reason: str,
    channel: str,
    *,
    token_hash: str | None = None,
    device_token: DeviceToken | None = None,
) -> None:
    """Merkt einen Fehlversuch vor und schreibt ihn gedrosselt in die Datenbank.

    Darf den eigentlichen Request nie stoeren: eigene DB-Session, Fehler werden nur geloggt.
    """
    prefix = (token_hash or (device_token.token_hash if device_token else "") or "")[:_HASH_PREFIX_LEN]
    key = (prefix, reason, channel)
    device_token_id = device_token.id if device_token is not None else None
    info = client_info(request)
    jetzt = time.monotonic()
    with _lock:
        _pending[key] = _pending.get(key, 0) + 1
        letzter = _last_flush.get(key)
        if letzter is not None and jetzt - letzter < _FLUSH_INTERVALL_S:
            return
        _last_flush[key] = jetzt
        anzahl = _pending.pop(key)
    try:
        _schreibe(key, anzahl, device_token_id, info)
    except SQLAlchemyError:
        logger.exception("Geraete-Fehlversuch konnte nicht gespeichert werden")


def _schreibe(
    key: tuple[str, str, str], anzahl: int, device_token_id: int | None, info: dict[str, str | None],
) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import DeviceAuthFailure, DeviceToken

    prefix, reason, channel = key
    jetzt = _utcnow()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zeile = (
            db.query(DeviceAuthFailure)
            .filter_by(token_hash_prefix=prefix, reason=reason, channel=channel)
            .first()
        )
        if zeile is None:
            zeile = DeviceAuthFailure(
                token_hash_prefix=prefix, reason=reason, channel=channel,
                count=0, first_at=jetzt, last_at=jetzt,
            )
            db.add(zeile)
        zeile.count = (zeile.count or 0) + anzahl
        zeile.last_at = jetzt
        zeile.device_token_id = device_token_id or zeile.device_token_id
        zeile.ip = info.get("ip") or zeile.ip
        zeile.user_agent = info.get("user_agent") or zeile.user_agent
        zeile.app_version = info.get("app_version") or zeile.app_version
        zeile.platform = info.get("platform") or zeile.platform
        if zeile.device_token_id:
            token = db.get(DeviceToken, zeile.device_token_id)
            if token is not None:
                token.last_failed_at = jetzt
                token.fail_count = (token.fail_count or 0) + anzahl
        db.commit()
        logger.warning(
            "Ungueltiger Geraetezugang: Grund=%s Kanal=%s Token=%s... Versuche=%s",
            reason, channel, prefix[:6], zeile.count,
        )
    finally:
        db.close()


def flush_pending() -> int:
    """Schreibt alle noch gesammelten Zaehler (z. B. beim Beenden und in Tests)."""
    with _lock:
        offen = dict(_pending)
        _pending.clear()
    for key, anzahl in offen.items():
        try:
            _schreibe(key, anzahl, None, {})
        except SQLAlchemyError:
            logger.exception("Geraete-Fehlversuch konnte nicht gespeichert werden")
    return len(offen)


def reset_state() -> None:
    with _lock:
        _pending.clear()
        _last_flush.clear()
