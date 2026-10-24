"""End-to-end target scenario for the automated GSL board HTTP routes."""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageJournalEntry, SitePhase
from tests.test_einheit_api import _als, _daten, _device, _h
from tests.test_gsl_fuehrung_einheiten import _mit_rolle, _post


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _phase(site_id):
    db = _db()
    try:
        return db.get(IncidentSite, site_id).phase
    finally:
        db.close()


def _journal_count(site_id, ereignis_typ):
    db = _db()
    try:
        return db.query(LageJournalEntry).filter_by(site_id=site_id, ereignis_typ=ereignis_typ).count()
    finally:
        db.close()


def _offener_auftrag(zustand, site_id):
    kategorien = zustand["kategorien"]
    auftraege = [kategorien["aktuell"]] if kategorien["aktuell"] else []
    auftraege.extend(kategorien["weitere"])
    return next(item for item in auftraege if item["site_id"] == site_id)


def _alte_testauftraege_zurueckziehen(data):
    db = _db()
    try:
        data["e2"] = db.get(EinheitSiteDispatch, data["d2"]).einheit_id
        for dispatch in db.query(EinheitSiteDispatch).filter(
            EinheitSiteDispatch.einheit_id.in_((data["e1"], data["e2"]))
        ):
            dispatch.withdrawn_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def test_zielbild_erkundung_abarbeitung_abschluss_und_wiedereroeffnen(client, setup_db):
    data = _daten()
    _alte_testauftraege_zurueckziehen(data)
    lage_id = data["lage"]
    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))

    created = _post(
        client,
        f"/lage/{lage_id}/stellen/neu",
        {"bezeichnung": "Zielbild", "einsatzgrund": "Wassereintritt", "ort": "Testort"},
    )
    assert created.status_code == 204
    db = _db()
    try:
        site = db.query(IncidentSite).filter_by(major_incident_id=lage_id, bezeichnung="Zielbild").one()
        site_id = site.id
        assert site.phase == SitePhase.eingegangen
    finally:
        db.close()

    dispatch_url = f"/lage/{lage_id}/stellen/{site_id}/einheit-disponieren"
    erkundung = _post(
        client,
        dispatch_url,
        {"einheit_id": data["e1"], "auftragsart": "erkundung", "auftrag": "Lage erkunden"},
    )
    assert erkundung.status_code == 200
    assert _phase(site_id) == SitePhase.erkundung

    _device(client, data)
    zustand = client.get("/einheit/api/zustand")
    assert zustand.status_code == 200
    next_auftrag = _offener_auftrag(zustand.json(), site_id)
    assert next_auftrag["naechster"] is True
    assert next_auftrag["auftragsart"] == "erkundung"
    assert next_auftrag["nav_url"]
    erkundung_id = next_auftrag["dispatch_id"]
    detail = client.get(f"/einheit/api/auftrag/{erkundung_id}")
    assert detail.status_code == 200
    assert "in_arbeit" not in detail.json()["auftrag"]["erlaubte_status"]

    ohne_ergebnis = client.post(
        f"/einheit/api/auftrag/{erkundung_id}/status",
        json={"status": "abgeschlossen", "client_uuid": str(uuid4())},
        headers=_h(client),
    )
    assert ohne_ergebnis.status_code == 422
    assert _phase(site_id) == SitePhase.erkundung
    assert _journal_count(site_id, "erkundet") == 0
    ergebnis_uuid = str(uuid4())
    ergebnis = client.post(
        f"/einheit/api/auftrag/{erkundung_id}/meldung",
        json={"art": "lagemeldung", "client_uuid": ergebnis_uuid, "erkundungsergebnis": True,
              "schnellmeldung": "massnahmen_erforderlich", "text": "Keller überflutet. Auspumpen erforderlich."},
        headers=_h(client),
    )
    assert ergebnis.status_code == 200, ergebnis.text
    erkundung_uuid = str(uuid4())
    abschluss_payload = {"status": "abgeschlossen", "client_uuid": erkundung_uuid,
                         "ergebnis_client_uuid": ergebnis_uuid}
    abgeschlossen = client.post(
        f"/einheit/api/auftrag/{erkundung_id}/status",
        json=abschluss_payload,
        headers=_h(client),
    )
    replay = client.post(
        f"/einheit/api/auftrag/{erkundung_id}/status",
        json=abschluss_payload,
        headers=_h(client),
    )
    assert abgeschlossen.status_code == replay.status_code == 200
    assert _phase(site_id) == SitePhase.bewertet
    assert _journal_count(site_id, "erkundet") == 1
    assert _journal_count(site_id, "bewertet") == 1

    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))
    assert _post(client, dispatch_url, {"einheit_id": data["e1"], "auftragsart": "abarbeitung"}).status_code == 200
    assert _post(client, dispatch_url, {"einheit_id": data["e2"], "auftragsart": "abarbeitung"}).status_code == 200
    assert _phase(site_id) == SitePhase.disponiert

    _device(client, data)
    a_auftrag = _offener_auftrag(client.get("/einheit/api/zustand").json(), site_id)
    a_id = a_auftrag["dispatch_id"]
    assert client.post(
        f"/einheit/api/auftrag/{a_id}/status",
        json={"status": "in_arbeit", "client_uuid": str(uuid4())},
        headers=_h(client),
    ).status_code == 200
    assert _phase(site_id) == SitePhase.in_arbeit
    assert client.post(
        f"/einheit/api/auftrag/{a_id}/status",
        json={"status": "abgeschlossen", "client_uuid": str(uuid4())},
        headers=_h(client),
    ).status_code == 200
    assert _phase(site_id) == SitePhase.in_arbeit

    _device(client, data, second=True)
    b_auftrag = _offener_auftrag(client.get("/einheit/api/zustand").json(), site_id)
    b_id = b_auftrag["dispatch_id"]
    assert client.post(
        f"/einheit/api/auftrag/{b_id}/status",
        json={"status": "in_arbeit", "client_uuid": str(uuid4())},
        headers=_h(client),
    ).status_code == 200
    fertig_uuid = str(uuid4())
    fertig = client.post(
        f"/einheit/api/auftrag/{b_id}/status",
        json={"status": "abgeschlossen", "client_uuid": fertig_uuid},
        headers=_h(client),
    )
    fertig_replay = client.post(
        f"/einheit/api/auftrag/{b_id}/status",
        json={"status": "abgeschlossen", "client_uuid": fertig_uuid},
        headers=_h(client),
    )
    assert fertig.status_code == fertig_replay.status_code == 200
    assert _phase(site_id) == SitePhase.fertig_gemeldet
    assert _journal_count(site_id, "abgeschlossen") == 2
    assert _journal_count(site_id, "alle_fertig") == 1

    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))
    hinweise = client.get(f"/lage/{lage_id}/dashboard/hinweise")
    assert hinweise.status_code == 200
    assert "Zielbild" in hinweise.text and "Abschluss ausständig" in hinweise.text
    assert _post(client, f"/lage/{lage_id}/stellen/{site_id}/abschliessen").status_code == 204
    assert _phase(site_id) == SitePhase.erledigt
    assert _journal_count(site_id, "site_abgeschlossen") == 1

    geschlossen = _post(client, dispatch_url, {"einheit_id": data["e1"], "auftragsart": "abarbeitung"})
    assert geschlossen.status_code == 400
    assert "wieder öffnen" in geschlossen.text
    assert _phase(site_id) == SitePhase.erledigt

    reopened = _post(client, f"/lage/{lage_id}/stellen/{site_id}/wiedereroeffnen")
    assert reopened.status_code == 204
    assert _phase(site_id) == SitePhase.fertig_gemeldet
    assert _journal_count(site_id, "site_wiedereroeffnet") == 1
