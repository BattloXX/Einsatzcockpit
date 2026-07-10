"""Lageführung-Modul: Fahrzeug-Icon-Fallback, Beschriftungen-Layer, Objekt-Infos auf der
Lagekarte und WYSIWYG-Kartendruck (Nutzer-Feedback nach Phase-3-Live-Test, zweite Runde).

Icon-Fallback/Beschriftungen-Toggle/Board-Button sind reines Frontend-Verhalten und daher
browserseitig statt hier zu verifizieren. Diese Tests decken die backend-seitig beobachtbaren
Verträge ab: objekte.json liefert jetzt Gefahren/Kontakte/Informationen, und die neue
Druckroute liefert eine gültige HTML-Seite ohne Journal/Kräfteübersicht.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    Objekt,
    ObjektEinsatz,
    ObjektGefahr,
    ObjektKontakt,
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


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",), lat=47.4652, lng=9.7503,
           objekt_enabled=False):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft6 Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True

        if objekt_enabled:
            obj_sys_row = db.get(SystemSettings, "objekt_module_enabled")
            if obj_sys_row is None:
                db.add(SystemSettings(key="objekt_module_enabled", value="true"))
            else:
                obj_sys_row.value = "true"
            os_row.objekt_module_enabled = True

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=lat, lng=lng)
        db.add(incident)
        db.flush()
        db.add(IncidentColumn(
            incident_id=incident.id, code="active", title="Tatsächlich im Einsatz",
            column_kind="vehicles", is_fixed=True,
        ))
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


def test_objekte_json_enthaelt_gefahren_kontakte_informationen():
    _, incident_id = _setup("lft6_objekt_user", objekt_enabled=True)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gefahr_katalog = GefahrenKatalog(org_id=ORG_ID, name="Photovoltaik", piktogramm_typ="pv")
        db.add(gefahr_katalog)
        db.flush()

        objekt = Objekt(
            org_id=ORG_ID, nummer=1, name="Testobjekt", lat=47.4652, lng=9.7503,
            informationen="Vorsicht: Hund im Hof", anfahrtsweg="Nur über Hofeinfahrt Nord",
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add(objekt)
        db.flush()
        db.add(ObjektGefahr(org_id=ORG_ID, objekt_id=objekt.id, gefahr_id=gefahr_katalog.id,
                             un_nummer="1234", stoffname="Testgefahrgut"))
        db.add(ObjektKontakt(org_id=ORG_ID, objekt_id=objekt.id, art="betreiber",
                              name="Max Mustermann", telefone_json='["+43 664 1234567"]'))
        db.add(ObjektEinsatz(org_id=ORG_ID, objekt_id=objekt.id, incident_id=incident_id,
                              quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
        db.commit()
    finally:
        db.close()

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "lft6_objekt_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/objekte.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert len(data) == 1
    o = data[0]
    assert o["informationen"] == "Vorsicht: Hund im Hof"
    assert o["anfahrtsweg"] == "Nur über Hofeinfahrt Nord"
    assert len(o["gefahren"]) == 1
    assert o["gefahren"][0]["un_nummer"] == "1234"
    assert o["gefahren"][0]["stoffname"] == "Testgefahrgut"
    assert len(o["kontakte"]) == 1
    assert o["kontakte"][0]["name"] == "Max Mustermann"
    assert o["kontakte"][0]["telefone"] == ["+43 664 1234567"]


def test_karte_druck_route_liefert_html_ohne_journal(client):
    _, incident_id = _setup("lft6_druck_user")
    _login(client, "lft6_druck_user", "Test1234!")

    r = client.get(
        f"/einsatz/{incident_id}/lagefuehrung/karte/druck",
        params={
            "min_lat": 47.46, "min_lng": 9.74, "max_lat": 47.47, "max_lng": 9.76,
            "fmt": "A4 landscape", "layers": "einsatzort,fahrzeuge,beschriftung",
        },
    )
    assert r.status_code == 200, r.text[:300]
    assert "karte-map" in r.text
    assert "print-ts" in r.text
    assert "karte-legende" in r.text
    assert "Journal" not in r.text
    assert "Chronologie" not in r.text


def test_karte_druck_ungueltiges_format_faellt_zurueck():
    _, incident_id = _setup("lft6_druck_fmt_user")
    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "lft6_druck_fmt_user", "Test1234!")

    r = client.get(
        f"/einsatz/{incident_id}/lagefuehrung/karte/druck",
        params={"min_lat": 47.46, "min_lng": 9.74, "max_lat": 47.47, "max_lng": 9.76, "fmt": "Bogus"},
    )
    assert r.status_code == 200
    assert "A4 Querformat" in r.text
