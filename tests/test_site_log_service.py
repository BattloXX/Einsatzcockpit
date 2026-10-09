"""Tests für die gemeinsame Chronik-Logik von Einsatzstellen."""
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, MajorIncident, SitePhase, SiteResourceAssignment
from app.models.master import FireDept, OrgSettings
from app.services.site_log_service import add_site_log, format_lagemeldung, normalisiere_user_kind
from tests.conftest import TestingSession


def _db():
    db = TestingSession()
    set_tenant_context(db, None)
    return db


def _site(db):
    org = FireDept(slug="site-log-service", name="Site Log", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    lage = MajorIncident(org_id=org.id, name="Testlage")
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Teststelle", phase=SitePhase.in_arbeit)
    db.add(site)
    db.flush()
    return org, site


def test_add_site_log_setzt_felder_und_lagemeldung_timer_zurueck():
    db = _db()
    try:
        org, site = _site(db)
        settings = OrgSettings(org_id=org.id)
        db.add(settings)
        db.flush()
        settings.gsl_lagemeldung_interval_minutes = 60
        db.add(SiteResourceAssignment(incident_site_id=site.id, resource_type="free_text", label="RLF"))
        erfasst_at = datetime(2026, 10, 9, 12, 30, tzinfo=UTC).replace(tzinfo=None)

        entry = add_site_log(
            db, site, "lagemeldung", "  Lage stabil  ", user_id=17, author_name="Musterfrau",
            einheit_id=3, erfasst_at=erfasst_at,
        )
        db.flush()

        assert entry.text == "Lage stabil"
        assert entry.einheit_id == 3
        assert entry.erfasst_at == erfasst_at
        assert site.naechste_lagemeldung_at is not None
    finally:
        db.rollback()
        db.close()


def test_normalisiere_user_kind():
    assert normalisiere_user_kind("lagemeldung") == "lagemeldung"
    assert normalisiere_user_kind("massnahmen") == "massnahmen"
    assert normalisiere_user_kind("status") == "note"


def test_format_lagemeldung_ordnet_alle_felder():
    assert format_lagemeldung({
        "lage": "Wasser steigt", "gefahren": "Strom", "massnahmen": "Pumpe gesetzt",
        "fortschritt": "Keller leer", "freitext": "Nachkontrolle folgt",
    }) == (
        "Lage vor Ort: Wasser steigt\nFestgestellte Gefahren: Strom\n"
        "Durchgeführte Maßnahmen: Pumpe gesetzt\nAktueller Fortschritt: Keller leer\nNachkontrolle folgt"
    )


def test_format_lagemeldung_nur_freitext_und_leere_felder():
    assert format_lagemeldung({"freitext": "  Kurze Meldung  "}) == "Kurze Meldung"
    assert format_lagemeldung({"lage": " ", "gefahren": None, "freitext": "\t"}) == ""
