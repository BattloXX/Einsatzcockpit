"""Service-Tests für Verbände und das Aufteilen von Ressourcen."""

from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitAusstattung,
    LageEinheitPerson,
    LageJournalEntry,
    MajorIncident,
    SitePhase,
)
from app.models.master import FireDept
from app.services import resource_service
from app.services import ressource_pflege_service as service
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db):
    suffix = uuid4().hex[:8]
    org = FireDept(slug=f"verband-{suffix}", name="Verband", color="#123456", bos="Feuerwehr")
    db.add(org)
    db.flush()
    lage, fremd = MajorIncident(name="Lage", org_id=org.id), MajorIncident(name="Fremd", org_id=org.id)
    db.add_all([lage, fremd])
    db.flush()
    a = LageEinheit(lage_id=lage.id, label="RLF A", status="bereitgestellt")
    b = LageEinheit(lage_id=lage.id, label="TLF B", status="im_einsatz")
    c = LageEinheit(lage_id=fremd.id, label="Fremd")
    site_a = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle A", phase=SitePhase.in_arbeit)
    site_b = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle B", phase=SitePhase.in_arbeit)
    db.add_all([a, b, c, site_a, site_b])
    db.flush()
    return lage, fremd, a, b, c, site_a, site_b


def test_verband_bilden_regeln_dispatches_und_aufloesen():
    with _session() as db:
        lage, _, a, b, c, site_a, site_b = _daten(db)
        with pytest.raises(ValueError, match="mindestens"):
            service.verband_bilden(db, lage, label="V", einheit_ids=[a.id], user_id=1, author_name="EL")
        with pytest.raises(ValueError, match="Lage"):
            service.verband_bilden(db, lage, label="V", einheit_ids=[a.id, c.id], user_id=1, author_name="EL")
        resource_service.dispatch_to_site(db, a.id, lage.id, site_a.id)
        resource_service.dispatch_to_site(db, b.id, lage.id, site_b.id)
        with pytest.raises(ValueError, match="Aktive Dispositionen"):
            service.verband_bilden(db, lage, label="V", einheit_ids=[a.id, b.id], user_id=1, author_name="EL")
        db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.einheit_id == b.id).one().withdrawn_at = service._now()
        resource_service.dispatch_to_site(db, b.id, lage.id, site_a.id)
        verband = service.verband_bilden(
            db, lage, label="Verband Nord", einheit_ids=[a.id, b.id], user_id=1, author_name="EL"
        )
        assert verband.status == "im_einsatz" and {a.verband_id, b.verband_id} == {verband.id}
        assert db.query(EinheitSiteDispatch).filter_by(einheit_id=a.id).count() == 1
        with pytest.raises(ValueError, match="Verband Nord"):
            resource_service.dispatch_to_site(db, a.id, lage.id, site_a.id)
        service.verband_aufloesen(db, lage, verband, user_id=1, author_name="EL")
        assert (a.verband_id, b.verband_id, verband.status) == (None, None, "abgerueckt")


def test_verband_summen_und_kraefte_ohne_doppelzaehlung():
    with _session() as db:
        lage, _, a, b, _, _, _ = _daten(db)
        service.personal_setzen(db, lage, a, gesamt=3, fuehrung=1, user_id=1, author_name="EL")
        service.modus_wechseln(db, lage, b, "liste", user_id=1, author_name="EL")
        service.person_hinzufuegen(db, lage, b, name="Anna", funktion="agt", user_id=1, author_name="EL")
        service.ausstattung_hinzufuegen(db, lage, a, kategorie="tauchpumpe", menge=2, user_id=1, author_name="EL")
        service.ausstattung_hinzufuegen(db, lage, b, kategorie="tauchpumpe", menge=1, user_id=1, author_name="EL")
        verband = service.verband_bilden(db, lage, label="V", einheit_ids=[a.id, b.id], user_id=1, author_name="EL")
        summen = service.verband_summen(db, verband)
        assert summen["gesamt"] == 4 and summen["agt"] == 1 and summen["ausstattung"][0]["menge"] == 3
        assert service.kraefte_summen(db, lage)["gesamt"] == 4


def test_aufteilen_summe_ausstattung_dispatch_kopie_und_atomare_grenze():
    with _session() as db:
        lage, _, a, _, _, site_a, _ = _daten(db)
        service.personal_setzen(db, lage, a, gesamt=5, user_id=1, author_name="EL")
        zeile = service.ausstattung_hinzufuegen(
            db, lage, a, kategorie="tauchpumpe", menge=2, user_id=1, author_name="EL"
        )
        dispatch = resource_service.dispatch_to_site(db, a.id, lage.id, site_a.id, auftrag="Pumpen")
        teile = service.einheit_aufteilen(db, lage, a, [
            {
                "label": "A-1",
                "anzahl": 2,
                "ausstattung": [{"zeile_id": zeile.id, "menge": 1}],
                "dispatches_kopieren": [dispatch.id],
            },
            {"label": "A-2", "anzahl": 1, "ausstattung": [{"zeile_id": zeile.id, "menge": 1}]},
        ], user_id=1, author_name="EL")
        assert a.staerke_gesamt == 2 and [x.staerke_gesamt for x in teile] == [2, 1]
        assert db.query(EinheitSiteDispatch).filter_by(einheit_id=a.id).count() == 1
        assert db.query(EinheitSiteDispatch).filter_by(einheit_id=teile[0].id).one().auftrag == "Pumpen"
        assert db.query(LageEinheitAusstattung).filter_by(einheit_id=a.id).count() == 0
        before = a.staerke_gesamt
        with pytest.raises(ValueError, match="Nicht genügend"):
            service.einheit_aufteilen(db, lage, a, [{"label": "Zu viel", "anzahl": 99}], user_id=1, author_name="EL")
        assert a.staerke_gesamt == before


def test_aufteilen_liste_verschiebt_personen_und_verbandsregeln():
    with _session() as db:
        lage, _, a, b, _, _, _ = _daten(db)
        service.modus_wechseln(db, lage, a, "liste", user_id=1, author_name="EL")
        person = service.person_hinzufuegen(db, lage, a, name="Anna", funktion="agt", user_id=1, author_name="EL")
        teil = service.einheit_aufteilen(
            db, lage, a, [{"label": "Teil", "person_ids": [person.id]}], user_id=1, author_name="EL"
        )[0]
        active = db.query(LageEinheitPerson).filter_by(lage_id=lage.id, name="Anna", bis_at=None).one()
        assert active.einheit_id == teil.id and active.herkunft == "umbuchung" and a.staerke_gesamt == 0
        verband = service.verband_bilden(db, lage, label="V", einheit_ids=[a.id, b.id], user_id=1, author_name="EL")
        with pytest.raises(ValueError, match="Verbände"):
            service.einheit_aufteilen(db, lage, verband, [{"label": "x", "anzahl": 0}], user_id=1, author_name="EL")
        with pytest.raises(ValueError, match="Teil eines"):
            service.einheit_aufteilen(db, lage, a, [{"label": "x", "person_ids": []}], user_id=1, author_name="EL")
        assert db.query(LageJournalEntry).filter(LageJournalEntry.einheit_id.in_([a.id, teil.id])).count() > 0
