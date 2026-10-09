"""Regression tests for administrator-to-user privilege boundaries."""
from __future__ import annotations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import AuditLog, Role, User, UserRole


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _session():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _role(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, label=code)
        db.add(role)
        db.flush()
    return role


def _user(db, username, org_id, *roles):
    user = User(username=username, display_name=username, password_hash=hash_password("Test1234!"),
                org_id=org_id, active=True)
    db.add(user)
    db.flush()
    for code in roles:
        db.add(UserRole(user_id=user.id, role_id=_role(db, code).id))
    return user


def test_org_admin_cannot_mutate_system_admin_and_attempts_are_audited(client):
    db = _session()
    try:
        org = db.query(FireDept).first()
        assert org is not None
        actor = _user(db, "role_guard_org_admin", org.id, "admin")
        target = _user(db, "role_guard_system_admin", org.id, "system_admin")
        db.commit()
        target_id = target.id
    finally:
        db.close()

    assert _login(client, "role_guard_org_admin").status_code == 302
    csrf = client.cookies.get("ec_csrf")
    for path, data in (
        (f"/admin/benutzer/{target_id}/edit", {"display_name": "changed", "_csrf": csrf}),
        (f"/admin/benutzer/{target_id}/reset-mail", {"_csrf": csrf}),
        (f"/admin/benutzer/{target_id}/passwort", {"_csrf": csrf}),
        (f"/admin/benutzer/{target_id}/loeschen", {"_csrf": csrf}),
        (f"/admin/benutzer/{target_id}/endgueltig-loeschen", {"_csrf": csrf}),
        (f"/admin/benutzer/{target_id}/rollen", {"role_codes": "admin", "_csrf": csrf}),
    ):
        assert client.post(path, data=data, follow_redirects=False).status_code == 403

    page = client.get("/admin/benutzer")
    assert "Nur Systemadministrator" in page.text
    assert f'data-user-id="{target_id}"' not in page.text

    db = _session()
    try:
        target = db.get(User, target_id)
        assert target is not None and target.active
        assert {role.code for role in target.roles} == {"system_admin"}
        denied = db.query(AuditLog).filter(
            AuditLog.action == "admin.user.mutation_denied", AuditLog.entity_id == target_id,
        ).count()
        assert denied == 6
    finally:
        db.close()


def test_org_admin_cannot_grant_system_admin_and_form_preserves_roles(client):
    db = _session()
    try:
        org = db.query(FireDept).first()
        assert org is not None
        _user(db, "role_guard_grant_admin", org.id, "admin")
        target = _user(db, "role_guard_grant_target", org.id, "readonly")
        db.commit()
        target_id = target.id
    finally:
        db.close()

    _login(client, "role_guard_grant_admin")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(f"/admin/benutzer/{target_id}/edit", data={
        "display_name": "unchanged", "role_codes": ["system_admin", "admin"], "_csrf": csrf,
    }, follow_redirects=False)
    assert response.status_code == 303

    db = _session()
    try:
        target = db.get(User, target_id)
        assert target is not None
        assert {role.code for role in target.roles} == {"admin"}
        assert db.query(AuditLog).filter(
            AuditLog.action == "admin.user.role_change_denied", AuditLog.entity_id == target_id,
        ).count() == 1
    finally:
        db.close()


def test_system_admin_can_grant_and_revoke_system_admin(client):
    db = _session()
    try:
        org = db.query(FireDept).first()
        assert org is not None
        _user(db, "role_guard_system_actor", org.id, "system_admin")
        target = _user(db, "role_guard_system_target", org.id, "readonly")
        db.commit()
        target_id = target.id
    finally:
        db.close()

    _login(client, "role_guard_system_actor")
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"/admin/benutzer/{target_id}/rollen", data={
        "role_codes": "system_admin", "_csrf": csrf,
    }, follow_redirects=False).status_code == 303
    assert client.post(f"/admin/benutzer/{target_id}/rollen", data={
        "role_codes": "readonly", "_csrf": csrf,
    }, follow_redirects=False).status_code == 303

    db = _session()
    try:
        target = db.get(User, target_id)
        assert target is not None
        assert {role.code for role in target.roles} == {"readonly"}
    finally:
        db.close()
