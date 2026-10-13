"""Druckregel gsl_stelle_disponiert: Einsatzstellen-Blatt je Disposition."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import (
    DOC_SITE_KARTE,
    TRIGGER_GSL_STELLE_DISPONIERT,
    Gateway,
    Printer,
    PrintJob,
    PrintRule,
)
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident
from app.models.master import OrgSettings, SystemSettings
from app.services import print_dispatcher


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _aufbau(db, *, filters=None, exercise=False):
    org = 810000 + uuid4().int % 10000
    system = db.query(SystemSettings).filter_by(key="gateway_module_enabled").first()
    if system is None:
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    else:
        system.value = "true"
    settings = db.query(OrgSettings).filter_by(org_id=org).first()
    if settings is None:
        settings = OrgSettings(org_id=org)
        db.add(settings)
    settings.gateway_module_enabled = True
    lage = MajorIncident(org_id=org, name="Lage", is_exercise=exercise)
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=org, bezeichnung="Stelle")
    unit_a = LageEinheit(lage_id=lage.id, label="A", resource_type="fahrzeug")
    unit_b = LageEinheit(lage_id=lage.id, label="B", resource_type="fahrzeug")
    gateway = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key(uuid4().hex))
    db.add_all([site, unit_a, unit_b, gateway])
    db.flush()
    printer = Printer(org_id=org, gateway_id=gateway.id, name="P", uri="ipp://p", aktiv=True)
    db.add(printer)
    db.flush()
    rule = PrintRule(org_id=org, name="Stelle", aktiv=True, trigger=TRIGGER_GSL_STELLE_DISPONIERT,
                     documents=[DOC_SITE_KARTE], printer_ids=[printer.id], filters=filters or {})
    db.add(rule)
    db.flush()
    return org, lage, site, unit_a, unit_b, gateway, rule


def _dispatch(db, site, unit, art="abarbeitung"):
    row = EinheitSiteDispatch(einheit_id=unit.id, site_id=site.id, dispatched_at=datetime.now(UTC),
                              status_at=datetime.now(UTC), auftragsart=art)
    db.add(row)
    db.flush()
    return row


def test_jobs_are_per_dispatch_and_idempotent(setup_db):
    db = _db()
    try:
        org, lage, site, a, b, gateway, rule = _aufbau(db)
        first = _dispatch(db, site, a)
        context = {"gsl_id": lage.id, "site_id": site.id, "dispatch_id": first.id, "einheit_id": a.id,
                   "auftragsart": "abarbeitung", "is_exercise": False, "erste_disposition": True}
        assert len(print_dispatcher.on_event(db, org, TRIGGER_GSL_STELLE_DISPONIERT, context)) == 1
        assert print_dispatcher.on_event(db, org, TRIGGER_GSL_STELLE_DISPONIERT, context) == []
        second = _dispatch(db, site, b)
        context["dispatch_id"], context["einheit_id"], context["erste_disposition"] = second.id, b.id, False
        assert len(print_dispatcher.on_event(db, org, TRIGGER_GSL_STELLE_DISPONIERT, context)) == 1
        jobs = db.query(PrintJob).filter_by(document_type=DOC_SITE_KARTE).all()
        assert len(jobs) == 2 and all(j.artifact_ref == str(site.id) and j.gsl_id == lage.id for j in jobs)
    finally:
        db.close()


def test_filters_and_test_context(setup_db):
    db = _db()
    try:
        _, lage, site, a, _, _, rule = _aufbau(db, filters={"stelle_nur_erste_disposition": True,
                                                             "stelle_auftragsart": "erkundung"}, exercise=True)
        dispatch = _dispatch(db, site, a, "erkundung")
        context = {"erste_disposition": False, "auftragsart": "erkundung", "is_exercise": True}
        assert not print_dispatcher._filter_matches(rule, context)
        context["erste_disposition"] = True
        assert print_dispatcher._filter_matches(rule, context)
        assert print_dispatcher.resolve_test_context(db, rule).ref_id == dispatch.id
        rule.filters = {"uebung": "nur_echt"}
        assert not print_dispatcher._filter_matches(rule, context)
    finally:
        db.close()


def test_background_creates_job(setup_db, monkeypatch):
    async def no_dispatch(db, job):
        return None

    monkeypatch.setattr(print_dispatcher, "dispatch_job", no_dispatch)
    db = _db()
    try:
        _, _, site, a, _, _, _ = _aufbau(db)
        dispatch = _dispatch(db, site, a)
        db.commit()
        asyncio.run(print_dispatcher.autoprint_gsl_stelle_disponiert_background(dispatch.id))
        assert db.query(PrintJob).filter_by(document_type=DOC_SITE_KARTE).count() == 1
    finally:
        db.close()


def test_filter_auftragsart_weicht_ab(setup_db):
    db = _db()
    try:
        _, _, _, _, _, _, rule = _aufbau(db, filters={"stelle_auftragsart": "erkundung"})
        assert not print_dispatcher._filter_matches(rule, {"auftragsart": "abarbeitung", "is_exercise": False})
        assert print_dispatcher._filter_matches(rule, {"auftragsart": "erkundung", "is_exercise": False})
    finally:
        db.close()


def test_disponieren_route_plant_autodruck(client, setup_db, monkeypatch):
    from tests.test_gk_auftrag_sms import _aufbau as _lage_aufbau
    from tests.test_gk_auftrag_sms import _post
    from tests.test_ressourcenkarte_routes import _login

    aufrufe = []

    async def merken(dispatch_id):
        aufrufe.append(dispatch_id)

    monkeypatch.setattr(print_dispatcher, "autoprint_gsl_stelle_disponiert_background", merken)
    a = _lage_aufbau(schalter=False)
    _login(client, a.username)
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="Keller")
    db = _db()
    try:
        dispatch = db.query(EinheitSiteDispatch).filter_by(einheit_id=a.einheit).one()
    finally:
        db.close()
    assert aufrufe == [dispatch.id]


def test_regeleditor_speichert_stellen_filter(setup_db):
    import inspect
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fastapi.params import Depends as DependsParam
    from pydantic.fields import FieldInfo

    from app.routers.ui_gateway import rule_save

    db = _db()
    try:
        org, _, _, _, _, _, rule = _aufbau(db)
        db.commit()
        kwargs = {}
        for name, param in inspect.signature(rule_save).parameters.items():
            if isinstance(param.default, FieldInfo):
                kwargs[name] = None if param.default.default is ... else param.default.default
            elif isinstance(param.default, DependsParam):
                kwargs[name] = None
        kwargs.update(
            rule_id=rule.id, request=MagicMock(), db=db, user=SimpleNamespace(org_id=org),
            name=rule.name, trigger=TRIGGER_GSL_STELLE_DISPONIERT, documents=[DOC_SITE_KARTE],
            objekt_elements=[], printer_ids=list(rule.printer_ids),
            stelle_nur_erste_disposition="1", stelle_auftragsart="abarbeitung",
        )
        rule_save(**kwargs)
        db.refresh(rule)
        assert rule.filters["stelle_nur_erste_disposition"] is True
        assert rule.filters["stelle_auftragsart"] == "abarbeitung"
        assert rule.documents == [DOC_SITE_KARTE]
    finally:
        db.close()
