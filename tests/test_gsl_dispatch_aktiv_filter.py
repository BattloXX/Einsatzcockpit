"""Regressionstests fuer beendete Dispositionen im GSL-Einheitenmodus."""
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    SitePhase,
)
from app.services import lagemeldung_service as lm
from app.services import resource_service as rs
from app.services.gsl_live_service import build_my_lage_queue
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


def _make_lage(db) -> MajorIncident:
    lage = MajorIncident(name="Test-Lage", org_id=1, status="active")
    db.add(lage)
    db.flush()
    return lage


def _make_site(db, lage_id: int, bezeichnung="Stelle A") -> IncidentSite:
    site = IncidentSite(
        major_incident_id=lage_id,
        org_id=1,
        bezeichnung=bezeichnung,
        phase=SitePhase.in_arbeit,
    )
    db.add(site)
    db.flush()
    return site


def _make_einheit(db, lage_id: int, label="RLF 1") -> LageEinheit:
    einheit = LageEinheit(
        lage_id=lage_id,
        label=label,
        resource_type="fahrzeug",
        status=rs.STATUS_BEREITGESTELLT,
    )
    db.add(einheit)
    db.flush()
    return einheit


def test_beendete_disposition_zaehlt_nur_als_fertig():
    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        einheit = _make_einheit(db, lage.id)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        dispatch.vor_ort_at = datetime.now(UTC)
        dispatch.einheit_status = "abgeschlossen"
        dispatch.beendet_at = datetime.now(UTC)

        assert rs.get_dispatch_counts_for_site(db, site.id) == {
            "alarmed": 0,
            "vor_ort": 0,
            "fertig": 1,
        }
        db.rollback()


def test_dispatch_erneut_nach_beendigung_aber_nicht_bei_aktiver_disposition():
    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        einheit = _make_einheit(db, lage.id)
        erste = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)

        with pytest.raises(ValueError, match="bereits für"):
            rs.dispatch_to_site(db, einheit.id, lage.id, site.id)

        erste.einheit_status = "abgeschlossen"
        erste.beendet_at = datetime.now(UTC)
        zweite = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)

        assert zweite.id != erste.id
        assert db.query(EinheitSiteDispatch).filter_by(site_id=site.id).count() == 2
        db.rollback()


def test_vor_ort_konflikt_beruecksichtigt_nur_aktive_status():
    with _session() as db:
        lage = _make_lage(db)
        site_a = _make_site(db, lage.id, "Stelle A")
        site_b = _make_site(db, lage.id, "Stelle B")
        einheit = _make_einheit(db, lage.id)
        dispatch_a = rs.dispatch_to_site(db, einheit.id, lage.id, site_a.id)
        dispatch_a.einheit_status = "in_arbeit"
        dispatch_a.vor_ort_at = datetime.now(UTC)
        rs.dispatch_to_site(db, einheit.id, lage.id, site_b.id)

        dispatch, konflikt = rs.set_vor_ort_at_site(db, einheit.id, lage.id, site_b.id)
        assert dispatch is None
        assert konflikt is not None

        dispatch_a.einheit_status = "abgeschlossen"
        dispatch_a.beendet_at = datetime.now(UTC)
        dispatch, konflikt = rs.set_vor_ort_at_site(db, einheit.id, lage.id, site_b.id)

        assert konflikt is None
        assert dispatch is not None
        db.rollback()


def test_vor_ort_setzt_status_und_statuszeitpunkt():
    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        einheit = _make_einheit(db, lage.id)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)

        result, konflikt = rs.set_vor_ort_at_site(db, einheit.id, lage.id, site.id)

        assert konflikt is None
        assert result is dispatch
        assert dispatch.einheit_status == "vor_ort"
        assert dispatch.status_at is not None
        db.rollback()


def test_abziehen_erhoeht_version_und_setzt_aenderungszeitpunkt():
    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        einheit = _make_einheit(db, lage.id)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        vorherige_version = dispatch.version

        rs.withdraw_from_site(db, einheit.id, lage.id, site.id)

        assert dispatch.version == vorherige_version + 1
        assert dispatch.geaendert_at is not None
        db.rollback()


def test_lagemeldung_und_live_warteschlange_ignorieren_beendete_disposition():
    class DeviceToken:
        vehicle_master_id = 987654

    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        einheit = _make_einheit(db, lage.id)
        einheit.status = rs.STATUS_IM_EINSATZ
        einheit.vehicle_id = DeviceToken.vehicle_master_id
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        dispatch.vor_ort_at = datetime.now(UTC)
        dispatch.einheit_status = "abgeschlossen"
        dispatch.beendet_at = datetime.now(UTC)
        db.flush()

        assert lm.has_active_resource(site, db) is False
        assert build_my_lage_queue(db, DeviceToken()) is None
        db.rollback()


def test_leere_einsatzstelle_hat_auch_fertig_zaehler():
    with _session() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)

        assert rs.get_dispatch_counts_for_sites(db, [site.id]) == {
            site.id: {"alarmed": 0, "vor_ort": 0, "fertig": 0},
        }
        db.rollback()
