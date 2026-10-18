"""Regressionstests für die extrahierte Einsatzstellen-Geschäftslogik."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, MajorIncident, Sector, SiteLogEntry, SitePhase, SitePriority
from app.routers.ui_major_incident import site_sektor_assign
from app.services import gsl_stelle_service
from tests.conftest import TestingSession


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _db():
    db = TestingSession()
    set_tenant_context(db, 1)
    return db


@pytest.mark.asyncio
async def test_stelle_anlegen_schreibt_log_und_audit(monkeypatch):
    db = _db()
    try:
        lage = MajorIncident(org_id=1, name="Testlage")
        db.add(lage)
        db.flush()
        async def ohne_geocoding(site):
            return None
        async def ohne_ki(site, db, org_id=None):
            return None
        audits = []
        monkeypatch.setattr(gsl_stelle_service, "_geocode_site", ohne_geocoding)
        monkeypatch.setattr(gsl_stelle_service, "_apply_ai_prio", ohne_ki)
        monkeypatch.setattr(gsl_stelle_service, "write_audit", lambda *args, **kwargs: audits.append((args, kwargs)))

        site = await gsl_stelle_service.stelle_anlegen(
            db, lage, bezeichnung="Stelle", ort="Wien", strasse="Hauptstraße", hausnr="1",
            user_id=7, author_name="EL",
        )
        db.flush()
        assert db.query(SiteLogEntry).filter_by(incident_site_id=site.id).one().text == "Einsatzstelle angelegt"
        assert audits[0][0][1] == "major_incident.site.created"
    finally:
        db.close()


def test_finde_adress_duplikate_normalisiert_und_ignoriert_abgeschlossene():
    db = _db()
    try:
        lage = MajorIncident(org_id=1, name="Testlage")
        db.add(lage)
        db.flush()
        offen = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="offen", ort="  WIEN ",
                            strasse="Haupt straße", hausnr=" 1 ")
        erledigt = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="erledigt", ort="wien",
                                strasse="hauptstraße", hausnr="1", phase=SitePhase.erledigt)
        db.add_all((offen, erledigt))
        db.flush()
        assert gsl_stelle_service.finde_adress_duplikate(
            db, lage, ort="wien", strasse="HAUPT STRASSE", hausnr="1",
        ) == [offen]
        assert gsl_stelle_service.finde_adress_duplikate(db, lage, ort="wien", strasse="", hausnr="1") == []
    finally:
        db.close()


def test_stelle_prio_schreibt_log_nur_bei_aenderung(monkeypatch):
    db = _db()
    try:
        lage = MajorIncident(org_id=1, name="Testlage")
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Stelle", priority=SitePriority.dringend)
        db.add(site)
        db.flush()
        monkeypatch.setattr(gsl_stelle_service.lagemeldung_service, "recompute_if_active", lambda *_: None)
        assert not gsl_stelle_service.stelle_prio_setzen(db, site, SitePriority.dringend, user_id=1, author_name="EL")
        assert db.query(SiteLogEntry).filter_by(incident_site_id=site.id).count() == 0
        assert gsl_stelle_service.stelle_prio_setzen(db, site, SitePriority.aufschiebbar, user_id=1, author_name="EL")
        db.flush()
        assert db.query(SiteLogEntry).filter_by(incident_site_id=site.id).one().text == "Priorität: Dringend → Aufschiebbar"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_abschnitt_route_liefert_404_fuer_fremden_abschnitt():
    db = _db()
    try:
        lage = MajorIncident(org_id=1, name="Lage A")
        andere_lage = MajorIncident(org_id=1, name="Lage B")
        db.add_all((lage, andere_lage))
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Stelle")
        sector = Sector(major_incident_id=andere_lage.id, name="Fremd")
        db.add_all((site, sector))
        db.flush()
        user = SimpleNamespace(id=1, org_id=1, roles=[])
        request = SimpleNamespace(state=SimpleNamespace(user=user))
        with pytest.raises(HTTPException) as exc:
            site_sektor_assign(request, lage.id, site.id, sector.id, db)
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_stelle_abschnitt_lehnt_fremden_abschnitt_ab():
    db = _db()
    try:
        lage = MajorIncident(org_id=1, name="Lage A")
        andere_lage = MajorIncident(org_id=1, name="Lage B")
        db.add_all((lage, andere_lage))
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Stelle")
        sector = Sector(major_incident_id=andere_lage.id, name="Fremd")
        db.add_all((site, sector))
        db.flush()
        with pytest.raises(ValueError, match="Abschnitt nicht gefunden"):
            gsl_stelle_service.stelle_abschnitt_setzen(db, site, sector.id)
    finally:
        db.close()
