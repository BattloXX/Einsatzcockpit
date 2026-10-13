"""Druckansicht einer GSL-Einsatzstelle zeigt die Disposition zum Druckzeitpunkt."""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    MajorIncident,
    Sector,
)
from app.services.print_artifact_service import render_map_html
from tests.test_gsl_material_board import _material_lage
from tests.test_gsl_sektoren import _login


def _add_dispatches(lage_id, org_id, site_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        active_unit = LageEinheit(lage_id=lage_id, label="RLF Druck 1")
        ended_unit = LageEinheit(lage_id=lage_id, label="TLF Zurückgezogen")
        db.add_all([active_unit, ended_unit])
        db.flush()
        db.add(LageEinheitLeader(
            einheit_id=active_unit.id, person_name="GK Druck", start_at=datetime.now(UTC)
        ))
        db.add_all([
            EinheitSiteDispatch(
                einheit_id=active_unit.id, site_id=site_id, dispatched_at=datetime.now(UTC),
                auftragsart="erkundung", auftrag="Keller erkunden und Rückmeldung geben.",
            ),
            EinheitSiteDispatch(
                einheit_id=ended_unit.id, site_id=site_id, dispatched_at=datetime.now(UTC),
                withdrawn_at=datetime.now(UTC), withdrawn_grund="Anderer Einsatz",
            ),
        ])
        db.commit()
    finally:
        db.close()


def test_site_druck_zeigt_aktive_und_zurueckgezogene_dispatches(client, setup_db):
    lage_id, org_id, site_id = _material_lage("site_druck_dispatch")
    _add_dispatches(lage_id, org_id, site_id)
    _login(client, "site_druck_dispatch")

    response = client.get(f"/lage/{lage_id}/stellen/{site_id}/druck")

    assert response.status_code == 200
    assert "RLF Druck 1" in response.text
    assert "GK Druck" in response.text
    assert "Erkundung" in response.text
    assert "Keller erkunden und Rückmeldung geben." in response.text
    assert "Beendet / zurückgezogen" in response.text
    assert "TLF Zurückgezogen" in response.text
    assert response.text.index("RLF Druck 1") < response.text.index("Beendet / zurückgezogen")


def test_site_druck_zeigt_abschnittsleiter_und_leere_disposition(client, setup_db):
    lage_id, org_id, site_id = _material_lage("site_druck_leer")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sector = Sector(major_incident_id=lage_id, name="Abschnitt Nord", leader_label="OBI Abschnitt")
        db.add(sector)
        db.flush()
        db.get(IncidentSite, site_id).sector_id = sector.id
        db.commit()
    finally:
        db.close()
    _login(client, "site_druck_leer")

    response = client.get(f"/lage/{lage_id}/stellen/{site_id}/druck")

    assert response.status_code == 200
    assert "Abschnitt Nord" in response.text
    assert "Abschnittsleiter: <strong>OBI Abschnitt</strong>" in response.text
    assert "Noch keine Einheit disponiert" in response.text


def test_site_druck_context_und_gateway_sind_request_frei(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=1, name="Druck-Gateway-Lage", status="active")
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Gateway-Stelle")
        unit = LageEinheit(lage_id=lage.id, label="KDO Gateway")
        db.add_all([site, unit])
        db.flush()
        db.add(EinheitSiteDispatch(
            einheit_id=unit.id, site_id=site.id, dispatched_at=datetime.now(UTC),
            auftrag="Gateway-Auftrag",
        ))
        db.commit()

        from app.routers.ui_major_incident import build_site_druck_context

        context = build_site_druck_context(db, lage, site)
        assert context["active_dispatches"][0].einheit.label == "KDO Gateway"
        assert context["gedruckt_at"].tzinfo is None
        html = render_map_html(db, SimpleNamespace(
            document_type="site_karte", gsl_id=lage.id, org_id=1, artifact_ref=str(site.id)
        ))
        assert "KDO Gateway" in html
        assert "Gateway-Auftrag" in html
        assert "window.__ecpgReady=true" in html
    finally:
        db.close()
