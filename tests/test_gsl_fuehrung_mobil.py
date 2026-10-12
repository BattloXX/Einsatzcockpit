"""Regression tests for the mobile GSL leadership overview."""
from datetime import UTC, datetime, timedelta
import re

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, MajorIncident, SitePhase, SitePriority
from app.models.master import OrgSettings
from app.services.gsl_fuehrung_service import fuehrungs_hinweise
from tests.test_einheit_api import _daten as _api_daten
from tests.test_gsl_fuehrung_einheiten import _als, _mit_rolle


def _leader(client, data):
    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))


def _set_site(data, **values):
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        site = db.get(IncidentSite, data["a"])
        for key, value in values.items():
            setattr(site, key, value)
        settings = db.query(OrgSettings).filter_by(org_id=site.org_id).first()
        if settings:
            settings.gsl_lagemeldung_interval_minutes = 10
        db.commit()
    finally:
        db.close()


def test_fuehrungs_hinweise_codes_and_ordering():
    data = _api_daten(); now = datetime.now(UTC).replace(tzinfo=None)
    _set_site(data, priority=SitePriority.sofort, naechste_lagemeldung_at=now - timedelta(minutes=4))
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, data["lage"])
        hints = fuehrungs_hinweise(db, lage, now=now)
        codes = {reason["code"] for reason in hints[0]["gruende"]}
        assert {"dringend", "lagemeldung_ueberfaellig"} <= codes
        assert "4 Min." in next(r["text"] for r in hints[0]["gruende"] if r["code"] == "lagemeldung_ueberfaellig")
        assert hints[0]["priority"] == SitePriority.sofort.value
        site = db.get(IncidentSite, data["a"]); site.phase = SitePhase.erledigt; db.commit()
        assert not fuehrungs_hinweise(db, lage, now=now)
    finally:
        db.close()


def test_fuehrungs_hinweise_unbestaetigt_rueckmeldung_und_abschluss():
    data = _api_daten(); now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, data["lage"])
        settings = db.query(OrgSettings).filter_by(org_id=lage.org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=lage.org_id)
            db.add(settings)
        settings.gsl_lagemeldung_interval_minutes = 10
        a = db.get(IncidentSite, data["a"]); b = db.get(IncidentSite, data["b"])
        a.priority = SitePriority.normal
        b.phase = SitePhase.fertig_gemeldet
        rows = db.query(EinheitSiteDispatch).filter_by(site_id=a.id).all()
        rows[0].einheit_status = "zugewiesen"; rows[0].bestaetigt_at = None
        rows[0].dispatched_at = now - timedelta(minutes=11)
        rows[1].einheit_status = "in_arbeit"; rows[1].dispatched_at = now - timedelta(minutes=11)
        rows[1].status_at = now - timedelta(minutes=11)
        rows[1].letzte_rueckmeldung_at = None
        db.commit()
        hints = fuehrungs_hinweise(db, lage, now=now)
        by_site = {hint["site_id"]: {r["code"] for r in hint["gruende"]} for hint in hints}
        assert "unbestaetigt" in by_site[a.id]
        assert "keine_rueckmeldung" in by_site[a.id]
        assert by_site[b.id] == {"wartet_auf_abschluss"}
    finally:
        db.close()


def test_dashboard_partial_map_and_mobile_detail_permissions(client):
    data = _api_daten(); _leader(client, data)
    _set_site(data, phase=SitePhase.fertig_gemeldet)
    dashboard = client.get(f"/lage/{data['lage']}/dashboard")
    assert "Fertig gemeldet" in dashboard.text
    partial = client.get(f"/lage/{data['lage']}/dashboard/hinweise")
    assert partial.status_code == 200
    sites = client.get(f"/lage/{data['lage']}/karte-sites").json()
    assert all("aufmerksamkeit" in site for site in sites)
    detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert "site-detail-actions" in detail and "Einsatz abschließen" in detail
    _als(client, sign_session(_mit_rolle(data["admin"], "readonly")))
    readonly = client.get(f"/lage/{data['lage']}/dashboard/hinweise").text
    readonly_detail = client.get(f"/lage/{data['lage']}/stellen/{data['a']}").text
    assert readonly and '<div class="site-detail-actions">' not in readonly_detail


