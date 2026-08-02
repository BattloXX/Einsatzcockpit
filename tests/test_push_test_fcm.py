"""Tests für den gerätegenauen FCM-Testversand."""
import sys
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.master import SystemSettings
from app.models.user import DeviceToken, FcmToken, User
from app.services import push_service


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

    def fake_send_fcm(token_row, title, body, url=None, cfg=None):
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


def test_fcm_push_uses_database_settings_when_environment_is_empty(monkeypatch):
    user_id = _setup_user("fcm_test_database_settings")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(FcmToken(user_id=user_id, token="fcm-db-settings-token", platform="android"))
        for key, value in {
            "fcm_enabled": "true",
            "fcm_project_id": "database-project",
            "fcm_credentials_path": "/database/firebase-credentials.json",
        }.items():
            row = db.query(SystemSettings).filter_by(key=key).first()
            if row:
                row.value = value
            else:
                db.add(SystemSettings(key=key, value=value))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(push_service.settings, "FCM_ENABLED", False)
    monkeypatch.setattr(push_service.settings, "FCM_PROJECT_ID", "")
    monkeypatch.setattr(push_service.settings, "FCM_CREDENTIALS_PATH", "")

    sent = []
    fake_app = object()
    firebase_admin = ModuleType("firebase_admin")
    credentials = ModuleType("firebase_admin.credentials")
    messaging = ModuleType("firebase_admin.messaging")
    credentials.Certificate = lambda path: ("certificate", path)
    firebase_admin.credentials = credentials
    firebase_admin.messaging = messaging
    firebase_admin._apps = []
    firebase_admin.initialize_app = lambda cred, options: fake_app
    firebase_admin.get_app = lambda: fake_app
    messaging.Notification = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.AndroidConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.Message = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.send = lambda message: sent.append(message)
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", credentials)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", messaging)
    monkeypatch.setattr(push_service, "_fcm_app", None)
    monkeypatch.setattr(push_service, "_fcm_unavailable", False)
    monkeypatch.setattr(push_service, "_fcm_unconfigured_warned", False)

    client = TestClient(app)
    client.cookies.set("session", sign_session(user_id))
    response = client.post("/push/test-fcm")

    assert response.json() == {"ok": True}
    assert len(sent) == 1
    assert sent[0].token == "fcm-db-settings-token"


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
