"""Objekt-Info bei Einsaetzen: Name/Adresse/BMA-Nr. statt Objekt-Nr. im Vordergrund
(Nutzer-Feedback: die OBJ-XXXX-Nummer ist im Alltag unwichtig, gesucht/erkannt wird
nach Name, Adresse und BMA-Nummer). Betrifft Board-Panel, Einsatzinfo-Section und
das Lageführungs-Kartenpopup-JSON.
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
    ObjektBMA,
    ObjektEinsatz,
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


def _setup(username, *, org_id=ORG_ID, nummer=9201):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Objekt10 Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))

        obj_sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if obj_sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            obj_sys_row.value = "true"
        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.objekt_module_enabled = True
        os_row.lagefuehrung_modul_aktiv = True

        objekt = Objekt(
            org_id=org_id, nummer=nummer, name="Gasthaus Adler", strasse="Kirchstrasse",
            hausnummer="12", plz="6922", ort="Wolfurt", lat=47.4652, lng=9.7503,
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add(objekt)
        db.flush()
        db.add(ObjektBMA(org_id=org_id, objekt_id=objekt.id, bma_nummer="4711"))

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=47.4652, lng=9.7503)
        db.add(incident)
        db.flush()
        db.add(ObjektEinsatz(org_id=org_id, objekt_id=objekt.id, incident_id=incident.id,
                              quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
        db.commit()
        return user.id, incident.id, objekt.id
    finally:
        db.close()


def test_board_panel_zeigt_name_adresse_bma_vor_objekt_nummer():
    _, incident_id, _ = _setup("obj10_board_user")

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "obj10_board_user", "Test1234!")

    r = client.get(f"/objekte/einsatz-panel/{incident_id}")
    assert r.status_code == 200, r.text[:300]
    body = r.text
    assert "Gasthaus Adler" in body
    assert "Kirchstrasse 12" in body
    assert "BMA 4711" in body
    assert "OBJ-9201" in body
    # Name muss vor der Objekt-Nummer im Markup erscheinen (prominent statt sekundaer).
    assert body.index("Gasthaus Adler") < body.index("OBJ-9201")


def test_einsatzinfo_section_zeigt_adresse_und_bma():
    _, incident_id, _ = _setup("obj10_info_user", nummer=9210)

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "obj10_info_user", "Test1234!")

    r = client.get(f"/objekte/einsatz-panel/{incident_id}?view=info")
    assert r.status_code == 200, r.text[:300]
    body = r.text
    assert "Gasthaus Adler" in body
    assert "Kirchstrasse 12" in body
    assert "BMA 4711" in body
    assert "OBJ-9210" in body


def test_kandidaten_suchtext_enthaelt_adresse_und_bma():
    _, incident_id, objekt_id = _setup("obj10_suche_user", nummer=9220)
    # Zweites, unverknuepftes Objekt als Such-Kandidat anlegen.
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kandidat = Objekt(
            org_id=ORG_ID, nummer=9221, name="Feuerwehrhaus", strasse="Bahnhofstrasse",
            hausnummer="3", plz="6922", ort="Wolfurt", lat=47.47, lng=9.75,
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add(kandidat)
        db.flush()
        db.add(ObjektBMA(org_id=ORG_ID, objekt_id=kandidat.id, bma_nummer="9999"))
        db.commit()
    finally:
        db.close()

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "obj10_suche_user", "Test1234!")

    r = client.get(f"/objekte/einsatz-panel/{incident_id}")
    assert r.status_code == 200, r.text[:300]
    body = r.text
    # Suchtext (t) muss BMA-Nr. und Adresse des Kandidaten enthalten, damit
    # nach diesen Kriterien gefiltert werden kann (nicht nur nach Nummer/Name).
    assert "9999" in body
    assert "bahnhofstrasse" in body.lower()
    # Anzeige-Label (l) im Ergebnis soll Name + Adresse zeigen, nicht die OBJ-Nummer voranstellen.
    assert "Feuerwehrhaus" in body
    assert "Bahnhofstrasse 3, 6922 Wolfurt" in body


def test_objekte_json_enthaelt_adresse_und_bma_nummer():
    _, incident_id, _ = _setup("obj10_karte_user", nummer=9230)

    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _login(client, "obj10_karte_user", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/objekte.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert len(data) == 1
    assert data[0]["adresse"] == "Kirchstrasse 12, 6922 Wolfurt"
    assert data[0]["bma_nummer"] == "4711"
