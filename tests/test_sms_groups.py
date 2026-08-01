"""Regressionstests fuer die SMS-Gruppen-Verwaltung."""
from __future__ import annotations

import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _create_admin() -> str:
    kennung = uuid.uuid4().hex
    username = f"sms_groups_{kennung}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"sms-groups-{kennung}",
            name="SMS-Gruppen Testorganisation",
            color="#123456",
            bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        admin = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="SMS-Gruppen Test",
            org_id=org.id,
            active=True,
        )
        db.add(admin)
        db.flush()
        role = db.query(Role).filter(Role.code == "admin").one()
        db.add(UserRole(user_id=admin.id, role_id=role.id))
        db.commit()
        return username
    finally:
        db.close()


def test_create_group_with_empty_name_redirects_to_groups(client):
    username = _create_admin()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )

    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/admin/gruppen/neu",
        data={"name": "   ", "_csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/gruppen?error=empty"
