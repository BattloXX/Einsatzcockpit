"""Tests für Mehrfach-Disposition von Einheiten (resource_service + lagemeldung_service)."""
import pytest

from app.db import Base
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    SitePhase,
    SiteResourceAssignment,
)
from app.services import resource_service as rs
from app.services import lagemeldung_service as lm
from tests.conftest import TestingSession, engine


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def _make_lage(db) -> MajorIncident:
    lage = MajorIncident(name="Test-Lage", org_id=1, status="active")
    db.add(lage)
    db.flush()
    return lage


def _make_site(db, lage_id, bezeichnung="Stelle A") -> IncidentSite:
    site = IncidentSite(
        major_incident_id=lage_id,
        org_id=1,
        bezeichnung=bezeichnung,
        phase=SitePhase.in_arbeit,
    )
    db.add(site)
    db.flush()
    return site


def _make_einheit(db, lage_id, label="RLF 1") -> LageEinheit:
    e = LageEinheit(lage_id=lage_id, label=label, resource_type="fahrzeug",
                    status=rs.STATUS_BEREITGESTELLT)
    db.add(e)
    db.flush()
    return e


# ── dispatch_to_site ──────────────────────────────────────────────────────────

def test_dispatch_to_site_creates_record():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        d = rs.dispatch_to_site(db, e.id, lage.id, site.id)

        assert d.id is not None
        assert d.vor_ort_at is None
        assert d.withdrawn_at is None
        assert e.status == rs.STATUS_IM_EINSATZ
        db.rollback()


def test_dispatch_duplicate_raises_value_error():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)
        rs.dispatch_to_site(db, e.id, lage.id, site.id)

        with pytest.raises(ValueError, match="bereits für"):
            rs.dispatch_to_site(db, e.id, lage.id, site.id)
        db.rollback()


# ── set_vor_ort_at_site ───────────────────────────────────────────────────────

def test_set_vor_ort_no_conflict_sets_timestamps():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)
        rs.dispatch_to_site(db, e.id, lage.id, site.id)

        dispatch, conflict = rs.set_vor_ort_at_site(db, e.id, lage.id, site.id)

        assert conflict is None
        assert dispatch.vor_ort_at is not None
        assert e.incident_site_id == site.id
        db.rollback()


def test_set_vor_ort_returns_conflict_when_other_site_has_vor_ort():
    with TestingSession() as db:
        lage = _make_lage(db)
        site_a = _make_site(db, lage.id, "Stelle A")
        site_b = _make_site(db, lage.id, "Stelle B")
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site_a.id)
        rs.set_vor_ort_at_site(db, e.id, lage.id, site_a.id)
        rs.dispatch_to_site(db, e.id, lage.id, site_b.id)

        dispatch, conflict = rs.set_vor_ort_at_site(db, e.id, lage.id, site_b.id)

        assert dispatch is None
        assert conflict is not None
        assert conflict.site_id == site_a.id
        db.rollback()


def test_set_vor_ort_no_conflict_when_other_site_only_alarmed():
    with TestingSession() as db:
        lage = _make_lage(db)
        site_a = _make_site(db, lage.id, "Stelle A")
        site_b = _make_site(db, lage.id, "Stelle B")
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site_a.id)
        # Stelle A: nur disponiert (vor_ort_at bleibt NULL)
        rs.dispatch_to_site(db, e.id, lage.id, site_b.id)

        dispatch, conflict = rs.set_vor_ort_at_site(db, e.id, lage.id, site_b.id)

        assert conflict is None
        assert dispatch.vor_ort_at is not None
        db.rollback()


# ── resolve_vor_ort_conflict ──────────────────────────────────────────────────

