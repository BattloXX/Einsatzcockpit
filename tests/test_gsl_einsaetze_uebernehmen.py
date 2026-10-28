"""Manuelle Übernahme bestehender Einsätze in eine aktive GSL."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentOrg
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus, SiteLogEntry
from app.models.user import AuditLog
from app.services.major_incident_service import create_site
from tests.test_gsl_einsatzstelle_anlegen_picker import _lage, _login


@pytest.fixture
def lage_factory(client):
    def erstellen(role="incident_leader", is_exercise=False):
        name = "adopt_" + uuid4().hex[:12]
        lage_id = _lage(name, name, role)
        with SessionLocal() as db:
            set_tenant_context(db, None)
            lage = db.get(MajorIncident, lage_id)
            lage.is_exercise = is_exercise
            db.commit()
            org_id = lage.org_id
        client.get("/logout")
        assert _login(client, name).status_code == 302
        return lage_id, org_id
    return erstellen


def _incident(org_id, **kwargs):
    values = {
        "primary_org_id": org_id, "alarm_type_code": "T1", "status": "active",
        "address_city": "Pickerstadt", "address_street": "Hauptstraße", "address_no": "7",
        "lat": 47.5, "lng": 9.7, "report_text": "Wasser im Keller", "reason": "Fallback",
        "started_at": datetime(2026, 1, 15, 12, 30, tzinfo=UTC).replace(tzinfo=None),
    }
    values.update(kwargs)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        incident = Incident(**values)
        db.add(incident)
        db.commit()
        return incident.id


def _post(client, lage_id, ids, *, htmx=True):
    return client.post(
        f"/lage/{lage_id}/einsaetze-uebernehmen",
        data={"_csrf": client.cookies.get("ec_csrf"), "incident_ids": [str(i) for i in ids]},
        headers={"HX-Request": "true"} if htmx else {}, follow_redirects=False,
    )


def _site_ids(lage_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return [s.incident_id for s in db.query(IncidentSite).filter_by(major_incident_id=lage_id).all()]


def _angebot(client, lage_id, incident_id):
    response = client.get(f"/lage/{lage_id}/einsaetze-uebernehmen")
    assert response.status_code == 200
    return f'name="incident_ids" value="{incident_id}"' in response.text


def test_knopf_nur_mit_kandidaten_und_dialog_beim_oeffnen_aktuell(client, lage_factory):
    lage_id, org_id = lage_factory()
    board = client.get(f"/lage/{lage_id}")
    assert board.status_code == 200
    assert "Einsätze übernehmen (" not in board.text
    erster = _incident(org_id, lis_operation_number="f26001234", external_key="NICHT-ANZEIGEN")
    board = client.get(f"/lage/{lage_id}")
    assert "Einsätze übernehmen (1)" in board.text
    assert 'id="einsaetze-uebernehmen-knopf-mobil"' in board.text
    zweiter = _incident(org_id, nummer=42)
    assert _angebot(client, lage_id, erster)
    assert _angebot(client, lage_id, zweiter)
    dialog = client.get(f"/lage/{lage_id}/einsaetze-uebernehmen").text
    for text in ("f26001234", "42", "T1", "Hauptstraße", "Wasser im Keller", "15.01.2026", "13:30", "Alle auswählen"):
        assert text in dialog
    assert "NICHT-ANZEIGEN" not in dialog
    assert 'name="_csrf"' in dialog
    assert 'disabled :disabled="selected.length === 0"' in dialog


def test_teilauswahl_verknuepft_genau_diese_einsaetze_mit_log_audit_broadcast(client, lage_factory, monkeypatch):
    lage_id, org_id = lage_factory()
    ids = [_incident(org_id, external_key="original"), _incident(org_id), _incident(org_id, report_text=None)]
    broadcasts = []

    async def broadcast(db, lage, site_id):
        broadcasts.append((lage.id, site_id))

    monkeypatch.setattr("app.services.gsl_broadcast.stelle_angelegt", broadcast)
    response = _post(client, lage_id, [ids[0], ids[2]])
    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "einsaetzeUebernommen"
    assert set(_site_ids(lage_id)) == {ids[0], ids[2]}
    with SessionLocal() as db:
        set_tenant_context(db, None)
        sites = db.query(IncidentSite).filter_by(major_incident_id=lage_id).order_by(IncidentSite.incident_id).all()
        for site in sites:
            assert (site.ort, site.strasse, site.hausnr, site.lat, site.lng, site.alarm_stufe) == (
                "Pickerstadt", "Hauptstraße", "7", 47.5, 9.7, "T1",
            )
            assert site.created_by is not None
            log = db.query(SiteLogEntry).filter_by(incident_site_id=site.id).one()
            assert log.text == "Aus Einsatz übernommen" and log.user_id == site.created_by
            assert db.query(AuditLog).filter_by(action="major_incident.site.adopted", incident_id=site.incident_id,
                                              org_id=org_id).count() == 1
        assert [s.external_key for s in sites] == ["original", f"ui_{ids[2]}"]
        assert [s.einsatzgrund for s in sites] == ["Wasser im Keller", "Fallback"]
        assert set(broadcasts) == {(lage_id, s.id) for s in sites}
        assert all(db.get(Incident, i).status == "active" for i in ids)
    assert not _angebot(client, lage_id, ids[0])
    assert _angebot(client, lage_id, ids[1])
    knopf = client.get(f"/lage/{lage_id}/einsaetze-uebernehmen/knopf")
    assert "Einsätze übernehmen (1)" in knopf.text
    assert _post(client, lage_id, [ids[1]]).status_code == 204
    assert "Einsätze übernehmen (" not in client.get(f"/lage/{lage_id}/einsaetze-uebernehmen/knopf").text


def test_zuordnung_in_irgendeiner_aktiven_lage_schliesst_kandidaten_aus(client, lage_factory):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        andere_lage = MajorIncident(org_id=org_id, name="Andere aktive Lage", status=MajorIncidentStatus.active)
        db.add(andere_lage)
        db.flush()
        create_site(db, andere_lage, "Bereits übernommen", incident_id=incident_id)
        db.commit()
    assert not _angebot(client, lage_id, incident_id)
    assert _post(client, lage_id, [incident_id]).status_code == 204
    assert _site_ids(lage_id) == []


def test_zuordnung_in_geschlossener_lage_erlaubt_erneute_uebernahme(client, lage_factory):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        alte_lage = MajorIncident(org_id=org_id, name="Alte Lage", status=MajorIncidentStatus.closed)
        db.add(alte_lage)
        db.flush()
        create_site(db, alte_lage, "Alte Stelle", incident_id=incident_id)
        db.commit()
    assert _angebot(client, lage_id, incident_id)
    assert _post(client, lage_id, [incident_id]).status_code == 204
    assert _site_ids(lage_id) == [incident_id]


def test_fremde_org_geschlossene_und_manipulierte_ids_ignoriert(client, lage_factory):
    fremde_lage, fremde_org = lage_factory()
    fremd = _incident(fremde_org)
    lage_id, org_id = lage_factory()
    geschlossen = _incident(org_id, status="closed")
    gueltig = _incident(org_id)
    # Auch sichtbare kollaborative Einsätze fremder Federführung bleiben ausgeschlossen.
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.add(IncidentOrg(incident_id=fremd, org_id=org_id))
        db.commit()
    assert not _angebot(client, lage_id, fremd)
    assert not _angebot(client, lage_id, geschlossen)
    response = _post(client, lage_id, [fremd, geschlossen, gueltig, "kein-int", -1, 999999999])
    assert response.status_code == 204
    assert _site_ids(lage_id) == [gueltig]
    assert _site_ids(fremde_lage) == []
    assert _post(client, fremde_lage, [fremd]).status_code in (403, 404)


def test_readonly_bekommt_403_und_keinen_knopf(client, lage_factory):
    lage_id, org_id = lage_factory(role="readonly")
    incident_id = _incident(org_id)
    assert "Einsätze übernehmen (" not in client.get(f"/lage/{lage_id}").text
    assert client.get(f"/lage/{lage_id}/einsaetze-uebernehmen").status_code == 403
    assert _post(client, lage_id, [incident_id]).status_code == 403
    assert _site_ids(lage_id) == []


def test_gsl_nur_lesen_ist_auch_mit_bearbeitungsrolle_gesperrt(client, lage_factory, monkeypatch):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    from app.routers import ui_major_incident

    original = ui_major_incident._check_org_access

    def nur_lesen(user, lage):
        original(user, lage)
        user.gsl_nur_lesen = True

    monkeypatch.setattr(ui_major_incident, "_check_org_access", nur_lesen)
    assert "Einsätze übernehmen (" not in client.get(f"/lage/{lage_id}").text
    assert client.get(f"/lage/{lage_id}/einsaetze-uebernehmen").status_code == 403
    assert _post(client, lage_id, [incident_id]).status_code == 403


def test_doppelpost_und_doppelte_ids_erzeugen_nur_eine_stelle(client, lage_factory, monkeypatch):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    broadcasts = []

    async def broadcast(db, lage, site_id):
        broadcasts.append(site_id)

    monkeypatch.setattr("app.services.gsl_broadcast.stelle_angelegt", broadcast)
    for _ in range(2):
        assert _post(client, lage_id, [incident_id, incident_id]).status_code == 204
    assert _site_ids(lage_id) == [incident_id]
    assert len(broadcasts) == 1


@pytest.mark.parametrize("is_exercise", [False, True])
def test_uebungsmismatch_wird_nicht_angeboten_oder_uebernommen(client, lage_factory, is_exercise):
    lage_id, org_id = lage_factory(is_exercise=is_exercise)
    passend = _incident(org_id, is_exercise=is_exercise)
    mismatch = _incident(org_id, is_exercise=not is_exercise)
    assert _angebot(client, lage_id, passend)
    assert not _angebot(client, lage_id, mismatch)
    assert _post(client, lage_id, [passend, mismatch]).status_code == 204
    assert _site_ids(lage_id) == [passend]


def test_geschlossene_lage_verbietet_uebernahme(client, lage_factory):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(MajorIncident, lage_id).status = MajorIncidentStatus.closed
        db.commit()
    assert "Einsätze übernehmen (" not in client.get(f"/lage/{lage_id}").text
    assert client.get(f"/lage/{lage_id}/einsaetze-uebernehmen").status_code == 400
    assert _post(client, lage_id, [incident_id]).status_code == 400
    assert _site_ids(lage_id) == []


@pytest.mark.parametrize("role", ["incident_leader", "admin", "org_admin", "recorder"])
def test_erlaubte_rollen_und_redirect_ohne_htmx(client, lage_factory, role):
    lage_id, org_id = lage_factory(role=role)
    incident_id = _incident(org_id)
    response = _post(client, lage_id, [incident_id], htmx=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/lage/{lage_id}"
    assert _site_ids(lage_id) == [incident_id]


def test_csrf_ist_erforderlich(client, lage_factory):
    lage_id, org_id = lage_factory()
    incident_id = _incident(org_id)
    response = client.post(f"/lage/{lage_id}/einsaetze-uebernehmen", data={"incident_ids": str(incident_id)})
    assert response.status_code == 403
    assert _site_ids(lage_id) == []
