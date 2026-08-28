"""Regressionstests fuer den Systemstatus-Eintrag in der Admin-Navigation."""

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_org_admin(username: str, org_slug: str, role_code: str = "admin") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#112233", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Admin",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_org_admin_sieht_systemstatus_nav_eintrag(client, setup_db):
    _make_org_admin("systemstatus_org_admin", "systemstatus-org")
    _login(client, "systemstatus_org_admin", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert 'href="/admin/systemstatus"' in response.text
    assert "Systemstatus" in response.text


def test_system_admin_sieht_systemstatus_nav_eintrag_genau_einmal(client, setup_db):
    _make_org_admin("systemstatus_system_admin", "systemstatus-system", "system_admin")
    _login(client, "systemstatus_system_admin", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert response.text.count('href="/admin/systemstatus"') == 1
