"""Regressionstests fuer den systemweiten Probenplanung-Schalter."""

import re

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def test_probenplanung_system_toggle_enthaelt_org_id(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        role = db.query(Role).filter(Role.code == "system_admin").one()
        user = User(
            username="probenplanung_toggle_sysadmin",
            password_hash=hash_password("Test1234!"),
            display_name="Probenplanung Toggle Test",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        org_id = org.id
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    login = client.post(
        "/login",
        data={
            "username": "probenplanung_toggle_sysadmin",
            "password": "Test1234!",
            "_csrf": csrf,
        },
        follow_redirects=False,
    )
    assert login.status_code == 302

    response = client.get(f"/admin/settings?org_id={org_id}")
    assert response.status_code == 200
    form = re.search(
        r'<form[^>]+action="/admin/settings/system/probenplanung-toggle"[^>]*>(.*?)</form>',
        response.text,
        re.DOTALL,
    )
    assert form is not None
    assert f'<input type="hidden" name="org_id" value="{org_id}">' in form.group(1)
