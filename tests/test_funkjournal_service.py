"""Tests für die gemeinsame Funkjournal-Logik."""
import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import CommLogEntry, IncidentSite, MajorIncident, SiteLogEntry
from app.models.master import FireDept
from app.services.funkjournal_service import add_comm_entry, toggle_handled
from tests.conftest import TestingSession


def _db():
    db = TestingSession()
    set_tenant_context(db, None)
    return db


def _lage_mit_site(db, slug="funkjournal-service"):
    org = FireDept(slug=slug, name=slug, color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    lage = MajorIncident(org_id=org.id, name="Testlage")
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Teststelle")
    db.add(site)
    db.flush()
    return lage, site


def test_add_comm_entry_spiegelt_in_site_log():
    db = _db()
    try:
        lage, site = _lage_mit_site(db)
        entry = add_comm_entry(
            db, lage, direction="in", channel="  K1 ", partner="  Florian  ", message="  Lage stabil ",
            is_request=True, related_site_id=site.id, user_id=11, author_name="Musterfrau",
        )
        db.flush()
        log = db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id == site.id).one()
        assert entry.channel == "K1"
        assert entry.partner == "Florian"
        assert entry.message == "Lage stabil"
        assert entry.is_request is True
        assert log.kind == "note"
        assert log.text == "Funkjournal (↓ Eingehend) – Kanal: K1 – Von/An: Florian – Lage stabil"
    finally:
        db.rollback()
        db.close()


def test_add_comm_entry_lehnt_ungueltige_richtung_ab():
    db = _db()
    try:
        lage, _ = _lage_mit_site(db)
        with pytest.raises(ValueError, match="Ungültige Richtung"):
            add_comm_entry(db, lage, direction="quer", message="Test", user_id=None, author_name=None)
    finally:
        db.rollback()
        db.close()


def test_add_comm_entry_lehnt_fremde_einsatzstelle_ab():
    db = _db()
    try:
        lage, _ = _lage_mit_site(db, "funkjournal-a")
        _, fremde_site = _lage_mit_site(db, "funkjournal-b")
        with pytest.raises(ValueError, match="gehört nicht zur Lage"):
            add_comm_entry(
                db, lage, direction="out", message="Test", user_id=None, author_name=None,
                related_site_id=fremde_site.id,
            )
    finally:
        db.rollback()
        db.close()


def test_toggle_handled():
    db = _db()
    try:
        lage, _ = _lage_mit_site(db)
        entry = CommLogEntry(major_incident_id=lage.id, direction="in", message="Test")
        db.add(entry)
        db.flush()
        assert toggle_handled(db, lage, entry.id).handled is True
        assert toggle_handled(db, lage, entry.id).handled is False
        with pytest.raises(LookupError):
            toggle_handled(db, lage, entry.id + 999)
    finally:
        db.rollback()
        db.close()
