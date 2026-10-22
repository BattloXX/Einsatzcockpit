"""ECP-01: Objekt beim Anlegen von Einsatz, Meldung und Auftrag auswaehlen und verknuepfen."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Message, Task
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    GefahrenKatalog,
    Objekt,
    ObjektEinsatz,
    ObjektGefahr,
)
from app.models.user import Role, User, UserRole
from app.services.incident_service import _create_fixed_columns


def _login(client, username, password="Test1234!"):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    rolle = db.query(Role).filter(Role.code == code).first()
    if rolle is None:
        rolle = Role(code=code, name=code)
        db.add(rolle)
        db.flush()
    return rolle


@pytest.fixture()
def daten():
    """Eigene Org mit aktivem Objektmodul, Einsatzleiter und Protokollant, ein Objekt mit Gefahr, fremde Org+Objekt."""
    with SessionLocal() as db:
        set_tenant_context(db, None)
        kennung = uuid4().hex[:8]
        org = FireDept(slug=f"oba-{kennung}", name="Objekt-Anlegen", color="#123456", bos="Feuerwehr")
        fremd = FireDept(slug=f"oba-f-{kennung}", name="Fremde Org", color="#654321", bos="Feuerwehr")
        db.add_all([org, fremd])
        db.flush()
        setting = db.get(SystemSettings, "objekt_module_enabled")
        if setting is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            setting.value = "true"
        for org_id in (org.id, fremd.id):
            db.add(OrgSettings(org_id=org_id, objekt_module_enabled=True))
        leiter = User(username=f"leiter-{kennung}", password_hash=hash_password("Test1234!"),
                      display_name="Leiter", org_id=org.id, active=True)
        schreiber = User(username=f"schreiber-{kennung}", password_hash=hash_password("Test1234!"),
                         display_name="Schreiber", org_id=org.id, active=True)
        db.add_all([leiter, schreiber])
        db.flush()
        db.add(UserRole(user_id=leiter.id, role_id=_rolle(db, "incident_leader").id))
        db.add(UserRole(user_id=schreiber.id, role_id=_rolle(db, "recorder").id))
        kat = GefahrenKatalog(org_id=org.id, name="Gasanschluss", piktogramm_typ="gas", aktiv=True)
        db.add(kat)
        db.flush()
        objekt = Objekt(org_id=org.id, nummer=1, name="Lagerhalle", status="freigegeben")
        fremdes = Objekt(org_id=fremd.id, nummer=1, name="Fremdes Objekt", status="freigegeben")
        db.add_all([objekt, fremdes])
        db.flush()
        db.add(ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=kat.id, stoffname="Fluessiggas"))
        incident = Incident(primary_org_id=org.id, alarm_type_code="B1", status="active")
        db.add(incident)
        db.flush()
        _create_fixed_columns(db, incident)
        db.commit()
        return {
            "org": org.id, "leiter": leiter.username, "schreiber": schreiber.username,
            "objekt": objekt.id, "fremdes": fremdes.id, "incident": incident.id,
        }


def _verknuepfungen(incident_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return db.query(ObjektEinsatz).filter(ObjektEinsatz.incident_id == incident_id).all()


def _neuer_einsatz(client, csrf, objekt_id, strasse):
    return client.post("/einsatz/neu", data={
        "_csrf": csrf, "alarm_type_code": "T1", "confirm_real_incident": "true",
        "address_street": strasse, "address_no": "1", "address_city": "Wolfurt",
        "report_text": "ECP-01", "objekt_id": str(objekt_id) if objekt_id else "",
    }, follow_redirects=False)


def test_neuer_einsatz_verknuepft_gewaehltes_objekt_und_erzeugt_gefahrenmeldung(client, daten, monkeypatch):
    async def nichts(*a, **kw):
        return None

    monkeypatch.setattr("app.services.incident_notify.notify_incident_created", nichts)
    _login(client, daten["leiter"])
    csrf = client.cookies.get("ec_csrf")
    antwort = _neuer_einsatz(client, csrf, daten["objekt"], f"Erststrasse {uuid4().hex[:6]}")
    assert antwort.status_code == 303
    incident_id = int(antwort.headers["location"].rsplit("/", 1)[-1])
    (verknuepfung,) = _verknuepfungen(incident_id)
    assert (verknuepfung.objekt_id, verknuepfung.quelle, verknuepfung.status) == (
        daten["objekt"], "manuell", OBJEKT_EINSATZ_BESTAETIGT,
    )
    with SessionLocal() as db:
        set_tenant_context(db, None)
        meldungen = db.query(Message).filter(Message.incident_id == incident_id,
                                             Message.objekt_gefahr_id.isnot(None)).count()
        assert meldungen == 1


def test_neuer_einsatz_ohne_objekt_bleibt_unveraendert(client, daten, monkeypatch):
    async def nichts(*a, **kw):
        return None

    monkeypatch.setattr("app.services.incident_notify.notify_incident_created", nichts)
    _login(client, daten["leiter"])
    antwort = _neuer_einsatz(client, client.cookies.get("ec_csrf"), None, f"Zweitstrasse {uuid4().hex[:6]}")
    assert antwort.status_code == 303
    assert _verknuepfungen(int(antwort.headers["location"].rsplit("/", 1)[-1])) == []


def test_fremdes_objekt_wird_abgelehnt_und_es_entsteht_kein_einsatz(client, daten, monkeypatch):
    async def nichts(*a, **kw):
        return None

    monkeypatch.setattr("app.services.incident_notify.notify_incident_created", nichts)
    _login(client, daten["leiter"])
    strasse = f"Drittstrasse {uuid4().hex[:6]}"
    antwort = _neuer_einsatz(client, client.cookies.get("ec_csrf"), daten["fremdes"], strasse)
    assert antwort.status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(Incident).filter(Incident.address_street == strasse).count() == 0


def test_auftrag_und_meldung_verknuepfen_objekt_beim_anlegen(client, daten):
    _login(client, daten["leiter"])
    csrf = client.cookies.get("ec_csrf")
    inc = daten["incident"]
    auftrag = client.post(f"/einsatz/{inc}/aufgabe", data={
        "_csrf": csrf, "title": "Gas absperren", "objekt_id": str(daten["objekt"])})
    assert auftrag.status_code == 200
    assert [v.objekt_id for v in _verknuepfungen(inc)] == [daten["objekt"]]
    # zweite Anlage mit demselben Objekt: idempotent, keine zweite Verknuepfung
    meldung = client.post(f"/einsatz/{inc}/meldung", data={
        "_csrf": csrf, "title": "Halle brennt", "objekt_id": str(daten["objekt"])})
    assert meldung.status_code == 200
    assert len(_verknuepfungen(inc)) == 1
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(Task).filter(Task.incident_id == inc, Task.title == "Gas absperren").count() == 1
        assert db.query(Message).filter(Message.incident_id == inc, Message.title == "Halle brennt").count() == 1


def test_ohne_berechtigung_wird_nichts_angelegt_und_kein_objekt_verknuepft(client, daten):
    _login(client, daten["schreiber"])
    csrf = client.cookies.get("ec_csrf")
    inc = daten["incident"]
    antwort = client.post(f"/einsatz/{inc}/aufgabe", data={
        "_csrf": csrf, "title": "Ohne Recht", "objekt_id": str(daten["objekt"])})
    assert antwort.status_code == 403
    assert _verknuepfungen(inc) == []
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(Task).filter(Task.incident_id == inc, Task.title == "Ohne Recht").count() == 0
    # ohne Objekt darf der Protokollant weiterhin Auftraege anlegen
    ok = client.post(f"/einsatz/{inc}/aufgabe", data={"_csrf": csrf, "title": "Normal"})
    assert ok.status_code == 200


def test_objektauswahl_nur_bei_berechtigung_und_aktivem_modul(client, daten):
    _login(client, daten["leiter"])
    assert 'aria-label="Objekt suchen"' in client.get("/").text
    assert 'aria-label="Objekt suchen"' in client.get(f"/einsatz/{daten['incident']}").text
    _login(client, daten["schreiber"])
    assert 'aria-label="Objekt suchen"' not in client.get(f"/einsatz/{daten['incident']}").text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter(OrgSettings.org_id == daten["org"]).one().objekt_module_enabled = False
        db.commit()
    _login(client, daten["leiter"])
    assert 'aria-label="Objekt suchen"' not in client.get("/").text


def test_objektliste_api_ohne_einsatz_liefert_adresse_und_bma(client, daten):
    _login(client, daten["leiter"])
    antwort = client.get("/api/uebernahme/objekte")
    assert antwort.status_code == 200
    (eintrag,) = [o for o in antwort.json()["objekte"] if o["id"] == daten["objekt"]]
    assert "adresse" in eintrag and "bma" in eintrag and eintrag["verknuepft"] is False
