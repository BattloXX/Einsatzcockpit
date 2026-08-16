"""Integrationstest fuer die gemeinsame Navigation der Einsatz-Unterseiten."""
import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.uas import UASEinsatz
from app.models.user import Role, User, UserRole


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _setup():
    username = f"subnav_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=username, name="Subnav Test", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        settings = OrgSettings(
            org_id=org.id,
            atemschutz_ueberwachung_modul_aktiv=True,
            lagefuehrung_modul_aktiv=True,
        )
        db.add(settings)
        system_flag = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if system_flag is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            system_flag.value = "true"
        breathing_flag = db.get(SystemSettings, "breathing_enabled")
        if breathing_flag is not None:
            breathing_flag.value = "true"

        role = db.query(Role).filter(Role.code == "incident_leader").first()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Subnav Test",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
        db.add(incident)
        db.flush()
        uas = UASEinsatz(org_id=org.id, incident_id=incident.id)
        db.add(uas)
        db.commit()
        return username, org.id, incident.id, uas.id
    finally:
        db.close()


def test_subnav_auf_allen_einsatzseiten_und_feature_gating(client):
    username, org_id, incident_id, uas_id = _setup()
    assert _login(client, username, "Test1234!").status_code in (302, 303)

    routes = {
        "board": f"/einsatz/{incident_id}",
        "info": f"/einsatz/{incident_id}/info",
        "atemschutz": f"/einsatz/{incident_id}/atemschutz",
        "lagefuehrung": f"/einsatz/{incident_id}/lagefuehrung",
        "funk": f"/einsatz/{incident_id}/funkjournal",
        "mannschaft": f"/einsatz/{incident_id}/mannschaft",
    }
    for active, route in routes.items():
        response = client.get(route)
        assert response.status_code == 200, (route, response.text[:500])
        for target in routes.values():
            assert f'href="{target}"' in response.text
        assert f'aria-current="page"' in response.text
        if active in {"board", "funk", "mannschaft"}:
            assert f'href="/uas/einsatz/{uas_id}"' in response.text
        else:
            assert f'href="/uas/einsatz/{uas_id}"' not in response.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one()
        org_settings.atemschutz_ueberwachung_modul_aktiv = False
        org_settings.lagefuehrung_modul_aktiv = False
        uas = db.get(UASEinsatz, uas_id)
        db.delete(uas)
        db.commit()
    finally:
        db.close()

    for route in (routes["board"], routes["info"], routes["funk"], routes["mannschaft"]):
        response = client.get(route)
        assert response.status_code == 200
        assert f'href="{routes["atemschutz"]}"' not in response.text
        assert f'href="{routes["lagefuehrung"]}"' not in response.text
        assert f'href="/uas/einsatz/{uas_id}"' not in response.text
