"""End-to-end-Regressionen der erweiterten GSL-MCP-Werkzeuge."""

import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, LageEinheit, MajorIncident
from app.models.user import AuditLog
from tests.test_mcp_gsl_ressourcen import _call, _new_seed
from tests.test_mcp_objekte import _token


def _lage(seed, *, label="MCP Einheit"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=seed["org_id"], name="MCP Erweiterung")
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=seed["org_id"], bezeichnung="MCP Stelle")
        db.add(site)
        einheit = LageEinheit(lage_id=lage.id, label=label, status="bereitgestellt", resource_type="fahrzeug")
        db.add(einheit)
        db.commit()
        return lage.id, site.id, einheit.id
    finally:
        db.close()


def test_neue_tools_disposition_aenderung_abzug_und_audit(client, monkeypatch):
    seed = _new_seed(f"mcp-gsl-e2-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, einheit_id = _lage(seed)
    token = _token(client, seed, "editor")
    import app.services.gsl_auftrag_events as events
    import app.services.print_dispatcher as printer

    sent, printed = [], []

    async def fake_send(auftrag):
        sent.append(auftrag)

    async def fake_print(einheit):
        printed.append(einheit)

    monkeypatch.setattr(events.gk_zugang_service, "sende_auto_sms", fake_send)
    monkeypatch.setattr(printer, "autoprint_gsl_einheit_background", fake_print)
    created = _call(
        client, token, "gsl_ressource_anlegen", lage_id=lage_id, resource_type="fahrzeug", label="Neu MCP",
        funkrufname="MCP 1", personal_gesamt=4,
    )
    assert created["label"] == "Neu MCP" and created["einheit_id"] in printed
    dispatched = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id,
                       site_id=site_id, auftrag="Erkunden", reihenfolge=1)
    changed = _call(client, token, "gsl_auftrag_aendern", lage_id=lage_id,
                    dispatch_id=dispatched["dispatch_id"], auftrag="Sichern", reihenfolge=1)
    same = _call(client, token, "gsl_auftrag_aendern", lage_id=lage_id,
                 dispatch_id=dispatched["dispatch_id"], auftrag="Sichern", reihenfolge=1)
    withdrawn = _call(client, token, "gsl_auftrag_zurueckziehen", lage_id=lage_id,
                      einheit_id=einheit_id, site_id=site_id, grund="Ende")
    assert changed["geaendert"] is True and same["sms"] == "übersprungen"
    assert withdrawn["dispatch_id"] == dispatched["dispatch_id"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        actions = {row.action for row in db.query(AuditLog).filter(AuditLog.org_id == seed["org_id"]).all()}
        assert {"gsl.auftrag.disponiert", "gsl.auftrag.geaendert", "gsl.auftrag.zurueckgezogen"} <= actions
    finally:
        db.close()


def test_erweiterung_rollen_tenant_status_und_geheimnisfreiheit(client):
    own = _new_seed(f"mcp-gsl-e2-own-{uuid4().hex[:8]}", {"reader": "readonly"})
    foreign = _new_seed(f"mcp-gsl-e2-foreign-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, einheit_id = _lage(own)
    foreign_lage, _, _ = _lage(foreign)
    token = _token(client, own, "reader")
    denied = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id, site_id=site_id)
    cross = _call(client, token, "gsl_ressource_qr", lage_id=foreign_lage, einheit_id=einheit_id, aktion="status")
    qr = _call(client, token, "gsl_ressource_qr", lage_id=lage_id, einheit_id=einheit_id, aktion="status")
    assert "__fehler__" in denied and "Lage nicht gefunden" in cross["__fehler__"]
    dump = json.dumps([denied, cross, qr])
    for secret in ("gkz_", "gkq_", "/gk#", "token_hash", "pin"):
        assert secret not in dump


def _sms_vorbereiten(seed, einheit_id):
    from app.models.major_incident import LageEinheitLeader
    from app.models.master import OrgSettings
    from app.services import gk_zugang_service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one()
        cfg.gk_zugang_aktiv, cfg.gk_auto_sms_auftrag, cfg.gk_qr_aktiv = True, True, True
        einheit = db.get(LageEinheit, einheit_id)
        leader = LageEinheitLeader(
            einheit_id=einheit_id, person_name="Max", phone_e164="+436641234567", phone_version=1, rolle="fuehrer",
            start_at=gk_zugang_service._now(),
        )
        db.add(leader)
        db.flush()
        einheit.leader_assignment_id = leader.id
        db.commit()
    finally:
        db.close()


def test_mcp_disposition_loest_genau_eine_auftrags_sms_aus(client, monkeypatch):
    from types import SimpleNamespace

    from app.services import gk_zugang_service

    seed = _new_seed(f"mcp-gsl-e2-sms-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id, site_id, einheit_id = _lage(seed)
    _sms_vorbereiten(seed, einheit_id)
    token = _token(client, seed, "editor")
    texte = []

    async def senden(org_id, nummer, text, **kwargs):
        texte.append(text)
        return SimpleNamespace(success=True, provider="test")

    monkeypatch.setattr(gk_zugang_service, "send_sms", senden)
    monkeypatch.setattr(gk_zugang_service, "sms_available", lambda org_id, db: True)
    dispatched = _call(client, token, "gsl_einheit_disponieren", lage_id=lage_id, einheit_id=einheit_id,
                       site_id=site_id, auftrag="Keller")
    assert len(texte) == 1 and "Neuer Einsatzauftrag" in texte[0]
    _call(client, token, "gsl_auftrag_aendern", lage_id=lage_id, dispatch_id=dispatched["dispatch_id"],
          auftrag="Keller")
    assert len(texte) == 1
    _call(client, token, "gsl_auftrag_aendern", lage_id=lage_id, dispatch_id=dispatched["dispatch_id"],
          auftrag="Garage")
    assert len(texte) == 2 and "Auftrag geaendert" in texte[1]
    assert "gkz_" not in json.dumps(dispatched)


def test_mcp_qr_widerruf_beendet_sitzung_und_readonly_darf_nicht(client):
    from app.services import gk_zugang_service as service
    from tests.test_gk_zugang_service import _session

    seed = _new_seed(f"mcp-gsl-e2-qr-{uuid4().hex[:8]}", {"editor": "recorder", "reader": "readonly"})
    lage_id, _, einheit_id = _lage(seed)
    _sms_vorbereiten(seed, einheit_id)
    with _session() as db:
        lage, einheit = db.get(MajorIncident, lage_id), db.get(LageEinheit, einheit_id)
        neu = service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        from app.models.major_incident import LageEinheitZugang

        cookie, _ = service.sitzung_anlegen(db, db.get(LageEinheitZugang, neu.zugang_id), user_agent=None, ip=None,
                                            verifiziert=True)
        db.commit()
    reader = _token(client, seed, "reader")
    editor = _token(client, seed, "editor")
    assert "__fehler__" in _call(client, reader, "gsl_ressource_qr", lage_id=lage_id, einheit_id=einheit_id,
                                 aktion="widerrufen")
    status = _call(client, editor, "gsl_ressource_qr", lage_id=lage_id, einheit_id=einheit_id, aktion="status")
    assert "gkq_" not in json.dumps(status)
    _call(client, editor, "gsl_ressource_qr", lage_id=lage_id, einheit_id=einheit_id, aktion="widerrufen")
    with _session() as db:
        assert service.sitzung_pruefen(db, cookie, "qr") is None


def test_mcp_fremder_dispatch_wird_abgewiesen(client):
    own = _new_seed(f"mcp-gsl-e2-d-own-{uuid4().hex[:8]}", {"editor": "recorder"})
    foreign = _new_seed(f"mcp-gsl-e2-d-foreign-{uuid4().hex[:8]}", {"editor": "recorder"})
    f_lage, f_site, f_einheit = _lage(foreign)
    token_f = _token(client, foreign, "editor")
    dispatched = _call(client, token_f, "gsl_einheit_disponieren", lage_id=f_lage, einheit_id=f_einheit,
                       site_id=f_site, auftrag="X")
    own_lage, _, _ = _lage(own)
    token = _token(client, own, "editor")
    antwort = _call(client, token, "gsl_auftrag_aendern", lage_id=own_lage, dispatch_id=dispatched["dispatch_id"],
                    auftrag="Hack")
    assert "__fehler__" in antwort
