"""Tests fuer die neuen passwortlosen Anmeldewege (Nutzer-Feedback 2026-07-11):
- SMS-PIN-Login fuer persoenliche Accounts (/pin-login, /pin-login/code)
- Geraete-Pairing per PIN statt QR-Code-Scan (/geraet-login-pin)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.login_pin import LoginPin
from app.models.user import DeviceToken, Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _csrf(client) -> str:
    """Holt einen frischen CSRF-Cookie (ohne eingeloggten Kontext) fuer anonyme POSTs."""
    client.get("/login")
    return client.cookies.get("ec_csrf") or ""


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _make_user(username, *, phone=None, org_id=ORG_ID, active=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org_id, active=active, phone=phone)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "readonly").id))
        db.commit()
        return user.id
    finally:
        db.close()


# ── SMS-PIN-Login: Anfordern (/pin-login) ────────────────────────────────────

def test_pin_login_unbekannte_nummer_neutral_redirect(client):
    csrf = _csrf(client)
    r = client.post("/pin-login", data={"phone": "+436601112233", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert "/pin-login/code" in r.headers["location"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LoginPin).count() == 0
    finally:
        db.close()


def test_pin_login_ohne_gateway_erzeugt_keine_pin(client):
    _make_user("pinlogin_nogw", phone="+436601110001")
    csrf = _csrf(client)
    with patch("app.routers.ws.is_sms_gateway_connected", return_value=False):
        r = client.post("/pin-login", data={"phone": "+436601110001", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LoginPin).count() == 0
    finally:
        db.close()


def test_pin_login_versendet_sms_und_erzeugt_pin(client):
    _make_user("pinlogin_happy", phone="+436601110002")
    csrf = _csrf(client)
    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_service.send_sms", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        r = client.post("/pin-login", data={"phone": "+436601110002", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    mock_send.assert_awaited_once()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pins = db.query(LoginPin).all()
        assert len(pins) == 1
        assert pins[0].used_at is None
    finally:
        db.close()


# ── SMS-PIN-Login: Bestaetigen (/pin-login/code) ─────────────────────────────

def _erzeuge_pin_fuer(user_id: int, pin: str, *, minutes_left: int = 10) -> None:
    import hashlib
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(LoginPin(
            user_id=user_id,
            pin_hash=hashlib.sha256(pin.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(minutes=minutes_left),
        ))
        db.commit()
    finally:
        db.close()


def test_pin_login_code_erfolgreich_loggt_ein(client):
    uid = _make_user("pinlogin_code_ok", phone="+436601110003")
    _erzeuge_pin_fuer(uid, "123456")
    csrf = _csrf(client)

    r = client.post("/pin-login/code", data={
        "phone": "+436601110003", "pin": "123456", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert "session" in r.cookies

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pin_row = db.query(LoginPin).filter(LoginPin.user_id == uid).first()
        assert pin_row.used_at is not None
    finally:
        db.close()


def test_pin_login_code_falscher_pin_erhoeht_attempt_count(client):
    uid = _make_user("pinlogin_code_falsch", phone="+436601110004")
    _erzeuge_pin_fuer(uid, "654321")
    csrf = _csrf(client)

    r = client.post("/pin-login/code", data={
        "phone": "+436601110004", "pin": "000000", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 401
    assert "session" not in r.cookies

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pin_row = db.query(LoginPin).filter(LoginPin.user_id == uid).first()
        assert pin_row.attempt_count == 1
        assert pin_row.used_at is None
    finally:
        db.close()


def test_pin_login_code_abgelaufene_pin_wird_abgelehnt(client):
    uid = _make_user("pinlogin_code_expired", phone="+436601110005")
    _erzeuge_pin_fuer(uid, "111222", minutes_left=-1)
    csrf = _csrf(client)

    r = client.post("/pin-login/code", data={
        "phone": "+436601110005", "pin": "111222", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 401


def test_pin_login_code_bereits_verwendete_pin_wird_abgelehnt(client):
    uid = _make_user("pinlogin_code_reuse", phone="+436601110006")
    _erzeuge_pin_fuer(uid, "999888")
    csrf = _csrf(client)
    r1 = client.post("/pin-login/code", data={
        "phone": "+436601110006", "pin": "999888", "_csrf": csrf,
    }, follow_redirects=False)
    assert r1.status_code == 302

    client.cookies.clear()
    csrf2 = _csrf(client)
    r2 = client.post("/pin-login/code", data={
        "phone": "+436601110006", "pin": "999888", "_csrf": csrf2,
    }, follow_redirects=False)
    assert r2.status_code == 401


# ── Geraete-Pairing-PIN (/geraet-login-pin) ──────────────────────────────────

def _make_device_token(*, pin: str | None = None, minutes_left: int = 10) -> tuple[int, int]:
    unique = uuid.uuid4().hex[:12]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=f"geraet_pin_{unique}", display_name="Geraet-PIN-Test",
                    password_hash=hash_password("irrelevant-Aa1!"), org_id=ORG_ID,
                    active=True, is_device=True)
        db.add(user)
        db.flush()
        dt = DeviceToken(label="Test-Geraet-PIN", token_hash=hash_api_key(f"initial-token-{unique}"),
                         user_id=user.id)
        if pin:
            dt.pairing_pin_hash = hash_api_key(pin.strip().upper())
            dt.pairing_pin_expires_at = datetime.now(UTC) + timedelta(minutes=minutes_left)
        db.add(dt)
        db.commit()
        return dt.id, user.id
    finally:
        db.close()


def test_geraet_login_pin_erfolgreich_setzt_session_und_rotiert_token(client):
    dt_id, user_id = _make_device_token(pin="ABCD1234")
    csrf = _csrf(client)
    alter_hash = hash_api_key("initial-token")  # nur zur Kontrastprobe, siehe DB-Check unten

    r = client.post("/geraet-login-pin", data={"pin": "abcd1234", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 200  # rendert Bestaetigungsseite direkt (kein Redirect)
    assert "session" in r.cookies
    assert "el_device_token" in r.text  # Confirmation-Page speichert den neuen Token per JS

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, dt_id)
        assert dt.pairing_pin_hash is None
        assert dt.pairing_pin_expires_at is None
        assert dt.token_hash != alter_hash  # rotiert auf ein frisches Geheimnis
        assert dt.last_used_at is not None
    finally:
        db.close()


def test_geraet_login_pin_falsche_pin_wird_abgelehnt(client):
    _make_device_token(pin="WXYZ7777")
    csrf = _csrf(client)

    r = client.post("/geraet-login-pin", data={"pin": "FALSCH99", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 401
    assert "session" not in r.cookies


def test_geraet_login_pin_abgelaufene_pin_wird_abgelehnt(client):
    _make_device_token(pin="OLD00000", minutes_left=-5)
    csrf = _csrf(client)

    r = client.post("/geraet-login-pin", data={"pin": "OLD00000", "_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 401


def test_geraet_login_pin_einmal_verwendbar(client):
    _make_device_token(pin="ONCE1111")
    csrf = _csrf(client)

    r1 = client.post("/geraet-login-pin", data={"pin": "ONCE1111", "_csrf": csrf}, follow_redirects=False)
    assert r1.status_code == 200

    client.cookies.clear()
    csrf2 = _csrf(client)
    r2 = client.post("/geraet-login-pin", data={"pin": "ONCE1111", "_csrf": csrf2}, follow_redirects=False)
    assert r2.status_code == 401


# ── Admin-UI: Pairing-PIN bei Geraete-Erstellung ─────────────────────────────

def test_admin_geraet_erstellen_zeigt_pairing_pin(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        admin = User(username="pin_admin_neu", password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=ORG_ID, active=True)
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=_rolle(db, "admin").id))
        db.commit()
    finally:
        db.close()

    _login(client, "pin_admin_neu", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/geraete-login/neu", data={
        "label": "Neues Test-Geraet", "device_type": "unit", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "PIN statt Scannen" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.query(DeviceToken).filter(DeviceToken.label == "Neues Test-Geraet").first()
        assert dt is not None
        assert dt.pairing_pin_hash is not None
        assert dt.pairing_pin_expires_at is not None
    finally:
        db.close()


def test_admin_geraet_pin_neu_generieren(client):
    dt_id, _ = _make_device_token(pin="OLDPIN01")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        admin = User(username="pin_admin_regen", password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=ORG_ID, active=True)
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=_rolle(db, "admin").id))
        db.commit()
        alte_pin_hash = db.get(DeviceToken, dt_id).pairing_pin_hash
    finally:
        db.close()

    _login(client, "pin_admin_regen", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/admin/geraete-login/{dt_id}/pin-neu", data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 200
    assert "Neue PIN" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, dt_id)
        assert dt.pairing_pin_hash is not None
        assert dt.pairing_pin_hash != alte_pin_hash
    finally:
        db.close()