def test_resolve_conflict_withdraws_old_and_sets_new():
    with TestingSession() as db:
        lage = _make_lage(db)
        site_a = _make_site(db, lage.id, "Stelle A")
        site_b = _make_site(db, lage.id, "Stelle B")
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site_a.id)
        rs.set_vor_ort_at_site(db, e.id, lage.id, site_a.id)
        rs.dispatch_to_site(db, e.id, lage.id, site_b.id)

        result = rs.resolve_vor_ort_conflict(db, e.id, lage.id, site_b.id)

        # Alte Stelle A abgezogen
        old = db.query(EinheitSiteDispatch).filter_by(site_id=site_a.id).first()
        assert old.withdrawn_at is not None

        # Neue Stelle B vor Ort
        assert result.vor_ort_at is not None
        assert e.incident_site_id == site_b.id
        db.rollback()


# ── withdraw_from_site ────────────────────────────────────────────────────────

def test_withdraw_from_site_sets_withdrawn_at_and_clears_incident_site_id():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site.id)
        rs.set_vor_ort_at_site(db, e.id, lage.id, site.id)
        assert e.incident_site_id == site.id

        rs.withdraw_from_site(db, e.id, lage.id, site.id)

        dispatch = db.query(EinheitSiteDispatch).filter_by(site_id=site.id).first()
        assert dispatch.withdrawn_at is not None
        assert e.incident_site_id is None
        db.rollback()


def test_withdraw_raises_when_no_active_dispatch():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        with pytest.raises(ValueError):
            rs.withdraw_from_site(db, e.id, lage.id, site.id)
        db.rollback()


# ── get_active_dispatches_for_site ────────────────────────────────────────────

def test_get_active_dispatches_excludes_withdrawn():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e1 = _make_einheit(db, lage.id, "E1")
        e2 = _make_einheit(db, lage.id, "E2")

        rs.dispatch_to_site(db, e1.id, lage.id, site.id)
        rs.dispatch_to_site(db, e2.id, lage.id, site.id)
        rs.withdraw_from_site(db, e1.id, lage.id, site.id)

        result = rs.get_active_dispatches_for_site(db, site.id)
        assert len(result) == 1
        assert result[0].einheit_id == e2.id
        db.rollback()


# ── get_dispatch_counts_for_site ──────────────────────────────────────────────

def test_get_dispatch_counts_for_site():
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e1 = _make_einheit(db, lage.id, "E1")
        e2 = _make_einheit(db, lage.id, "E2")

        rs.dispatch_to_site(db, e1.id, lage.id, site.id)
        rs.dispatch_to_site(db, e2.id, lage.id, site.id)
        rs.set_vor_ort_at_site(db, e1.id, lage.id, site.id)

        counts = rs.get_dispatch_counts_for_site(db, site.id)
        assert counts["vor_ort"] == 1
        assert counts["alarmed"] == 1
        db.rollback()


# ── lagemeldung_service.has_active_resource ───────────────────────────────────

def test_has_active_resource_only_alarmed_does_not_trigger():
    """Nur disponiert (vor_ort_at=NULL) → Timer läuft NICHT."""
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site.id)
        db.flush()

        assert lm.has_active_resource(site, db) is False
        db.rollback()


def test_has_active_resource_vor_ort_triggers():
    """Vor Ort gesetzt → Timer läuft."""
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site.id)
        rs.set_vor_ort_at_site(db, e.id, lage.id, site.id)
        db.flush()

        assert lm.has_active_resource(site, db) is True
        db.rollback()


def test_has_active_resource_withdrawn_does_not_trigger():
    """Nach Abzug → kein aktiver Timer."""
    with TestingSession() as db:
        lage = _make_lage(db)
        site = _make_site(db, lage.id)
        e = _make_einheit(db, lage.id)

        rs.dispatch_to_site(db, e.id, lage.id, site.id)
        rs.set_vor_ort_at_site(db, e.id, lage.id, site.id)
        rs.withdraw_from_site(db, e.id, lage.id, site.id)
        db.flush()

        assert lm.has_active_resource(site, db) is False
        db.rollback()
