"""Geräte-Pairing-PIN: kurzlebige, abtippbare Alternative zum QR-Code-Scan beim
Geräte-Login (Android-App, "Einheit-Gerät"). Muster: gateway_service.py
(erzeuge_pairing_code/pair_gateway), gleiche TTL-Konstante wie Gateway-Pairing.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.security import generate_pairing_code, generate_sms_gateway_token, hash_api_key
from app.models.gateway import PAIRING_CODE_TTL_MINUTES
from app.models.user import DeviceToken, SmsGatewayToken


def erzeuge_pairing_pin(db: Session, device_token: DeviceToken) -> str:
    """Erzeugt eine neue Pairing-PIN (10 min gültig) für ein DeviceToken und
    speichert nur den Hash. Gibt die Klartext-PIN zurück (nur einmal angezeigt)."""
    pin = generate_pairing_code()
    device_token.pairing_pin_hash = hash_api_key(pin)
    device_token.pairing_pin_expires_at = datetime.now(UTC) + timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    return pin


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def redeem_pairing_pin(db: Session, pin: str) -> tuple[DeviceToken, str, str | None] | None:
    """Löst eine Geräte-Pairing-PIN ein → rotiert das Device-Token auf ein frisches
    Geheimnis (Einmal-Gebrauch, wie beim QR-Scan) und gibt
    (device_token, raw_token, raw_gateway_token) zurück, oder None
    (unbekannt/abgelaufen/widerrufen).

    Ist das Gerät als kombiniertes "Geraet + SMS-Gateway" angelegt
    (paired_gateway_token_id gesetzt, siehe ui_admin.py), wird auch das
    verknüpfte SmsGatewayToken auf ein frisches Geheimnis rotiert und
    zurückgegeben — sonst kann die PIN-Alternative die Gateway-Rolle nie
    aktivieren, da (anders als der QR-Code) kein Gateway-Token kodiert ist.
    raw_gateway_token ist None, wenn kein Gateway gekoppelt ist.

    device_token ist nicht TenantScoped (analog zu /geraet-login in auth.py) —
    Zugriff erfolgt ausschließlich über den PIN-Hash, kein Org-Kontext nötig.
    """
    if not pin:
        return None
    pin_hash = hash_api_key(pin.strip().upper())
    now = datetime.now(UTC).replace(tzinfo=None)
    dt = (
        db.query(DeviceToken)
        .filter(DeviceToken.pairing_pin_hash == pin_hash, DeviceToken.revoked_at.is_(None))
        .first()
    )
    if dt is None:
        return None
    if dt.pairing_pin_expires_at is None or _naive_utc(dt.pairing_pin_expires_at) < now:
        return None

    raw_token = secrets.token_urlsafe(32)
    dt.token_hash = hash_api_key(raw_token)
    dt.pairing_pin_hash = None
    dt.pairing_pin_expires_at = None

    raw_gateway_token: str | None = None
    if dt.paired_gateway_token_id is not None:
        gw = db.get(SmsGatewayToken, dt.paired_gateway_token_id)
        if gw is not None and gw.revoked_at is None:
            raw_gateway_token = generate_sms_gateway_token()
            gw.token_hash = hash_api_key(raw_gateway_token)

    return dt, raw_token, raw_gateway_token
