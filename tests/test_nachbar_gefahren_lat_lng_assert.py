"""Regressionstest fuer GET /einsatz/{id}/nachbar-gefahren.json.

Der mypy-Cleanup (65 -> 36 Fehler) hat hier einen `assert o.lat is not None and o.lng is not
None` ergaenzt, weil die Query bereits per SQL-Filter (Objekt.lat.is_not(None), Objekt.lng.is_not
(None)) garantiert, dass keine Nachbarobjekte ohne Koordinaten in die Distanzberechnung gelangen
-- mypy kennt dieses Filter-Narrowing nur nicht. Dieser Test deckt den kompletten Endpoint-Pfad
(inkl. Distanzberechnung + Gefahren-Serialisierung) ab, der zuvor ungetestet war, um sicherzustellen,
dass der neue Assert bei realistischen Daten nicht greift.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import GefahrenKatalog, Objekt, ObjektGefahr
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


def _setup(username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Nachbar-Gefahren Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=ORG_ID).first()
        if os_row is None:
            os_row = OrgSettings(org_id=ORG_ID)
            db.add(os_row)
        os_row.objekt_module_enabled = True

        # Einsatz mit Koordinaten (Bezugspunkt fuer den Nachbar-Umkreis)
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="B1", status="active",
                             lat=47.4652, lng=9.7503)
        db.add(incident)

        # Nachbar-Objekt ~50m entfernt, mit einer strukturierten Gefahr -> muss im Ergebnis
        # auftauchen (durchlaeuft die _haversine_m/_richtung-Aufrufe mit dem neuen Assert).
        katalog = GefahrenKatalog(org_id=ORG_ID, name="Gasflaschenlager", piktogramm_typ="gas")
        db.add(katalog)
        db.flush()
        objekt = Objekt(org_id=ORG_ID, nummer=9001, name="Nachbarhaus", lat=47.4656, lng=9.7508)
        db.add(objekt)
        db.flush()
        db.add(ObjektGefahr(org_id=ORG_ID, objekt_id=objekt.id, gefahr_id=katalog.id, sort=0))

        db.commit()
        return user.id, incident.id
    finally:
        db.close()


def test_nachbar_gefahren_json_mit_koordinaten_und_gefahr():
    _, incident_id = _setup("nachbargef_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "nachbargef_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/nachbar-gefahren.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    # Die geteilte Test-DB kann bereits andere Objekt-Fixtures in der Naehe enthalten (andere
    # Testdateien) -- gezielt nach unserem eigenen Objekt suchen statt Index 0 anzunehmen.
    treffer = [o for o in data["objekte"] if o["name"] == "Nachbarhaus"]
    assert treffer, "Erwartetes Nachbarobjekt mit Gefahr fehlt in der Antwort"
    obj = treffer[0]
    assert obj["gefahren"]
    assert obj["entfernung_m"] < settings_radius()


def settings_radius() -> int:
    from app.config import settings
    return settings.NACHBAR_GEFAHR_RADIUS_M
