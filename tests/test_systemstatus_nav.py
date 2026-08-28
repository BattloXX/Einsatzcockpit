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


def test_systemstatus_zeigt_korrekte_dienstlabels(client, setup_db):
    _make_org_admin("systemstatus_labels", "systemstatus-labels")
    _login(client, "systemstatus_labels", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "SMS-Gateway" in response.text
    assert "Alarm DIBOS" in response.text
    assert "Sms Gateway" not in response.text
    assert "Alarm Dibos" not in response.text


def test_systemstatus_zeigt_nicht_eingerichtete_dienste(client, setup_db):
    _make_org_admin("systemstatus_fresh", "systemstatus-fresh")
    _login(client, "systemstatus_fresh", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "Nicht eingerichtet" in response.text


def test_systemstatus_zeigt_uptime_referenz_ohne_token(client, setup_db):
    _make_org_admin("systemstatus_reference", "systemstatus-reference")
    _login(client, "systemstatus_reference", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "health/dienste" in response.text
    assert "&lt;TOKEN&gt;" in response.text


def test_systemstatus_zeigt_fertige_urls_nach_token_anlage(client, setup_db):
    _make_org_admin("systemstatus_token", "systemstatus-token")
    _login(client, "systemstatus_token", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    response = client.post(
        "/admin/systemstatus/token/neu",
        data={"_csrf": csrf, "label": "Uptime Kuma"},
    )

    assert response.status_code == 200
    assert "health/dienste?token=" in response.text
    for key in ("print_gateway", "sms_gateway", "alarm_seriell", "alarm_dibos"):
        assert f"health/dienst/{key}?token=" in response.text
