"""Regressionen der schreibenden GSL-Lagewerkzeuge."""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident
from tests.test_mcp_gsl_ressourcen import _call, _new_seed
from tests.test_mcp_objekte import _token


def _lage(seed):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=seed["org_id"], name="MCP Schreiben")
        db.add(lage)
        db.commit()
        return lage.id
    finally:
        db.close()


def test_einsatzstelle_anlegen_duplikat_und_schreiben(client, monkeypatch):
    from app.services import gsl_broadcast, gsl_stelle_service

    seed = _new_seed(f"mcp-gsl-schreiben-{uuid4().hex[:8]}", {"editor": "recorder", "reader": "readonly"})
    lage_id = _lage(seed)
    token = _token(client, seed, "editor")
    reader = _token(client, seed, "reader")
    events = []

    async def no_geo(site):
        return None

    async def no_ai(site, db, org_id=None):
        return None

    async def created(db, lage, site_id):
        events.append(("created", site_id))

    monkeypatch.setattr(gsl_stelle_service, "_geocode_site", no_geo)
    monkeypatch.setattr(gsl_stelle_service, "_apply_ai_prio", no_ai)
    monkeypatch.setattr(gsl_broadcast, "stelle_angelegt", created)
    created_site = _call(
        client, token, "gsl_einsatzstelle_anlegen", lage_id=lage_id, bezeichnung="Keller",
        ort="  Wien ", strasse=" Hauptstraße ", hausnr="1", prio=2,
    )
    assert created_site["angelegt"] and created_site["prio"] == 2 and events
    duplicate = _call(
        client, token, "gsl_einsatzstelle_anlegen", lage_id=lage_id, bezeichnung="Doppelt",
        ort="wien", strasse="haupt straße", hausnr="1",
    )
    assert duplicate["angelegt"] is False and duplicate["duplikate"]
    denied = _call(client, reader, "gsl_einsatzstelle_eintrag", lage_id=lage_id, site_id=created_site["id"], text="x")
    assert "__fehler__" in denied
    changed = _call(
        client, token, "gsl_einsatzstelle_aendern", lage_id=lage_id, site_id=created_site["id"],
        felder={"bezeichnung": "Keller neu", "prio": 1},
    )
    assert changed["prio"] == 1


def test_phase_lagemeldung_funkstatus_und_wiedereroeffnen(client, monkeypatch):
    from app.models.major_incident import EinheitSiteDispatch, LageEinheit, LageJournalEntry, SiteLogEntry
    from app.services import gsl_broadcast

    seed = _new_seed(f"mcp-gsl-status-{uuid4().hex[:8]}", {"editor": "recorder"})
    lage_id = _lage(seed)
    token = _token(client, seed, "editor")
    site = _call(client, token, "gsl_einsatzstelle_anlegen", lage_id=lage_id, bezeichnung="Stelle")["id"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = LageEinheit(lage_id=lage_id, label="TLF", status="bereitgestellt")
        db.add(einheit)
        db.flush()
        dispatch = EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=site, einheit_status="zugewiesen", dispatched_at=datetime.now(UTC),
        )
        db.add(dispatch)
        db.commit()
        dispatch_id = dispatch.id
    finally:
        db.close()
    broadcasts = []

    async def status_event(*args, **kwargs):
        broadcasts.append("status")

    monkeypatch.setattr(gsl_broadcast, "einheit_status_geaendert", status_event)
    assert "gsl_einsatz_abschliessen" in _call(
        client, token, "gsl_einsatzstelle_phase_setzen", lage_id=lage_id, site_id=site, phase="erledigt"
    )["__fehler__"]
    _call(client, token, "gsl_lagemeldung_erfassen", lage_id=lage_id, site_id=site, lage="Wasser")
    status = _call(
        client, token, "gsl_einheit_status_melden", lage_id=lage_id, dispatch_id=dispatch_id,
        status="bestaetigt", kommentar="bin unterwegs",
    )
    assert status["status"] == "bestaetigt" and broadcasts
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log = db.query(SiteLogEntry).filter_by(incident_site_id=site).order_by(SiteLogEntry.id.desc()).first()
        assert "Funk (MCP)" in log.text
        assert "Funk (MCP)" in db.query(LageJournalEntry).filter_by(major_incident_id=lage_id).one().text
        dispatch = db.get(EinheitSiteDispatch, dispatch_id)
        dispatch.beendet_at = dispatch.status_at
        db.commit()
    finally:
        db.close()
    reopened = _call(client, token, "gsl_auftrag_wiedereroeffnen", lage_id=lage_id, dispatch_id=dispatch_id)
    assert reopened["status"] == "zugewiesen"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        lage.status = "closed"
        db.commit()
    finally:
        db.close()
    answer = _call(client, token, "gsl_einsatzstelle_eintrag", lage_id=lage_id, site_id=site, text="x")
    assert "nicht aktiv" in answer["__fehler__"]
