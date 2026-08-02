"""Regression test for the admin-save to FCM-config read round trip."""
import sys
from types import ModuleType

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.user import Role, User, UserRole
from app.services import push_service


def _setup_system_admin(username: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "system_admin").first()
        if role is None:
            role = Role(code="system_admin", name="system_admin")
            db.add(role)
            db.flush()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="FCM Settings Test Admin",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def test_fcm_settings_save_then_fresh_session_read(monkeypatch):
    _setup_system_admin("fcm_settings_roundtrip_admin")
    client = TestClient(app)
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    login = client.post(
        "/login",
        data={
            "username": "fcm_settings_roundtrip_admin",
            "password": "Test1234!",
            "_csrf": csrf,
        },
        follow_redirects=False,
    )
    assert login.status_code == 302

    response = client.post(
        "/admin/system-einstellungen",
        data={
            "_csrf": client.cookies.get("ec_csrf"),
            "k_fcm_enabled": "true",
            "k_fcm_project_id": "einsatzleiter-cloud",
            "k_fcm_credentials_path": "/etc/einsatzleiter/fcm-service-account.json",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/system-einstellungen?saved=1"

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = push_service._push_cfg(db)
    finally:
        db.close()

    assert cfg["fcm_enabled"] is True
    assert cfg["fcm_project_id"] == "einsatzleiter-cloud"
    assert cfg["fcm_credentials_path"] == "/etc/einsatzleiter/fcm-service-account.json"

    fake_app = object()
    firebase_admin = ModuleType("firebase_admin")
    credentials = ModuleType("firebase_admin.credentials")
    credentials.Certificate = lambda path: ("certificate", path)
    firebase_admin.credentials = credentials
    firebase_admin._apps = []
    firebase_admin.initialize_app = lambda cred, options: fake_app
    firebase_admin.get_app = lambda: fake_app
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", credentials)
    monkeypatch.setattr(push_service, "_fcm_app", None)
    monkeypatch.setattr(push_service, "_fcm_unavailable", False)

    assert push_service._get_fcm_app(cfg) is fake_app
