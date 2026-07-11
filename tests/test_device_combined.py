"""Tests fuer das kombinierte Geraet (Einheit-Geraet + SMS-Gateway auf einem Android-Geraet).

Nutzer-Feedback 2026-07-11: ein physisches Geraet soll gleichzeitig als Board-Geraet
(DeviceToken -> Session-Cookie via /geraet-login) UND als SMS-Gateway
(SmsGatewayToken -> Bearer-Token via /ws/sms-gateway) laufen koennen. Die Admin-Aktion
POST /admin/geraete-login/neu mit device_type=unit+sms-gateway erzeugt dafuer beide
Tokens in einem Schritt (garantiert gleiche Org) und einen kombinierten QR-Code.
"""
from __future__ import annotations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, Role, SmsGatewayToken, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _make_admin(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        admin = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Admin", org_id=ORG_ID, active=True)
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=_rolle(db, "admin").id))
        db.commit()
    finally:
        db.close()


def test_kombiniertes_geraet_erzeugt_beide_tokens_gleiche_org(client):
    _make_admin("combo_admin_neu")
    _login(client, "combo_admin_neu", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/admin/geraete-login/neu", data={
        "label": "Fahrzeug-Tablet Kombi", "device_type": "unit+sms-gateway", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 200
    # Beide Roh-Tokens werden auf der Reveal-Seite angezeigt.
    assert "mode=unit+sms-gateway" in r.text
    assert "gateway_token=" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = (
            db.query(DeviceToken)
            .join(User, DeviceToken.user_id == User.id)
            .filter(DeviceToken.label == "Fahrzeug-Tablet Kombi")
            .first()
        )
        gw = db.query(SmsGatewayToken).filter(SmsGatewayToken.label == "Fahrzeug-Tablet Kombi").first()

        assert dt is not None
        assert gw is not None
        # Pairing-PIN wird wie beim reinen Einheit-Geraet mit angelegt.
        assert dt.pairing_pin_hash is not None

        device_user = db.get(User, dt.user_id)
        assert device_user.org_id == ORG_ID
        assert gw.org_id == ORG_ID
        assert gw.org_id == device_user.org_id
    finally:
        db.close()


def test_sms_gateway_only_erzeugt_keinen_device_token(client):
    _make_admin("combo_admin_gwonly")
    _login(client, "combo_admin_gwonly", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/admin/geraete-login/neu", data={
        "label": "Nur-Gateway", "device_type": "sms-gateway", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "gateway_token=" not in r.text  # kein kombinierter QR bei reinem Gateway

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.query(SmsGatewayToken).filter(SmsGatewayToken.label == "Nur-Gateway").first()
        dt = db.query(DeviceToken).filter(DeviceToken.label == "Nur-Gateway").first()
        assert gw is not None
        assert dt is None
    finally:
        db.close()


def test_einheit_geraet_only_hat_weiterhin_keinen_gateway_token(client):
    _make_admin("combo_admin_devonly")
    _login(client, "combo_admin_devonly", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/admin/geraete-login/neu", data={
        "label": "Nur-Geraet", "device_type": "unit", "_csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "gateway_token=" not in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.query(DeviceToken).filter(DeviceToken.label == "Nur-Geraet").first()
        gw = db.query(SmsGatewayToken).filter(SmsGatewayToken.label == "Nur-Geraet").first()
        assert dt is not None
        assert gw is None
    finally:
        db.close()
