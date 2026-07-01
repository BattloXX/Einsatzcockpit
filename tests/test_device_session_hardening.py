"""Regressionstests PR12:
- SEC-5: Widerrufene Device-Sessions duerfen nach Revocation nicht mehr
  authentifizieren (Session-Cookie speichert selbst keinen Widerrufsstatus).
- SEC-8: /api/v1/device/* ist cookie-authentifiziert, aber unter dem
  CSRF-Exempt-Praefix /api/v1/ -- Origin-Check als Ersatzschutz."""
from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceToken, Role, User, UserRole


def _make_device_user(db, username: str) -> User:
    role = db.query(Role).filter(Role.code == "readonly").first()
    user = User(username=username, password_hash=hash_password("Test1234!"),
                display_name=username, active=True, is_device=True)
    db.add(user)
    db.flush()
    if role:
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    db.refresh(user)
    return user


# ── SEC-5: Device-Session-Widerruf ───────────────────────────────────────────

def test_device_session_rejected_without_any_device_token(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec5user1")
        user_id = user.id
    finally:
        db.close()

    token = sign_session(user_id, device=True)
    client.cookies.set("session", token)
    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    # Kein DeviceToken vorhanden -> Session darf NICHT authentifizieren.
    assert r.status_code in (302, 401, 403)
    if r.status_code == 302:
        assert "/login" in r.headers.get("location", "")


def test_device_session_valid_with_active_device_token(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec5user2")
        db.add(DeviceToken(user_id=user.id, token_hash="hash-active", label="Test-Geraet"))
        db.commit()
        user_id = user.id
    finally:
        db.close()

    token = sign_session(user_id, device=True)
    client.cookies.set("session", token)
    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert r.status_code not in (401,)
    # Nicht auf /login umgeleitet -> Session wurde akzeptiert.
    if r.status_code == 302:
        assert "/login" not in r.headers.get("location", "")


def test_device_session_rejected_after_revocation(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec5user3")
        dt = DeviceToken(user_id=user.id, token_hash="hash-to-revoke", label="Verlorenes Geraet")
        db.add(dt)
        db.commit()
        user_id = user.id
        dt_id = dt.id
    finally:
        db.close()

    token = sign_session(user_id, device=True)
    client.cookies.set("session", token)
    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert r.status_code not in (401,)

    # Geraet wird widerrufen (z. B. verloren gemeldet) ...
    from datetime import UTC, datetime
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, dt_id)
        dt.revoked_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()

    # ... dasselbe (noch gueltig signierte) Cookie darf jetzt nicht mehr wirken.
    r = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert r.status_code in (302, 401, 403)
    if r.status_code == 302:
        assert "/login" in r.headers.get("location", "")


# ── SEC-8: Origin-Check fuer /api/v1/device/* ────────────────────────────────

def test_device_endpoint_rejects_foreign_origin(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec8user1")
        user_id = user.id
    finally:
        db.close()

    client.cookies.set("session", sign_session(user_id, device=False))
    r = client.post(
        "/api/v1/device/duty",
        json={"active": True},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_device_endpoint_allows_matching_origin(client, setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec8user2")
        user_id = user.id
    finally:
        db.close()

    client.cookies.set("session", sign_session(user_id, device=False))
    r = client.post(
        "/api/v1/device/duty",
        json={"active": True},
        headers={"Origin": "http://localhost:8092"},
    )
    assert r.status_code != 403


def test_device_endpoint_allows_missing_origin_native_app(client, setup_db):
    """Native App sendet i. d. R. keinen Origin-Header — darf nicht blockiert werden."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _make_device_user(db, "devicesec8user3")
        user_id = user.id
    finally:
        db.close()

    client.cookies.set("session", sign_session(user_id, device=False))
    r = client.post("/api/v1/device/duty", json={"active": True})
    assert r.status_code != 403
