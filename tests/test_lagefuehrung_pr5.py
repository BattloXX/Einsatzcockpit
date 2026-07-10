"""Lageführung-Modul: Zeichnen-Fixes (Nutzer-Feedback nach Phase 3 Live-Test).

Betrifft primär die Lagekarte selbst (lagefuehrung.js/lagefuehrung.html) — die meisten
Fixes (verwaiste Geoman-Rohlayer, Flächen-Palette-Stilübernahme, Text-Werkzeug, generischer
Beschriftungs-Popup) sind reines Frontend-Verhalten und daher browserseitig statt hier zu
verifizieren. Diese Tests decken nur die dafür nötigen, tatsächlich backend-seitig
beobachtbaren Datenverträge ab: typ="text" ist erstellbar, und beliebige props (inkl. der
neuen Flächen-Stil-Felder) werden verlustfrei gespeichert/zurückgegeben.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn
from app.models.master import OrgSettings, SystemSettings
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


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",), lat=47.4652, lng=9.7503):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft5 Test", org_id=org_id, active=True)
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


def test_text_feature_erstellbar(client):
    _, incident_id = _setup("lft5_text_user")
    _login(client, "lft5_text_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={
            "typ": "text", "label": "Absperrung ab hier",
            "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text[:300]
    data = r.json()
    assert data["typ"] == "text"
    assert data["label"] == "Absperrung ab hier"

    liste = client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json").json()
    assert any(f["id"] == data["id"] and f["typ"] == "text" for f in liste)


def test_flaeche_props_bleiben_erhalten(client):
    """Die Flächen-Palette (lagefuehrung.js::renderFlaechenPicker) komponiert das props-Objekt
    rein clientseitig — hier wird nur geprüft, dass der Server es unverändert speichert/liefert,
    wie jedes andere freie JSON-props-Feld auch."""
    _, incident_id = _setup("lft5_flaeche_user")
    _login(client, "lft5_flaeche_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    props = {
        "flaeche_key": "brand", "color": "#dc2626", "hatch": "diag",
        "hatchColor": "#dc2626", "dash": False,
    }
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={
            "typ": "zeichnung", "label": "Brandbereich",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[9.75, 47.46], [9.76, 47.46], [9.76, 47.47], [9.75, 47.46]]],
            },
            "props": props,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text[:300]
    data = r.json()
    assert data["props"] == props

    liste = client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json").json()
    saved = next(f for f in liste if f["id"] == data["id"])
    assert saved["props"] == props
