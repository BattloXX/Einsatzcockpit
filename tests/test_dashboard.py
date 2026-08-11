import uuid

from app.core.security import hash_password
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole


SYSTEM_MODULE_FLAGS = (
    "uas_module_enabled",
    "objekt_module_enabled",
    "nachschlagewerke_module_enabled",
    "foerderstrecke_module_enabled",
)


def _setup_dashboard_user(*, modules_enabled: bool, active_incident: bool) -> str:
    suffix = uuid.uuid4().hex[:10]
    username = f"dashboard_{suffix}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"dashboard-{suffix}", name="Dashboard Test")
        db.add(org)
        db.flush()

        org_settings = OrgSettings(
            org_id=org.id,
            uas_module_enabled=modules_enabled,
            objekt_module_enabled=modules_enabled,
            nachschlagewerke_module_enabled=modules_enabled,
            foerderstrecke_module_enabled=modules_enabled,
            fahrtenbuch_modul_aktiv=modules_enabled,
            atemschutz_pruefung_modul_aktiv=modules_enabled,
        )
        db.add(org_settings)

        if modules_enabled:
            for key in SYSTEM_MODULE_FLAGS:
                row = db.get(SystemSettings, key)
                if row is None:
                    db.add(SystemSettings(key=key, value="true"))
                else:
                    row.value = "true"

        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Dashboard Test",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "org_admin").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))

        if active_incident:
            db.add(Incident(
                primary_org_id=org.id,
                alarm_type_code="T1",
                status="active",
                address_street="Teststraße",
                address_no="1",
                address_city="Wolfurt",
            ))

        db.commit()
        return username
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_dashboard_zeigt_standby_und_blendet_deaktivierte_module_aus(client, monkeypatch):
    username = _setup_dashboard_user(modules_enabled=False, active_incident=False)
    monkeypatch.setitem(templates.env.globals, "WEATHER_ENABLED", False)
    _login(client, username)

    response = client.get("/")

    assert response.status_code == 200
    assert 'class="incident-standby"' in response.text
    assert 'class="empty-state"' not in response.text
    assert 'class="module-grid"' in response.text
    for label in ("Lage", "Teilnahme", "Statistik", "Archiv"):
        assert f'<span class="module-btn__label">{label}</span>' in response.text
    for label in (
        "Fahrtenbuch", "Objekte", "Atemschutzprüfung", "Wetter", "Drohne / UAS",
        "Nachschlagewerke", "Förderstrecke",
    ):
        assert f'<span class="module-btn__label">{label}</span>' not in response.text


def test_dashboard_zeigt_aktiven_einsatz_und_aktivierte_module(client, monkeypatch):
    username = _setup_dashboard_user(modules_enabled=True, active_incident=True)
    monkeypatch.setitem(templates.env.globals, "WEATHER_ENABLED", True)
    _login(client, username)

    response = client.get("/")

    assert response.status_code == 200
    assert "Aktive Einsätze (1)" in response.text
    assert 'class="incident-card "' in response.text
    assert 'class="incident-standby"' not in response.text
    for label in (
        "Fahrtenbuch", "Objekte", "Atemschutzprüfung", "Wetter", "Drohne / UAS",
        "Nachschlagewerke", "Förderstrecke",
    ):
        assert f'<span class="module-btn__label">{label}</span>' in response.text
