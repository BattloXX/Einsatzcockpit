"""Lageführung: hinterlegte Objekt-Geometrien (Zufahrten/Sammelplaetze) auf der
Einsatzkarte (Nutzer-Feedback nach Konzept-Abgleich — F04 war bislang nur Punkt-
Marker + Text, ohne die in der Objekt-Lagekarte gepflegten Geometrien).

QuickPrint (Druckeinstellungen merken) und das Mobile-Bottom-Sheet sind reines
Frontend-Verhalten (localStorage/CSS) und daher browserseitig statt hier zu
verifizieren.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektEinsatz,
    ObjektKartenObjekt,
)
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


def _setup(username, *, org_id=ORG_ID, lat=47.4652, lng=9.7503):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft7 Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        obj_sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if obj_sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            obj_sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True
        os_row.objekt_module_enabled = True

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=lat, lng=lng)
        db.add(incident)
        db.flush()
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


def test_objekte_json_enthaelt_kartenobjekte_punkt_und_geometrie():
    _, incident_id = _setup("lft7_kobj_user")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = Objekt(
            org_id=ORG_ID, nummer=9101, name="Testobjekt", lat=47.4652, lng=9.7503,
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add(objekt)
        db.flush()
        db.add(ObjektKartenObjekt(
            org_id=ORG_ID, objekt_id=objekt.id, typ="sammelplatz",
            lat=47.4655, lng=9.7510, label="Sammelplatz Nord",
        ))
        db.add(ObjektKartenObjekt(
            org_id=ORG_ID, objekt_id=objekt.id, typ="geometrie",
            geometry_json='{"type": "LineString", "coordinates": [[9.750, 47.465], [9.751, 47.466]]}',
            label="Zufahrt Nord",
        ))
        db.add(ObjektEinsatz(org_id=ORG_ID, objekt_id=objekt.id, incident_id=incident_id,
                              quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
        db.commit()
    finally:
        db.close()

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "lft7_kobj_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/objekte.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert len(data) == 1
    kartenobjekte = data[0]["kartenobjekte"]
    assert len(kartenobjekte) == 2

    punkt = next(k for k in kartenobjekte if k["typ"] == "sammelplatz")
    assert punkt["lat"] == 47.4655
    assert punkt["label"] == "Sammelplatz Nord"
    assert punkt["typ_label"] == "Sammelplatz"
    assert punkt["geometry"] is None

    linie = next(k for k in kartenobjekte if k["typ"] == "geometrie")
    assert linie["geometry"]["type"] == "LineString"
    assert linie["label"] == "Zufahrt Nord"
    assert linie["lat"] is None


def test_objekte_json_ueberspringt_kartenobjekte_ohne_position():
    _, incident_id = _setup("lft7_kobj_leer_user")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = Objekt(
            org_id=ORG_ID, nummer=9102, name="Testobjekt Leer", lat=47.4652, lng=9.7503,
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add(objekt)
        db.flush()
        # Weder lat/lng noch geometry_json gesetzt -> darf nicht in der Ausgabe landen.
        db.add(ObjektKartenObjekt(org_id=ORG_ID, objekt_id=objekt.id, typ="fsd", label="Kaputter Eintrag"))
        db.add(ObjektEinsatz(org_id=ORG_ID, objekt_id=objekt.id, incident_id=incident_id,
                              quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
        db.commit()
    finally:
        db.close()

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "lft7_kobj_leer_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/objekte.json")
    assert r.status_code == 200, r.text[:300]
    assert r.json()[0]["kartenobjekte"] == []
