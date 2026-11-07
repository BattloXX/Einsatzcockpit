"""Pairing und Zugangsdaten des Infoscreen-Agents; Commit durch den Aufrufer."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.security import generate_pairing_code, hash_api_key
from app.models.infoscreen_agent import InfoscreenDevice

PAIRING_CODE_TTL_MINUTES = 15


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clear_presence(device: InfoscreenDevice) -> None:
    device.agent_connection_id = None
    device.agent_worker_id = None
    device.agent_presence_expires_at = None


def _clear_pairing(device: InfoscreenDevice) -> None:
    device.pairing_code_hash = None
    device.pairing_expires_at = None


def erzeuge_pairing_code(db: Session, device: InfoscreenDevice) -> str:
    """Neuer Einmal-Code, nur als Hash gespeichert; gesperrte Geraete ablehnen."""
    if device.org_id is None or device.status == "revoked":
        raise ValueError("Geraet muss einer Org zugeordnet und entsperrt sein")
    code = generate_pairing_code()
    device.pairing_code_hash = hash_api_key(code)
    device.pairing_expires_at = _utcnow() + timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    return code


def pair(db: Session, code: str) -> tuple[InfoscreenDevice, str] | None:
    """Org-Nachweis durch den Code; atomarer Vergleich verhindert paralleles Einloesen."""
    code = code.strip().upper()
    if len(code) != 8:
        return None
    device = (
        db.query(InfoscreenDevice)
        .filter(InfoscreenDevice.pairing_code_hash == hash_api_key(code))
        .execution_options(include_all_tenants=True)
        .populate_existing()
        .first()
    )
    if (device is None or device.org_id is None or device.status == "revoked"
            or device.pairing_expires_at is None or device.pairing_expires_at <= _utcnow()):
        return None
    token = "ecia_" + secrets.token_urlsafe(32)
    # Compare-and-Swap genau einer Zeile statt einer Lesesperre: Auch SQLite
    # erlaubt so nur einen Gewinner. Org und ID stammen aus dem Code-Nachweis.
    # Kein Bulk query().update(); der bedingte Schreibzugriff ist hier notwendig,
    # weil ein getrenntes Lesen/Mutieren denselben Code mehrfach einloesen koennte.
    result = db.connection().execute(
        update(InfoscreenDevice)
        .where(
            InfoscreenDevice.id == device.id,
            InfoscreenDevice.org_id == device.org_id,
            InfoscreenDevice.pairing_code_hash == hash_api_key(code),
            InfoscreenDevice.pairing_expires_at > _utcnow(),
            InfoscreenDevice.status.in_(("pending", "active")),
        )
        .values(
            device_token_hash=hash_api_key(token), status="active",
            pairing_code_hash=None, pairing_expires_at=None,
            agent_connection_id=None, agent_worker_id=None, agent_presence_expires_at=None,
        )
    )
    db.refresh(device)
    if result.rowcount != 1:
        return None
    return device, token


def rotate_token(db: Session, device: InfoscreenDevice) -> str:
    """Neues Token genau einmal liefern; altes Token und offene Codes verwerfen."""
    if device.org_id is None or device.status == "revoked":
        raise ValueError("Gesperrtes oder organisationsloses Geraet")
    token = "ecia_" + secrets.token_urlsafe(32)
    device.device_token_hash = hash_api_key(token)
    device.status = "active"
    _clear_pairing(device)
    _clear_presence(device)
    return token


def sperren(device: InfoscreenDevice) -> None:
    """Alle Zugangsdaten widerrufen; Entsperren reaktiviert niemals alte Tokens."""
    device.status = "revoked"
    device.device_token_hash = None
    _clear_pairing(device)
    _clear_presence(device)


def entsperren(device: InfoscreenDevice) -> None:
    """Zurueck zu pending; erneutes Pairing oder explizite Token-Rotation erforderlich."""
    if device.status == "revoked":
        device.status = "pending"
        device.device_token_hash = None
        _clear_pairing(device)
        _clear_presence(device)


def resolve_device_token(db: Session, token: str) -> InfoscreenDevice | None:
    """Public-Authentifizierung; nur aktive, organisationsgebundene Geraete."""
    if not token:
        return None
    return (
        db.query(InfoscreenDevice)
        .filter(InfoscreenDevice.device_token_hash == hash_api_key(token),
                InfoscreenDevice.status == "active", InfoscreenDevice.org_id.is_not(None))
        .execution_options(include_all_tenants=True)
        .first()
    )


def mark_seen(
    db: Session, device: InfoscreenDevice, *, ip: str | None = None,
    agent_version: str | None = None, os_version: str | None = None,
    hostname: str | None = None,
) -> None:
    """Naive UTC und ausschliesslich technische Metadaten aktualisieren."""
    if device.status != "active":
        raise ValueError("Geraet ist nicht aktiv")
    device.last_seen_at = _utcnow()
    if ip is not None:
        device.last_ip = ip
    if agent_version is not None:
        device.agent_version = agent_version
    if os_version is not None:
        device.os_version = os_version
    if hostname is not None:
        device.hostname = hostname


async def publish_config_changed(org_id: int, display_ids: list[int]) -> None:
    """Post-Commit-Hinweis fuer die Administration (S4/S5)."""
    from app.services.infoscreen_agent_events import publish_config_changed as publish

    await publish(org_id, display_ids)
