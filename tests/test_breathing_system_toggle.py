"""Regression test fuer den systemweiten Atemschutzueberwachung-Toggle.

Bisher gab es fuer Atemschutzueberwachung anders als fuer UAS/Objekt/Gateway/
Nachschlagewerke/Foerderstrecke/Lagefuehrung keinen eigenen System-Admin-Toggle
in admin/settings.html - nur eine generische Einstellung in system_settings.html.
"""
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.master import SystemSettings
from app.models.user import Role, User, UserRole
from app.services.breathing_service import breathing_system_enabled


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
            display_name="Breathing Toggle Test Admin",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )


def test_breathing_system_toggle_only_system_admin(request):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        original = db.get(SystemSettings, "breathing_enabled")
        original_value = original.value if original else None
    finally:
        db.close()

    def restore():
        restore_db = SessionLocal()
        set_tenant_context(restore_db, None)
        try:
            row = restore_db.get(SystemSettings, "breathing_enabled")
            if original_value is None:
                if row is not None:
                    restore_db.delete(row)
            elif row is not None:
                row.value = original_value
            else:
                restore_db.add(SystemSettings(key="breathing_enabled", value=original_value))
            restore_db.commit()
        finally:
            restore_db.close()

    request.addfinalizer(restore)

    _setup_system_admin("breathing_toggle_sysadmin")
    client = TestClient(app)
    assert _login(client, "breathing_toggle_sysadmin").status_code == 302

    settings_page = client.get("/admin/settings")
    assert settings_page.status_code == 200
    assert "Atemschutzüberwachung" in settings_page.text
    assert '/admin/settings/system/breathing-toggle' in settings_page.text

    response = client.post(
        "/admin/settings/system/breathing-toggle",
        data={"_csrf": client.cookies.get("ec_csrf"), "enabled_raw": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert breathing_system_enabled(db) is False
    finally:
        db.close()

    response = client.post(
        "/admin/settings/system/breathing-toggle",
        data={"_csrf": client.cookies.get("ec_csrf"), "enabled_raw": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert breathing_system_enabled(db) is True
    finally:
        db.close()


def test_breathing_system_toggle_rejects_non_system_admin(request):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username="breathing_toggle_org_admin",
            password_hash=hash_password("Test1234!"),
            display_name="Org Admin",
            org_id=1,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "org_admin").first()
        if role is not None:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    assert _login(client, "breathing_toggle_org_admin").status_code == 302

    response = client.post(
        "/admin/settings/system/breathing-toggle",
        data={"_csrf": client.cookies.get("ec_csrf"), "enabled_raw": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 403
