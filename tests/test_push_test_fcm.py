"""Tests für den gerätegenauen FCM-Testversand."""
from fastapi.testclient import TestClient

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.user import DeviceToken, FcmToken, User


def _setup_user(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="FCM-Testnutzer",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def test_fcm_push_without_session_returns_401():
    response = TestClient(app).post("/push/test-fcm")

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "Nicht eingeloggt"}


def test_fcm_push_without_registered_token_returns_error():
    user_id = _setup_user("fcm_test_without_token")
    client = TestClient(app)
    client.cookies.set("session", sign_session(user_id))

    response = client.post("/push/test-fcm")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "Kein FCM-Token für dieses Gerät registriert",
    }


def test_fcm_push_sends_to_registered_token(monkeypatch):
    user_id = _setup_user("fcm_test_registered_token")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = FcmToken(user_id=user_id, token="fcm-test-send-token", platform="android")
        db.add(token)
        db.commit()
        token_id = token.id
    finally:
        db.close()

    sent = {}

    def fake_send_fcm(token_row, title, body, url=None):
        sent.update(token_id=token_row.id, title=title, body=body, url=url)
        return True

    monkeypatch.setattr("app.services.push_service.send_fcm", fake_send_fcm)
    client = TestClient(app)
    client.cookies.set("session", sign_session(user_id))

    response = client.post("/push/test-fcm")

    assert response.json() == {"ok": True}
    assert sent == {
        "token_id": token_id,
        "title": "Test-Push",
        "body": "Wenn du das siehst, funktioniert FCM!",
        "url": "/admin/push-nachrichten",
    }


def test_fcm_push_is_scoped_to_current_device():
    user_id = _setup_user("fcm_test_device_scope")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        registered = DeviceToken(
            user_id=user_id,
            token_hash=hash_api_key("fcm-test-registered-device"),
            label="Registriertes Gerät",
        )
        inactive = DeviceToken(
            user_id=user_id,
            token_hash=hash_api_key("fcm-test-inactive-device"),
            label="Inaktives Gerät",
        )
        db.add_all([registered, inactive])
        db.flush()
        db.add(FcmToken(
            user_id=user_id,
            device_token_id=registered.id,
            token="fcm-test-device-scoped-token",
            platform="android",
        ))
        db.commit()
        inactive_id = inactive.id
    finally:
        db.close()

    client = TestClient(app)
    client.cookies.set(
        "session",
        sign_session(user_id, device=True, device_token_id=inactive_id),
    )
    response = client.post("/push/test-fcm")

    assert response.json() == {
        "ok": False,
        "error": "Kein FCM-Token für dieses Gerät registriert",
    }
