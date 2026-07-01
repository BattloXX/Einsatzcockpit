"""Regressionstests für Härtungsplan-PR1 (Sofort-Quick-Wins).

- SEC-3: /qr-pin und /einsatz/{id}/pin-zugang Rate-Limit-Decorator-Reihenfolge
  (muss identisch zu /login sein, sonst greift slowapi nicht).
- SEC-2: DELETE /api/v1/device/fcm-token erfordert Login und darf nur Token
  des eigenen Users löschen.
"""
from app.core.rate_limit import limiter
from app.core.security import hash_password, hash_pin
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.user import FcmToken, Role, User, UserRole


def _make_user(db, username: str, password: str) -> User:
    role = db.query(Role).filter(Role.code == "recorder").first()
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        active=True,
    )
    db.add(user)
    db.flush()
    if role:
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str, password: str):
    client.get("/login")  # setzt ec_csrf-Cookie
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


# ── SEC-3: Rate-Limit-Decorator-Reihenfolge ──────────────────────────────────

def test_pin_zugang_rate_limit_enforced(client, setup_db):
    """>5 falsche PINs in 15 Minuten müssen 429 auslösen (SEC-3)."""
    limiter.reset()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        inc = Incident(alarm_type_code="T1", status="active", access_pin_hash=hash_pin("1234"))
        db.add(inc)
        db.commit()
        inc_id = inc.id
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    statuses = []
    for _ in range(7):
        r = client.post(
            f"/einsatz/{inc_id}/pin-zugang",
            data={"pin": "9999", "_csrf": csrf},
            follow_redirects=False,
        )
        statuses.append(r.status_code)
    assert 429 in statuses, f"Rate-Limit griff nicht, Status-Codes: {statuses}"


def test_qr_pin_rate_limit_enforced(client, setup_db):
    """>5 falsche PINs am QR-Einstieg müssen 429 auslösen (SEC-3)."""
    limiter.reset()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_user(db, "qrpintest", "Test1234!")
        inc = Incident(alarm_type_code="T1", status="active", access_pin_hash=hash_pin("1234"))
        db.add(inc)
        db.commit()
        inc_id = inc.id
        username = user.username
    finally:
        db.close()

    _login(client, username, "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    statuses = []
    for _ in range(7):
        r = client.post(
            "/qr-pin",
            data={"incident_id": inc_id, "pin": "9999", "_csrf": csrf},
            follow_redirects=False,
        )
        statuses.append(r.status_code)
    assert 429 in statuses, f"Rate-Limit griff nicht, Status-Codes: {statuses}"


# ── SEC-2: fcm-token DELETE Auth + Owner-Scope ───────────────────────────────

def test_fcm_token_delete_requires_auth(client, setup_db):
    r = client.request("DELETE", "/api/v1/device/fcm-token", json={"token": "irrelevant"})
    assert r.status_code == 401


def test_fcm_token_delete_only_deletes_own_token(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        owner = _make_user(db, "fcmowner", "Test1234!")
        attacker = _make_user(db, "fcmattacker", "Test1234!")
        token = FcmToken(user_id=owner.id, token="fcm-victim-token", platform="android")
        db.add(token)
        db.commit()
        attacker_username = attacker.username
    finally:
        db.close()

    _login(client, attacker_username, "Test1234!")
    r = client.request("DELETE", "/api/v1/device/fcm-token", json={"token": "fcm-victim-token"})
    assert r.status_code == 200

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        still_there = db.query(FcmToken).filter(FcmToken.token == "fcm-victim-token").first()
        assert still_there is not None, "Fremder User durfte Token eines anderen Users löschen"
    finally:
        db.close()
