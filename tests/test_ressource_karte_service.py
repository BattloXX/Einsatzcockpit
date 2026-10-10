"""Lesesicht und manuelle Eintraege der Ressourcenkarte."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    LageJournalEntry,
    MajorIncident,
    SiteLogEntry,
)
from app.services import resource_service as rs
from app.services import ressource_karte_service as karte_service
from tests.conftest import TestingSession


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
    now = datetime.now(UTC).replace(tzinfo=None)
    lage = MajorIncident(name="Karten-Test", org_id=1, started_at=now)
    db.add(lage)
    db.flush()
    sites = [
        IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="A", external_key="EXT-A"),
        IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="B"),
        IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="C"),
        IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="D"),
    ]
    einheit = LageEinheit(lage_id=lage.id, label="TLF", resource_type="fahrzeug", added_at=now)
    andere = LageEinheit(lage_id=lage.id, label="Andere", resource_type="fahrzeug", added_at=now)
    db.add_all([*sites, einheit, andere])
    db.flush()
    return now, lage, sites, einheit, andere


def test_einsaetze_alle_kategorien_dauer_und_nummern():
    with _session() as db:
        now, lage, sites, einheit, _ = _daten(db)
        incident = Incident(primary_org_id=1, lis_operation_number="LIS-7")
        db.add(incident)
        db.flush()
        sites[0].incident_id = incident.id
        sites[2].external_key = "EXT-C"
        dispatches = [
            EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=now - timedelta(hours=1))
            for site in sites
        ]
        dispatches[0].einheit_status = "vor_ort"
        dispatches[0].vor_ort_at = now - timedelta(minutes=50)
        einheit.incident_site_id = sites[0].id
        dispatches[1].beendet_at = now
        dispatches[1].vor_ort_at = now - timedelta(minutes=30)
        dispatches[1].beendet_grund = "fertig"
        dispatches[2].withdrawn_at = now
        dispatches[2].withdrawn_grund = "anderweitig gebraucht"
        dispatches[2].withdrawn_author = "EL"
        db.add_all(dispatches)
        db.flush()
        db.add_all(
            [
                SiteLogEntry(
                    incident_site_id=sites[1].id, einheit_id=einheit.id, ts=now, kind="lagemeldung", text="Lage"
                ),
                SiteLogEntry(
                    incident_site_id=sites[1].id, einheit_id=einheit.id, ts=now, kind="massnahmen", text="Maßnahme"
                ),
            ]
        )
        db.flush()
        result = karte_service.einsaetze(db, lage, einheit)
        assert result["aktuell"]["einsatznummer"] == "LIS-7"
        assert result["weitere"][0]["einsatznummer"] == "S4"
        assert result["abgeschlossen"][0]["dauer_sekunden"] == 1800
        assert result["abgeschlossen"][0]["letzte_lagemeldung"]["text"] == "Lage"
        assert result["abgeschlossen"][0]["massnahmen"][0]["text"] == "Maßnahme"
        assert result["zurueckgezogen"][0]["withdrawn_author"] == "EL"
        assert result["zurueckgezogen"][0]["einsatznummer"] == "EXT-C"


def test_journal_merge_filter_paging_storno_und_einheitsgrenze():
    with _session() as db:
        now, lage, sites, einheit, andere = _daten(db)
        dispatch = EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=sites[0].id, dispatched_at=now - timedelta(minutes=2)
        )
        real = LageJournalEntry(
            major_incident_id=lage.id,
            einheit_id=einheit.id,
            site_id=sites[0].id,
            category="ressource",
            ereignis_typ="disponiert",
            text="Echt",
            ts=now,
            storniert_at=now,
            storno_grund="Fehler",
        )
        fremd = SiteLogEntry(incident_site_id=sites[0].id, einheit_id=andere.id, ts=now, kind="note", text="fremd")
        log = SiteLogEntry(
            incident_site_id=sites[0].id,
            einheit_id=einheit.id,
            ts=now - timedelta(minutes=1),
            kind="note",
            text="Notiz",
        )
        db.add_all((dispatch, real, fremd, log))
        db.flush()
        zeilen = karte_service.journal(db, lage, einheit)
        assert [z.text for z in zeilen].count("Disposition erteilt") == 0
        assert any(z.storniert and z.storno_grund == "Fehler" for z in zeilen)
        assert "fremd" not in [z.text for z in zeilen]
        assert [z.text for z in karte_service.journal(db, lage, einheit, typen={"meldung"})] == ["Notiz"]
        assert karte_service.journal(db, lage, einheit, site_id=sites[1].id) == []
        assert all(z.ts < now for z in karte_service.journal(db, lage, einheit, vor_ts=now))
        assert len(karte_service.journal(db, lage, einheit, limit=1)) == 1


def test_kommunikation_karte_und_manueller_storno():
    with _session() as db:
        now, lage, sites, einheit, _ = _daten(db)
        einheit.status_at = now
        einheit.bereitstellungsraum = "Wartezone"
        dispatch = EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=sites[0].id, dispatched_at=now - timedelta(minutes=5)
        )
        bestaetigt = EinheitSiteDispatch(
            einheit_id=einheit.id,
            site_id=sites[1].id,
            dispatched_at=now - timedelta(minutes=4),
            bestaetigt_at=now - timedelta(minutes=3),
            einheit_status="bestaetigt",
        )
        leader = LageEinheitLeader(einheit_id=einheit.id, person_name="GK", start_at=now, rolle="fuehrer")
        stv = LageEinheitLeader(einheit_id=einheit.id, person_name="Stv", start_at=now, rolle="stellvertreter")
        db.add_all((dispatch, bestaetigt, leader, stv))
        db.flush()
        einheit.leader_assignment_id = leader.id
        db.add(
            SiteLogEntry(
                incident_site_id=sites[0].id, einheit_id=einheit.id, ts=now, kind="lagemeldung", text="Alles OK"
            )
        )
        db.flush()
        comm = karte_service.kommunikation(db, lage, einheit)
        assert comm["nicht_quittierte_auftraege"]["anzahl"] == 1
        assert comm["uebernommene_auftraege"] == 1
        assert comm["letzte_lagemeldung"]["text"] == "Alles OK"
        card = karte_service.karte(db, lage, einheit)
        assert card["allgemein"]["standort"] == "Wartezone"
        assert card["gruppenkommandant"]["current"]["name"] == "GK"
        assert card["gruppenkommandant"]["stellvertreter"]["name"] == "Stv"
        entry = rs.journal_eintrag_manuell(
            db, lage, einheit, text="  Hinweis  ", site_id=sites[0].id, user_id=7, author_name="EL"
        )
        assert entry.text == "Hinweis" and entry.ereignis_typ == "manuell"
        assert (
            rs.journal_eintrag_stornieren(db, lage, einheit, entry.id, grund="Korrektur", user_id=8, author_name="EL")
            is entry
        )
        with pytest.raises(ValueError, match="bereits"):
            rs.journal_eintrag_stornieren(db, lage, einheit, entry.id, grund="nochmal", user_id=8, author_name="EL")
        with pytest.raises(ValueError):
            rs.journal_eintrag_manuell(db, lage, einheit, text=" ", user_id=7, author_name="EL")
        with pytest.raises(ValueError):
            rs.journal_eintrag_manuell(db, lage, einheit, text="x", site_id=999999, user_id=7, author_name="EL")
