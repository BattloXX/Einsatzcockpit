"""Regressionen für offene Konsumenten der Phase ``fertig_gemeldet``."""

from app.models.major_incident import SITE_PHASE_GROUP, SitePhase
from app.services.major_incident_service import PHASE_LABELS


def test_fertig_gemeldet_ist_offen_und_erledigt_heisst_abgeschlossen():
    assert SITE_PHASE_GROUP[SitePhase.fertig_gemeldet] == "in_arbeit"
    assert PHASE_LABELS[SitePhase.erledigt] == "Abgeschlossen"


def test_gsl_live_zaehlt_fertig_gemeldet_als_in_arbeit(setup_db):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import IncidentSite, MajorIncident
    from app.services.gsl_live_service import build_gsl_live_payload

    db = SessionLocal()
    set_tenant_context(db, 1)
    try:
        lage = MajorIncident(org_id=1, name="Phasen", status="active")
        db.add(lage)
        db.flush()
        db.add(IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Fertig",
                            phase=SitePhase.fertig_gemeldet))
        db.commit()
        assert build_gsl_live_payload(db, lage)["counts"] == {"neu": 0, "in_arbeit": 1, "erledigt": 0,
                                                               "gesamt": 1}
    finally:
        db.close()


def test_mcp_tools_are_registered_with_manage_scope():
    from app.mcp.registry import TOOLS

    assert TOOLS["gsl_einsatz_abschliessen"].required_roles == ("incident_leader", "admin", "org_admin")
    assert TOOLS["gsl_einsatz_wiedereroeffnen"].required_roles == ("incident_leader", "admin", "org_admin")


def test_karte_sites_uses_teal_for_fertig_gemeldet(client, setup_db):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import IncidentSite
    from tests.test_gk_auftrag_sms import _aufbau
    from tests.test_ressourcenkarte_routes import _login

    daten = _aufbau()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        site = db.get(IncidentSite, daten.site)
        site.phase = SitePhase.fertig_gemeldet
        site.lat, site.lng = 47.2, 9.8
        db.commit()
    finally:
        db.close()
    _login(client, daten.username)
    response = client.get(f"/lage/{daten.lage}/karte-sites")
    assert response.status_code == 200
    assert response.json()[0]["color"] == "#0d9488"


# ── Ergänzungen: Infoscreen, MCP-Disposition mit Auftragsart, Abschluss/Wiedereröffnung ──

from uuid import uuid4  # noqa: E402

from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.major_incident import EinheitSiteDispatch, IncidentSite  # noqa: E402
from app.models.user import AuditLog  # noqa: E402
from tests.test_mcp_gsl_erweiterung import _lage as _mcp_lage  # noqa: E402
from tests.test_mcp_gsl_ressourcen import _call, _new_seed  # noqa: E402
from tests.test_mcp_objekte import _token  # noqa: E402


def test_infoscreen_gruppiert_fertig_gemeldet_als_offen():
    from app.routers.ui_infoscreen_alarm import _GSL_PHASE_GRUPPE

    assert _GSL_PHASE_GRUPPE["fertig_gemeldet"] == "in_arbeit"
    assert _GSL_PHASE_GRUPPE["erledigt"] == "erledigt"


def _site(site_id):
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        site = db.get(IncidentSite, site_id)
        dispatches = db.query(EinheitSiteDispatch).filter_by(site_id=site_id).all()
        return site.phase, [(d.auftragsart, d.withdrawn_at is not None) for d in dispatches]
    finally:
        db.close()


def _mcp_stumm(monkeypatch):
    import app.services.gsl_auftrag_events as events
    import app.services.print_dispatcher as printer

    async def nichts(*_a, **_k):
        return None

    monkeypatch.setattr(events.gk_zugang_service, "sende_auto_sms", nichts)
    monkeypatch.setattr(printer, "autoprint_gsl_einheit_background", nichts)


def test_mcp_disponieren_mit_auftragsart_und_fehlerfaelle(client, monkeypatch):
    _mcp_stumm(monkeypatch)
    seed = _new_seed(f"mcp-gsl-art-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, einheit_id = _mcp_lage(seed)
    token = _token(client, seed, "editor")
    falsch = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id,
                   site_id=site_id, auftragsart="bergung")
    assert "__fehler__" in falsch
    result = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id,
                   site_id=site_id, auftragsart="erkundung")
    assert result["auftragsart"] == "erkundung" and result["site_phase"] == "erkundung"
    assert _site(site_id) == (SitePhase.erkundung, [("erkundung", False)])
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        db.get(IncidentSite, site_id).phase = SitePhase.erledigt
        db.commit()
    finally:
        db.close()
    zu = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id,
               site_id=site_id)
    assert "wieder öffnen" in zu["__fehler__"]


def test_mcp_einsatz_abschliessen_und_wiedereroeffnen(client, monkeypatch):
    _mcp_stumm(monkeypatch)
    seed = _new_seed(f"mcp-gsl-close-{uuid4().hex[:8]}", {"leiter": "incident_leader", "editor": "recorder"})
    lage_id, site_id, einheit_id = _mcp_lage(seed)
    leiter = _token(client, seed, "leiter")
    editor = _token(client, seed, "editor")
    _call(client, leiter, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id, site_id=site_id)
    # recorder hat keine Abschlussrechte
    assert "__fehler__" in _call(client, editor, "gsl_einsatz_abschliessen", lage_id=lage_id, site_id=site_id)
    ohne = _call(client, leiter, "gsl_einsatz_abschliessen", lage_id=lage_id, site_id=site_id)
    assert "Aktive Aufträge" in ohne["__fehler__"]
    assert _site(site_id)[0] == SitePhase.disponiert
    mit = _call(client, leiter, "gsl_einsatz_abschliessen", lage_id=lage_id, site_id=site_id,
                aktive_zurueckziehen=True)
    assert mit["site_phase"] == "erledigt" and len(mit["zurueckgezogen"]) == 1
    assert _site(site_id) == (SitePhase.erledigt, [("abarbeitung", True)])
    offen = _call(client, leiter, "gsl_einsatz_wiedereroeffnen", lage_id=lage_id, site_id=site_id)
    assert offen["site_phase"] != "erledigt"
    assert "__fehler__" in _call(client, leiter, "gsl_einsatz_wiedereroeffnen", lage_id=lage_id, site_id=site_id)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        aktionen = {row.action for row in db.query(AuditLog).filter(AuditLog.org_id == seed["org_id"]).all()}
        assert {"gsl.einsatz.abgeschlossen", "gsl.einsatz.wiedereroeffnet"} <= aktionen
    finally:
        db.close()