def test_board_query_params_and_templates_use_ascii_attribute_quotes(client):
    data = _api_daten(); _leader(client, data)
    board = client.get(f"/lage/{data['lage']}?phase=fertig_gemeldet&site={data['a']}").text
    assert "params.get('phase')" in board and "params.get('site')" in board
    smart_in_attr = re.compile(r'''=\s*("[^"]*[“”„‘’][^"]*"|'[^']*[“”„‘’][^']*')''')
    for url in (f"/lage/{data['lage']}/dashboard", f"/lage/{data['lage']}/stellen/{data['a']}", f"/lage/{data['lage']}/karte"):
        assert not smart_in_attr.findall(client.get(url).text), url


def test_unbestaetigt_erst_nach_schwelle_und_fremde_lage_ausgeschlossen():
    data = _api_daten(); now = datetime.now(UTC).replace(tzinfo=None)
    fremd = _api_daten()
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, data["lage"])
        settings = db.query(OrgSettings).filter_by(org_id=lage.org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=lage.org_id)
            db.add(settings)
        settings.gsl_lagemeldung_interval_minutes = 10
        a = db.get(IncidentSite, data["a"]); a.priority = SitePriority.normal
        for row in db.query(EinheitSiteDispatch).filter_by(site_id=a.id).all():
            row.einheit_status, row.bestaetigt_at = "zugewiesen", None
            row.dispatched_at = now - timedelta(minutes=9)  # unter der Schwelle
        fremd_site = db.get(IncidentSite, fremd["a"]); fremd_site.priority = SitePriority.sofort
        db.commit()
        hints = fuehrungs_hinweise(db, lage, now=now)
        codes = {r["code"] for h in hints if h["site_id"] == a.id for r in h["gruende"]}
        assert "unbestaetigt" not in codes
        assert fremd_site.id not in {h["site_id"] for h in hints}
        assert "unbestaetigt" in {
            r["code"] for h in fuehrungs_hinweise(db, lage, now=now + timedelta(minutes=2))
            if h["site_id"] == a.id for r in h["gruende"]
        }
    finally:
        db.close()


def test_keine_rueckmeldung_gleich_wie_board_karte():
    from app.services import lagemeldung_service, resource_service

    data = _api_daten(); now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, data["lage"])
        org = db.query(OrgSettings).filter_by(org_id=lage.org_id).first()
        if org is None:
            org = OrgSettings(org_id=lage.org_id)
            db.add(org)
        org.gsl_lagemeldung_interval_minutes = 10
        a = db.get(IncidentSite, data["a"])
        for row in db.query(EinheitSiteDispatch).filter_by(site_id=a.id).all():
            row.einheit_status, row.status_at, row.letzte_rueckmeldung_at = "in_arbeit", now - timedelta(minutes=25), None
            row.dispatched_at = now - timedelta(minutes=30)
        db.commit()
        settings = lagemeldung_service.org_settings_for(db, lage.org_id)
        karte = resource_service.get_einheiten_chips_for_sites(db, [a], org_settings=settings, jetzt=now)[a.id]
        hint = next((h for h in fuehrungs_hinweise(db, lage, now=now) if h["site_id"] == a.id), None)
        text = next((r["text"] for r in (hint or {}).get("gruende", []) if r["code"] == "keine_rueckmeldung"), None)
        assert karte["ohne_rueckmeldung_min"] is not None
        assert text == f"Keine Rückmeldung seit {karte['ohne_rueckmeldung_min']} Min."
    finally:
        db.close()


def test_links_aus_dashboard_und_hinweisen_fuehren_auf_bestehende_seiten(client):
    data = _api_daten(); _leader(client, data)
    _set_site(data, phase=SitePhase.fertig_gemeldet)
    html = client.get(f"/lage/{data['lage']}/dashboard").text + client.get(
        f"/lage/{data['lage']}/dashboard/hinweise").text + client.get(f"/lage/{data['lage']}/karte").text
    links = set(re.findall(r'href="(/lage/\d+[^"]*)"', html))
    assert any("?site=" in link for link in links) and any("?phase=" in link for link in links)
    for link in links:
        if "?site=" in link or "?phase=" in link or link.rstrip("/").endswith(str(data["lage"])):
            assert client.get(link).status_code == 200, link
