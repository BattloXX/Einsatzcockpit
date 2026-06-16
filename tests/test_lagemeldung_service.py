"""Tests für den GSL-Lagemeldungs-Regelkreis (lagemeldung_service)."""
from types import SimpleNamespace

from app.models.major_incident import (
    AUTO_KIND_LAGEMELDUNG,
    CommLogEntry,
    IncidentSite,
    MajorIncident,
    SitePhase,
    SitePriority,
    SiteResourceAssignment,
)
from app.services import lagemeldung_service as lm
from tests.conftest import TestingSession


# ── Reine Logik (ohne DB) ─────────────────────────────────────────────────────

def test_interval_off_when_default_empty():
    site = SimpleNamespace(priority=None)
    org = SimpleNamespace(gsl_lagemeldung_interval_minutes=None,
                          gsl_lagemeldung_interval_sofort_minutes=30)
    assert lm.interval_minutes_for(site, org) is None


def test_interval_default_used_for_normal_prio():
    site = SimpleNamespace(priority=SitePriority.normal)
    org = SimpleNamespace(gsl_lagemeldung_interval_minutes=60,
                          gsl_lagemeldung_interval_sofort_minutes=30)
    assert lm.interval_minutes_for(site, org) == 60


def test_interval_sofort_override():
    site = SimpleNamespace(priority=SitePriority.sofort)
    org = SimpleNamespace(gsl_lagemeldung_interval_minutes=60,
                          gsl_lagemeldung_interval_sofort_minutes=30)
    assert lm.interval_minutes_for(site, org) == 30


def test_interval_sofort_falls_back_to_default_when_empty():
    site = SimpleNamespace(priority=SitePriority.sofort)
    org = SimpleNamespace(gsl_lagemeldung_interval_minutes=60,
                          gsl_lagemeldung_interval_sofort_minutes=None)
    assert lm.interval_minutes_for(site, org) == 60


def test_interval_none_org():
    site = SimpleNamespace(priority=None)
    assert lm.interval_minutes_for(site, None) is None


def test_recompute_due_sets_and_clears():
    site = SimpleNamespace(priority=SitePriority.normal, naechste_lagemeldung_at="x")
    org = SimpleNamespace(gsl_lagemeldung_interval_minutes=60,
                          gsl_lagemeldung_interval_sofort_minutes=30)
    lm.recompute_due(site, org)
    assert site.naechste_lagemeldung_at is not None and site.naechste_lagemeldung_at != "x"
    # Intervall aus ⇒ None
    org2 = SimpleNamespace(gsl_lagemeldung_interval_minutes=None,
                           gsl_lagemeldung_interval_sofort_minutes=None)
    lm.recompute_due(site, org2)
    assert site.naechste_lagemeldung_at is None


# ── DB-gestützt ───────────────────────────────────────────────────────────────

def _set_interval(db, minutes):
    """Setzt das Org-Intervall für org_id=1 zuverlässig (auch auf NULL).

    Wichtig: Der Python-Default (60) greift beim INSERT auch bei explizitem None,
    daher erst die Zeile per Flush anlegen und danach per UPDATE auf None setzen.
    """
    from app.models.master import OrgSettings
    os_row = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    if not os_row:
        os_row = OrgSettings(org_id=1)
        db.add(os_row)
        db.flush()  # INSERT mit Default
    os_row.gsl_lagemeldung_interval_minutes = minutes
    db.flush()      # UPDATE schreibt auch None korrekt als NULL
    return os_row


def _mk_lage_site(db, *, phase=SitePhase.in_arbeit, with_resource=True):
    lage = MajorIncident(org_id=1, name="Testlage")
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Teststelle", phase=phase)
    db.add(site)
    db.flush()
    if with_resource:
        db.add(SiteResourceAssignment(incident_site_id=site.id, resource_type="free_text", label="RLF"))
        db.flush()
    return lage, site


def test_ensure_timer_sets_when_configured():
    db = TestingSession()
    try:
        _set_interval(db, 60)

        lage, site = _mk_lage_site(db)
        assert lm.ensure_timer(site, db) is True
        assert site.naechste_lagemeldung_at is not None

        # Erneuter Aufruf setzt nicht erneut (bereits gesetzt)
        assert lm.ensure_timer(site, db) is False
    finally:
        db.rollback()
        db.close()


def test_ensure_timer_off_when_interval_empty():
    db = TestingSession()
    try:
        _set_interval(db, None)

        lage, site = _mk_lage_site(db)
        assert lm.ensure_timer(site, db) is False
        assert site.naechste_lagemeldung_at is None
    finally:
        db.rollback()
        db.close()


def test_register_lagemeldung_closes_open_auto_auftrag():
    db = TestingSession()
    try:
        _set_interval(db, 60)

        lage, site = _mk_lage_site(db)
        auftrag = CommLogEntry(
            major_incident_id=lage.id, related_site_id=site.id, direction="int",
            is_request=True, auto_kind=AUTO_KIND_LAGEMELDUNG,
            message="Lagemeldung anfordern", handled=False,
        )
        db.add(auftrag)
        db.flush()

        lm.register_lagemeldung(site, db)
        db.flush()
        assert auftrag.handled is True
        assert site.naechste_lagemeldung_at is not None
    finally:
        db.rollback()
        db.close()


def test_clear_timer_closes_auftrag_and_resets():
    db = TestingSession()
    try:
        lage, site = _mk_lage_site(db)
        site.naechste_lagemeldung_at = lm._now()
        auftrag = CommLogEntry(
            major_incident_id=lage.id, related_site_id=site.id, direction="int",
            is_request=True, auto_kind=AUTO_KIND_LAGEMELDUNG,
            message="Lagemeldung anfordern", handled=False,
        )
        db.add(auftrag)
        db.flush()

        lm.clear_timer(site, db)
        db.flush()
        assert site.naechste_lagemeldung_at is None
        assert auftrag.handled is True
    finally:
        db.rollback()
        db.close()
