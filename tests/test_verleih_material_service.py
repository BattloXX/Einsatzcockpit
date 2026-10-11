"""P1-Geschaeftslogik fuer Material an Einsatzstellen."""
from __future__ import annotations

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, LageEinheit, MajorIncident, SiteLogEntry
from app.models.master import OrgSettings
from app.models.verleih import VerleihArtikel, VerleihAusleihe, VerleihStatus
from app.services import verleih_service as service
from tests.test_gsl_sektoren import _user_lage


def _context(suffix: str):
    lage_id, org_id = _user_lage(f"material_{suffix}", f"material-{suffix}")
    db = SessionLocal()
    set_tenant_context(db, None)
    lage = db.get(MajorIncident, lage_id)
    assert lage is not None
    site = IncidentSite(
        major_incident_id=lage_id,
        org_id=org_id,
        bezeichnung="Keller Musterhaus",
        strasse="Hauptstraße",
        hausnr="7",
        ort="Musterort",
    )
    db.add(site)
    db.commit()
    return db, lage, site


def test_material_an_einsatzstelle_uses_fallbacks_and_creates_log(setup_db):
    db, lage, site = _context("fallback")
    try:
        ausleihe = service.material_an_einsatzstelle(
            db,
            lage=lage,
            site=site,
            positionen=[{"bezeichnung": "Tauchpumpe", "menge": 2}, {"bezeichnung": "Sandsack", "menge": 1}],
            name="",
            telefon=None,
            adresse="",
            notizen=None,
            einheit=None,
            quelle="board",
            user_id=None,
            author_name="Einsatzleitung",
        )
        assert ausleihe.name == "Vor Ort – Keller Musterhaus"
        assert ausleihe.adresse == "Hauptstraße 7, Musterort"
        assert ausleihe.quelle == "board"
        log = db.query(SiteLogEntry).filter_by(incident_site_id=site.id).one()
        assert log.kind == "massnahmen"
        assert log.text == "Material belassen: 2× Tauchpumpe, Sandsack (Empfänger: Vor Ort – Keller Musterhaus)"
    finally:
        db.close()


def test_material_catalog_rule_and_site_validation(setup_db):
    db, lage, site = _context("catalog")
    try:
        artikel = VerleihArtikel(org_id=lage.org_id, bezeichnung="Eigene Pumpe")
        foreign = LageEinheit(lage_id=lage.id, label="GK", is_from_org=False)
        own = LageEinheit(lage_id=lage.id, label="RLF", is_from_org=True)
        db.add_all([artikel, foreign, own])
        db.commit()
        position = [{"bezeichnung": "Pumpe", "artikel_id": artikel.id, "menge": 10000}]
        fremd = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=position, name="A", telefon=None, adresse=None,
            notizen=None, einheit=foreign, quelle="einheit", user_id=None, author_name=None,
        )
        own_loan = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=position, name="B", telefon=None, adresse=None,
            notizen=None, einheit=own, quelle="einheit", user_id=None, author_name=None,
        )
        board = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=position, name="C", telefon=None, adresse=None,
            notizen=None, einheit=None, quelle="board", user_id=None, author_name=None,
        )
        assert fremd.positionen[0].artikel_id is None
        assert own_loan.positionen[0].artikel_id == artikel.id
        assert board.positionen[0].artikel_id == artikel.id
        assert fremd.positionen[0].menge == 9999
        other_lage_id, other_org_id = _user_lage("material_other", "material-other")
        other_site = IncidentSite(major_incident_id=other_lage_id, org_id=other_org_id, bezeichnung="Fremd")
        fremd_artikel = VerleihArtikel(org_id=other_org_id, bezeichnung="Fremde Pumpe")
        db.add_all([other_site, fremd_artikel])
        db.commit()
        org_fremd = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=[{"bezeichnung": "Pumpe", "artikel_id": fremd_artikel.id}],
            name="D", telefon=None, adresse=None, notizen=None, einheit=None, quelle="board",
            user_id=None, author_name=None,
        )
        assert org_fremd.positionen[0].artikel_id is None
        with pytest.raises(ValueError, match="gehoert nicht"):
            service.material_an_einsatzstelle(
                db, lage=lage, site=other_site, positionen=position, name="X", telefon=None, adresse=None,
                notizen=None, einheit=None, quelle="board", user_id=None, author_name=None,
            )
    finally:
        db.close()


def test_material_zuruecknehmen_creates_log(setup_db):
    db, lage, site = _context("return")
    try:
        ausleihe = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=[{"bezeichnung": "Pumpe"}], name="A", telefon=None,
            adresse=None, notizen=None, einheit=None, quelle="board", user_id=1, author_name="Autor",
        )
        result = service.material_zuruecknehmen(db, ausleihe, author_name="Rücknahme", user_id=2, einheit_id=None)
        assert result.status == VerleihStatus.zurueckgegeben
        assert db.query(SiteLogEntry).filter(SiteLogEntry.text == "Material zurückgenommen: Pumpe").one()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_sms_uses_stelle_and_logs_result(setup_db, monkeypatch):
    db, lage, site = _context("sms")
    try:
        settings = OrgSettings(org_id=lage.org_id, gsl_verleih_sms_ausleih_text="{stelle}: {name}")
        db.add(settings)
        db.commit()
        ausleihe = service.material_an_einsatzstelle(
            db, lage=lage, site=site, positionen=[{"bezeichnung": "Pumpe"}], name="Mia", telefon="0664123",
            adresse=None, notizen=None, einheit=None, quelle="board", user_id=None, author_name=None, pin="123456",
        )
        assert service.get_sms_ausleih_text(db, lage.org_id, ausleihe) == "Keller Musterhaus: Mia"

        async def fake_send_sms(org_id, telefon, text):
            assert (org_id, telefon, text) == (lage.org_id, "0664123", "Keller Musterhaus: Mia PIN: 123456")
            return True

        monkeypatch.setattr("app.services.sms_service.send_sms", fake_send_sms)
        assert await service.sende_material_sms(ausleihe.id, lage.org_id, True) is True
        db.expire_all()
        assert db.get(VerleihAusleihe, ausleihe.id).sms_ausleih_gesendet is True
        assert db.query(SiteLogEntry).filter(SiteLogEntry.incident_site_id == site.id, SiteLogEntry.text == "SMS an Empfänger gesendet").one()
        db.get(VerleihAusleihe, ausleihe.id).telefon = None
        db.commit()
        assert await service.sende_material_sms(ausleihe.id, lage.org_id, False) is False
    finally:
        db.close()
