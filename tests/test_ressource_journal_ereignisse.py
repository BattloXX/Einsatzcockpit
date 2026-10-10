"""Ereignistypen des Ressourcenjournals."""
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, LageEinheit, LageJournalEntry, MajorIncident
from app.services import resource_service as rs
from app.services.einheit_service import kontext_fuer_einheit, setze_einheit_status
from tests.conftest import TestingSession
from tests.test_einheit_api import _als, _h
from tests.test_einheit_api import _daten as api_daten


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db):
    lage = MajorIncident(name="Journal-Test", org_id=1, started_at=datetime.now(UTC))
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Stelle A")
    einheit = LageEinheit(lage_id=lage.id, label="TLF", resource_type="fahrzeug", status=rs.STATUS_BEREITGESTELLT)
    db.add_all((site, einheit))
    db.flush()
    return lage, site, einheit


def _eintrag(db, typ):
    db.flush()
    return db.query(LageJournalEntry).filter_by(ereignis_typ=typ).one()


def _ctx(db, einheit, quelle="funk"):
    return kontext_fuer_einheit(db, SimpleNamespace(org_id=1, is_system_admin=False), einheit.id, quelle=quelle)


def test_angelegt_hat_einheitsbezug():
    with _session() as db:
        lage, _, _ = _daten(db)
        einheit = rs.add_resource(db, lage.id, "MTF", author_name="EL", user_id=1)
        eintrag = _eintrag(db, "angelegt")
        assert eintrag.einheit_id == einheit.id and eintrag.quelle == "manuell"


def test_status_hat_einheitsbezug():
    with _session() as db:
        lage, _, einheit = _daten(db)
        rs.set_status(db, einheit.id, lage.id, rs.STATUS_IM_EINSATZ, author_name="EL", user_id=1)
        assert _eintrag(db, "status").einheit_id == einheit.id


def test_disponiert_hat_stellenbezug():
    with _session() as db:
        lage, site, einheit = _daten(db)
        rs.dispatch_to_site(db, einheit.id, lage.id, site.id, author_name="EL", user_id=1)
        eintrag = _eintrag(db, "disponiert")
        assert (eintrag.einheit_id, eintrag.site_id) == (einheit.id, site.id)


def test_uebernommen_hat_stellenbezug():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        ctx = _ctx(db, einheit)
        setze_einheit_status(db, ctx, dispatch, "bestaetigt", user_id=1, author_name="Funk")
        eintrag = _eintrag(db, "uebernommen")
        assert (eintrag.einheit_id, eintrag.site_id, eintrag.quelle) == (einheit.id, site.id, "funk")


def test_begonnen_wird_je_dispatch_nur_einmal_geschrieben():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        ctx = _ctx(db, einheit)
        setze_einheit_status(db, ctx, dispatch, "anfahrt", user_id=1, author_name="Funk")
        setze_einheit_status(db, ctx, dispatch, "vor_ort", user_id=1, author_name="Funk")
        db.flush()
        eintraege = db.query(LageJournalEntry).filter_by(ereignis_typ="begonnen").all()
        assert len(eintraege) == 1
        assert eintraege[0].text == "TLF: Einsatz begonnen (Stelle A)"


def test_abgeschlossen_hat_stellenbezug():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        ctx = _ctx(db, einheit, "tablet")
        setze_einheit_status(db, ctx, dispatch, "vor_ort", user_id=1, author_name="Tablet")
        setze_einheit_status(db, ctx, dispatch, "abgeschlossen", user_id=1, author_name="Tablet")
        assert _eintrag(db, "abgeschlossen").site_id == site.id


def test_zurueckgezogen_speichert_grund_und_urheber():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        rs.withdraw_from_site(
            db, einheit.id, lage.id, site.id, author_name="EL", user_id=42,
            grund="Nicht mehr benötigt",
        )
        eintrag = _eintrag(db, "zurueckgezogen")
        assert (dispatch.withdrawn_by, dispatch.withdrawn_author, dispatch.withdrawn_grund) == (
            42, "EL", "Nicht mehr benötigt",
        )
        assert "Nicht mehr benötigt" in eintrag.text


def test_abgerueckt_hat_eigenen_ereignistyp():
    with _session() as db:
        lage, _, einheit = _daten(db)
        rs.set_status(db, einheit.id, lage.id, rs.STATUS_ABGERUECKT, author_name="EL", user_id=1)
        assert _eintrag(db, "abgerueckt").einheit_id == einheit.id


def test_ressourcenjournal_kann_nicht_geloescht_werden(client):
    x = api_daten()
    _als(client, sign_session(x["admin"]))
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        entry = LageJournalEntry(major_incident_id=x["lage"], category="ressource", text="geschützt")
        db.add(entry)
        db.commit()
        entry_id = entry.id
    finally:
        db.close()
    client.get("/lage")
    response = client.post(
        f"/lage/{x['lage']}/journal/{entry_id}/loeschen",
        data={"_csrf": client.cookies.get("ec_csrf")}, headers=_h(client),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Ressourceneintraege koennen nur storniert werden"
