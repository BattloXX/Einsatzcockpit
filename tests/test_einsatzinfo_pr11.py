"""Nutzer-Feedback 2026-07-11: (1) Google-Maps-Link auf der Einsatzinfo-Seite,
(2) der per SMS/Teams verschickte /alarm/{token}-Link soll bei eingeloggtem,
berechtigtem User direkt auf die Einsatzinfo weiterleiten statt die oeffentliche
No-Login-Seite zu zeigen, (3) Objekt-Vorschlaege zum manuellen Verknuepfen nach
Entfernung zum Einsatzort sortiert statt nach Objekt-Nr./ID.
"""
import hashlib

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
from app.models.teams_bot import AlarmToken
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_incident(username, *, lat=47.4652, lng=9.7503, with_token=False, objekt_module=False):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Info11 Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        if objekt_module:
            obj_sys_row = db.get(SystemSettings, "objekt_module_enabled")
            if obj_sys_row is None:
                db.add(SystemSettings(key="objekt_module_enabled", value="true"))
            else:
                obj_sys_row.value = "true"
            os_row = db.query(OrgSettings).filter_by(org_id=ORG_ID).first()
            if os_row is None:
                os_row = OrgSettings(org_id=ORG_ID)
                db.add(os_row)
            os_row.objekt_module_enabled = True

        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active",
                             lat=lat, lng=lng)
        db.add(incident)
        db.flush()
        token_plain = None
        if with_token:
            token_plain = f"tok-{incident.id}-secret"
            db.add(AlarmToken(incident_id=incident.id,
                               token_hash=hashlib.sha256(token_plain.encode()).hexdigest()))
        db.commit()
        return user.id, incident.id, token_plain
    finally:
        db.close()


# ── Google-Maps-Link auf der Einsatzinfo-Seite ───────────────────────────────

def test_einsatzinfo_zeigt_google_maps_link_wenn_koordinaten_vorhanden():
    _, incident_id, _ = _setup_incident("info11_gmaps_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "info11_gmaps_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/info")
    assert r.status_code == 200, r.text[:300]
    assert "maps.google.com/?q=47.4652,9.7503" in r.text
    assert "Google Maps" in r.text


def test_einsatzinfo_ohne_koordinaten_kein_google_maps_link():
    _, incident_id, _ = _setup_incident("info11_nogmaps_user", lat=None, lng=None)

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "info11_nogmaps_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/info")
    assert r.status_code == 200, r.text[:300]
    assert "maps.google.com" not in r.text


# ── /alarm/{token}: Login-Redirect auf die interne Einsatzinfo ──────────────

def test_alarm_link_eingeloggter_berechtigter_user_wird_auf_einsatzinfo_umgeleitet():
    _, incident_id, token = _setup_incident("info11_alarm_user", with_token=True)

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "info11_alarm_user", "Test1234!")

    r = client.get(f"/alarm/{token}", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/einsatz/{incident_id}/info"


def test_alarm_link_anonym_zeigt_weiterhin_oeffentliche_seite():
    _, incident_id, token = _setup_incident("info11_alarm_anon_user", with_token=True)

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    r = client.get(f"/alarm/{token}", follow_redirects=False)
    assert r.status_code == 200
    assert f"/einsatz/{incident_id}" in r.text  # Board-Link auf der oeffentlichen Seite selbst


# ── Objekt-Vorschlaege: Entfernung statt Objekt-Nr./ID ───────────────────────

def test_objekt_kandidaten_nach_entfernung_sortiert_nicht_nach_id():
    user_id, incident_id, _ = _setup_incident("info11_distanz_user", objekt_module=True)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # Bewusst so angelegt, dass die Objekt-Nr.-Reihenfolge (5, 10, 99) der
        # Entfernungs-Reihenfolge (nah -> fern) genau entgegengesetzt ist.
        fern = Objekt(org_id=ORG_ID, nummer=5, name="Fernes Objekt", strasse="Weit weg",
                      hausnummer="1", plz="6900", ort="Bregenz", lat=47.500, lng=9.800,
                      status=OBJEKT_STATUS_FREIGEGEBEN)
        mittel = Objekt(org_id=ORG_ID, nummer=10, name="Mittleres Objekt", strasse="Mittelweg",
                        hausnummer="2", plz="6922", ort="Wolfurt", lat=47.470, lng=9.755,
                        status=OBJEKT_STATUS_FREIGEGEBEN)
        nah = Objekt(org_id=ORG_ID, nummer=99, name="Nahes Objekt", strasse="Nahestrasse",
                    hausnummer="3", plz="6922", ort="Wolfurt", lat=47.4653, lng=9.7504,
                    status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add_all([fern, mittel, nah])
        db.commit()
        fern_id, mittel_id, nah_id = fern.id, mittel.id, nah.id
    finally:
        db.close()

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "info11_distanz_user", "Test1234!")

    r = client.get(f"/objekte/einsatz-panel/{incident_id}")
    assert r.status_code == 200, r.text[:300]
    body = r.text

    pos_nah = body.index(f"id: {nah_id},")
    pos_mittel = body.index(f"id: {mittel_id},")
    pos_fern = body.index(f"id: {fern_id},")
    # Entfernungs-Reihenfolge (nah -> mittel -> fern), NICHT die Objekt-Nr.-Reihenfolge.
    assert pos_nah < pos_mittel < pos_fern
