"""Regressionstest: Lageführung-Editor-Rechte live umschaltbar (Session 2026-07-16,
GSL-Reload-Audit-Nachtrag).

Vormals loeste ein per WS empfangenes lagefuehrung.berechtigung.changed fuer den
eigenen Nutzer einen kompletten location.reload() aus, weil Toolbar/Taktik-Tab/
Fahrzeugsuche nur bei can_edit ueberhaupt ins DOM gerendert wurden. Diese Bloecke
werden jetzt immer gerendert (nur per `hidden`-Attribut versteckt) und das Template
liefert zusaetzlich `editierbarViaRolle` getrennt von `editierbar`, damit das
Frontend beim Widerruf einer Berechtigung den korrekten Endzustand nachbilden kann
(Rolle behaelt das Recht trotz Widerruf einer Extra-Berechtigung).

Diese Tests pruefen die serverseitige Grundlage dieses Live-Togglings: die
gerenderte Seite muss fuer alle drei Faelle (Rollen-Editor, reiner Viewer,
Viewer mit explizit erteilter Berechtigung) die richtigen Flags/DOM-Attribute
liefern."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.lagefuehrung import LagefuehrungBerechtigung
from app.models.master import OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
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


def _setup(username, *, rollen=("incident_leader",)):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft Toggle Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=ORG_ID).first()
        if os_row is None:
            os_row = OrgSettings(org_id=ORG_ID)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True

        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active",
                           lat=47.4652, lng=9.7503)
        db.add(incident)
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


def test_rollen_editor_sieht_editierbar_und_ungehiddene_werkzeuge(client):
    _, incident_id = _setup("lft_tog_rolle", rollen=("incident_leader",))
    _login(client, "lft_tog_rolle", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert r.status_code == 200
    assert "editierbar: true" in r.text
    assert "editierbarViaRolle: true" in r.text
    # Taktik-Tab-Button, Werkzeuge-Container und Uebernehmen-Formular ohne hidden;
    # der Nur-Ansicht-Hinweis dagegen hidden.
    assert 'id="lft-tab-taktik" data-lft-tab="taktik"\n                >Taktik' in r.text
    assert 'id="lft-edit-werkzeuge" >' in r.text
    assert 'id="lft-uebernehmen-form" >' in r.text
    assert 'id="lft-readonly-hint" class="form-hint" style="margin-top:10px;" hidden>' in r.text


def test_reiner_viewer_sieht_editierbar_false_und_versteckte_werkzeuge(client):
    _, incident_id = _setup("lft_tog_viewer", rollen=("readonly",))
    _login(client, "lft_tog_viewer", "Test1234!")

    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert r.status_code == 200
    assert "editierbar: false" in r.text
    assert "editierbarViaRolle: false" in r.text
    # Taktik-Tab-Button, Werkzeuge-Container und Uebernehmen-Formular versteckt;
    # der Nur-Ansicht-Hinweis dagegen sichtbar.
    assert 'id="lft-tab-taktik" data-lft-tab="taktik"\n                hidden>Taktik' in r.text
    assert 'id="lft-edit-werkzeuge" hidden>' in r.text
    assert 'id="lft-uebernehmen-form" hidden>' in r.text
    assert 'id="lft-readonly-hint" class="form-hint" style="margin-top:10px;" >' in r.text


def test_viewer_mit_erteilter_berechtigung_editierbar_true_aber_nicht_via_rolle(client):
    """Kernfall fuer den Live-Toggle: editierbar=true (durch Extra-Berechtigung),
    aber editierbarViaRolle=false -- genau diese Unterscheidung braucht das Frontend,
    um bei einem spaeteren Widerruf korrekt auf false statt faelschlich auf die
    Rollen-Berechtigung zurueckzufallen."""
    user_id, incident_id = _setup("lft_tog_granted", rollen=("readonly",))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(LagefuehrungBerechtigung(org_id=ORG_ID, incident_id=incident_id, user_id=user_id))
        db.commit()
    finally:
        db.close()

    _login(client, "lft_tog_granted", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung")
    assert r.status_code == 200
    assert "editierbar: true" in r.text
    assert "editierbarViaRolle: false" in r.text
    assert 'id="lft-edit-werkzeuge" >' in r.text
