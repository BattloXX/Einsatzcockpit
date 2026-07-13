"""Print & Alarm Gateway (ECPG) – Service: Feature-Flags, Pairing, Config-Sync,
Status-Ableitung.

Effektive Aktivierung: System-Flag (SystemSettings key "gateway_module_enabled"
== "true") UND Org-Flag (OrgSettings.gateway_module_enabled == True) — Muster
UAS/Objekt-Modul.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.security import (
    generate_gateway_token,
    generate_pairing_code,
    hash_api_key,
)
from app.models.gateway import (
    GATEWAY_STATUS_OFFLINE,
    GATEWAY_STATUS_ONLINE,
    GATEWAY_STATUS_UNPAIRED,
    PAIRING_CODE_TTL_MINUTES,
    Gateway,
    Printer,
)

# ── Feature-Flags ──────────────────────────────────────────────────────────────

def gateway_system_enabled(db: Session) -> bool:
    """Systemweiter Gateway-Flag aus SystemSettings. Fehlender Key → False."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "gateway_module_enabled").first()
    return row is not None and row.value == "true"


def gateway_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Gateway effektiv aktiv ⟺ System-Flag AN und Org-Flag AN."""
    if org_id is None:
        return False
    if not gateway_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return bool(org_s and org_s.gateway_module_enabled)


# ── Pairing ────────────────────────────────────────────────────────────────────

def erzeuge_pairing_code(db: Session, gateway: Gateway) -> str:
    """Erzeugt einen neuen Einmal-Code (10 min gültig) und speichert nur den Hash.

    Gibt den Klartext-Code zurück (wird nur einmal angezeigt).
    """
    code = generate_pairing_code()
    gateway.pairing_code_hash = hash_api_key(code)
    gateway.pairing_expires_at = datetime.now(UTC) + timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    return code


def _naive_utc(dt: datetime | None) -> datetime | None:
    """DB speichert naive UTC; Vergleichswerte konsistent machen."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def pair_gateway(db: Session, code: str) -> tuple[Gateway, str] | None:
    """Löst einen Pairing-Code ein → setzt langlebiges Device-Token.

    Sucht das Gateway anhand des Code-Hashes (org-übergreifend, da der Container
    noch keine Org kennt – aber der Code ist ein 8-stelliges Geheimnis). Gibt
    (gateway, raw_device_token) zurück oder None (unbekannt/abgelaufen).

    Läuft ohne Tenant-Kontext → set_tenant_context(db, None) muss der Aufrufer
    gesetzt haben.
    """
    if not code:
        return None
    code_hash = hash_api_key(code.strip().upper())
    now = datetime.now(UTC).replace(tzinfo=None)
    gw = (
        db.query(Gateway)
        .filter(Gateway.pairing_code_hash == code_hash)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if gw is None:
        return None
    expires_at = _naive_utc(gw.pairing_expires_at)
    if expires_at is None or expires_at < now:
        return None

    raw_token = generate_gateway_token()
    gw.device_token_hash = hash_api_key(raw_token)
    gw.pairing_code_hash = None
    gw.pairing_expires_at = None
    gw.status = GATEWAY_STATUS_OFFLINE  # verbindet sich gleich per WSS
    return gw, raw_token


def rotate_token(db: Session, gateway: Gateway) -> str:
    """Rotiert das Device-Token (altes wird ungültig). Gibt neues Klartext-Token zurück."""
    raw_token = generate_gateway_token()
    gateway.device_token_hash = hash_api_key(raw_token)
    return raw_token


def revoke_token(gateway: Gateway) -> None:
    """Widerruft das Device-Token – Gateway muss neu gekoppelt werden."""
    gateway.device_token_hash = None
    gateway.status = GATEWAY_STATUS_UNPAIRED


# ── Status-Ableitung ───────────────────────────────────────────────────────────

def derive_status(gateway: Gateway, *, connected: bool | None = None) -> str:
    """Bestimmt den Anzeigestatus. connected: Live-WS-Info (optional)."""
    if not gateway.device_token_hash:
        return GATEWAY_STATUS_UNPAIRED
    if connected:
        return GATEWAY_STATUS_ONLINE
    return GATEWAY_STATUS_OFFLINE


def mark_seen(db: Session, gateway: Gateway, *, version: str | None = None) -> None:
    """Aktualisiert last_seen_at/version/status bei hello/heartbeat."""
    gateway.last_seen_at = datetime.now(UTC).replace(tzinfo=None)
    gateway.status = GATEWAY_STATUS_ONLINE
    gateway.offline_alerted_at = None
    if version:
        gateway.version = version


# ── config_sync-Assembly ───────────────────────────────────────────────────────

def build_config_sync(db: Session, gateway: Gateway) -> dict:
    """Baut die vollständige Config, die das Gateway bei (Re-)Connect erhält:
    aktive Drucker (mit CUPS-Queue-Angaben), W&T- und Parser-Konfiguration."""
    printers = (
        db.query(Printer)
        .filter(Printer.gateway_id == gateway.id, Printer.aktiv == True)  # noqa: E712
        .execution_options(include_all_tenants=True)
        .all()
    )
    return {
        "gateway_id": gateway.id,
        "printers": [
            {
                "id": p.id,
                "name": p.name,
                "uri": p.uri,
                "defaults": p.defaults or {},
            }
            for p in printers
        ],
        "wut": gateway.wut_config or {},
        "parser": gateway.parser_config or {},
    }
