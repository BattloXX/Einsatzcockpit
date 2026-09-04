"""Regressionstests für den zentralen Exercise-Guard."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.gateway import PrintRule
from app.models.incident import Incident, IncidentVehicle
from app.models.lis import OrgLisConfig
from app.models.master import FireDept, OrgSettings, VehicleMaster
from app.models.objekt import Objekt
from app.models.teams_bot import TeamsAlarmConfig
from app.models.wordpress_report import WordPressReportConfig
from app.services import broadcast, einsatz_live_service, incident_live_notify, incident_notify
from app.services.exercise_guard import darf_extern
from tests.conftest import TestingSession

ROOT = Path(__file__).resolve().parents[1]


def _org_mit_settings(db: Session) -> OrgSettings:
    org = FireDept(slug="exercise-guard-test", name="Exercise-Guard-Test")
    db.add(org)
    db.flush()
    settings = OrgSettings(org_id=org.id)
    db.add(settings)
    db.flush()
    return settings


def test_uebung_opt_ins_defaults_und_realeinsatz_regression(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        settings = _org_mit_settings(db)
        channels = ("push", "ws_alarm", "nachbar", "lis_status", "wordpress")
        for channel in channels:
            assert darf_extern(
                channel, is_exercise=False, org_id=settings.org_id, db=db
            ) is True
            assert darf_extern(
                channel, is_exercise=True, org_id=settings.org_id, db=db
            ) is False
        assert darf_extern(
            "geocoding", is_exercise=True, org_id=settings.org_id, db=db
        ) is True
    finally:
        db.rollback()
        db.close()


def test_uebung_opt_ins_geben_jeden_neuen_kanal_frei(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        settings = _org_mit_settings(db)
        mapping = {
            "push": "uebung_push_erlaubt",
            "ws_alarm": "uebung_ws_alarm_erlaubt",
            "nachbar": "uebung_nachbar_einladung_erlaubt",
            "lis_status": "uebung_lis_status_erlaubt",
            "wordpress": "uebung_wordpress_bericht_erlaubt",
            "geocoding": "uebung_geocoding_erlaubt",
        }
        for channel, attribute in mapping.items():
            setattr(settings, attribute, True)
            assert darf_extern(
                channel, is_exercise=True, org_id=settings.org_id, db=db
            ) is True
            setattr(settings, attribute, False)
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def guard_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    db = factory()
    set_tenant_context(db, None)
    org = FireDept(slug="exercise-integration", name="Exercise Integration")
    db.add(org)
    db.flush()
    settings = OrgSettings(org_id=org.id)
    incident = Incident(
        primary_org_id=org.id,
        alarm_type_code="B2",
        status="active",
        started_at=datetime(2026, 9, 4, 8, 0),
        address_city="Testort",
    )
    db.add_all([settings, incident])
    db.commit()
    yield db, settings, incident
    db.close()
    Base.metadata.drop_all(engine)


def _set_case(settings, incident, flag: str, is_exercise: bool, opt_in: bool) -> None:
    incident.is_exercise = is_exercise
    setattr(settings, flag, opt_in)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, 0), (True, True, 1), (False, False, 1)],
)
async def test_initial_push_aufrufstelle(
    guard_db, monkeypatch, is_exercise, opt_in, expected
):
    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_push_erlaubt", is_exercise, opt_in)
    db.commit()
    calls = []

    async def fake_push(*args, **kwargs):
        calls.append((args, kwargs))

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(incident_notify, "_send_incident_push", fake_push)
    monkeypatch.setattr("app.services.sms_dispatch_service.dispatch_einsatzinfo", noop)
    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", noop)
    monkeypatch.setattr(
        einsatz_live_service,
        "build_incident_live_payload",
        lambda *_: {
            "id": incident.id, "alarm_type_code": "B2", "address": "Testort",
            "started_at": "2026-09-04T08:00:00Z", "phase": "alarmiert",
            "phase_index": 0, "phase_count": 4, "phase_label": "Alarmiert",
            "unit_count": 0, "is_exercise": is_exercise, "url": f"/einsatz/{incident.id}",
        },
    )
    await incident_notify.notify_incident_created(
        db, incident, org_id=settings.org_id, background_tasks=None
    )
    assert len(calls) == expected


@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, 0), (True, True, 1), (False, False, 1)],
)
def test_live_push_aufrufstelle(guard_db, monkeypatch, is_exercise, opt_in, expected):
    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_push_erlaubt", is_exercise, opt_in)
    incident.live_push_at = None
    db.commit()
    calls = []
    monkeypatch.setattr(
        einsatz_live_service,
        "build_incident_live_payload",
        lambda *_: {
            "id": incident.id, "alarm_type_code": "B2", "address": "Testort",
            "started_at": "2026-09-04T08:00:00Z", "phase": "alarmiert",
            "phase_index": 0, "phase_count": 4, "phase_label": "Alarmiert",
            "unit_count": 0, "is_exercise": is_exercise, "url": f"/einsatz/{incident.id}",
        },
    )
    monkeypatch.setattr(
        "app.services.push_service.notify_org_web",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 1,
    )
    incident_live_notify._dispatch_live_push(incident.id, settings.org_id, "created")
    assert len(calls) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, False), (True, True, True), (False, False, True)],
)
async def test_live_ws_payload_ist_abwaertskompatibel(
    guard_db, monkeypatch, is_exercise, opt_in, expected
):
    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_ws_alarm_erlaubt", is_exercise, opt_in)
    db.commit()
    events = []

    async def fake_broadcast(_org_id, payload):
        events.append(payload)

    monkeypatch.setattr(broadcast, "broadcast_org", fake_broadcast)
    monkeypatch.setattr(
        einsatz_live_service,
        "build_incident_live_payload",
        lambda *_: {
            "id": incident.id, "alarm_type_code": "B2", "address": "Testort",
            "started_at": "2026-09-04T08:00:00Z", "phase": "alarmiert",
            "phase_index": 0, "phase_count": 4, "phase_label": "Alarmiert",
            "unit_count": 0, "is_exercise": is_exercise, "url": f"/einsatz/{incident.id}",
        },
    )
    await incident_live_notify.notify_incident_live(
        db, incident, org_id=settings.org_id, reason="reopened"
    )
    assert events[0]["alarm"] == "B2"
    assert events[0]["alarm_erlaubt"] is expected


def test_alle_incident_created_broadcasts_behalten_alarm_code_und_neues_flag():
    files = [
        "app/routers/ui_incident.py",
        "app/routers/api_v1.py",
        "app/routers/gateway_api.py",
        "app/services/dibos/dibos_enrich.py",
        "app/services/lis/lis_sync.py",
    ]
    for relpath in files:
        source = (ROOT / relpath).read_text()
        assert '"alarm_erlaubt": darf_extern(' in source
        assert '"alarm":' in source


def test_frontend_alarm_ist_bei_fehlendem_flag_fail_open_und_badge_bleibt_code():
    app_js = (ROOT / "app/static/js/app.js").read_text()
    infoscreen = (ROOT / "app/templates/objekt/infoscreen_alarm.html").read_text()
    base = (ROOT / "app/templates/base.html").read_text()
    assert "if (detail.alarm_erlaubt === false)" in app_js
    assert "if (!detail.alarm)" not in app_js
    assert "ev.alarm_erlaubt !== false" in infoscreen
    assert 'x-text="newIncidentAlert?.alarm"' in base


@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, 0), (True, True, 1), (False, False, 1)],
)
def test_nachbar_aufrufstelle(guard_db, monkeypatch, is_exercise, opt_in, expected):
    from app.routers import ui_incident

    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_nachbar_einladung_erlaubt", is_exercise, opt_in)
    db.commit()
    calls = []
    monkeypatch.setattr(
        ui_incident, "_create_neighbor_invitations", lambda *args: calls.append(args)
    )
    ui_incident._create_neighbor_invitations_guarded(
        db, incident, "B2", settings.org_id, None
    )
    assert len(calls) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_exercise", "geocoding_enabled", "expected"),
    [(True, True, 1), (True, False, 0), (False, False, 1)],
)
async def test_geocoding_aufrufstelle(
    guard_db, monkeypatch, is_exercise, geocoding_enabled, expected
):
    from app.routers.api_v1 import _geocode_incident

    db, settings, incident = guard_db
    _set_case(
        settings, incident, "uebung_geocoding_erlaubt", is_exercise, geocoding_enabled
    )
    incident.lat = None
    incident.lng = None
    db.commit()
    calls = []

    async def fake_geocode(*args):
        calls.append(args)
        return None

    monkeypatch.setattr("app.services.geocoding.geocode_address", fake_geocode)
    await _geocode_incident(incident.id, "Hauptstrasse", "1", "Testort")
    assert len(calls) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, 0), (True, True, 1), (False, False, 1)],
)
async def test_wordpress_aufrufstelle(
    guard_db, monkeypatch, is_exercise, opt_in, expected
):
    from app.services.wordpress_report_service import post_incident_report

    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_wordpress_bericht_erlaubt", is_exercise, opt_in)
    incident.status = "closed"
    db.add(WordPressReportConfig(
        org_id=settings.org_id,
        enabled=True,
        webhook_url="https://example.invalid/report",
        webhook_token="token",
    ))
    db.commit()
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"post_id": 123, "edit_url": "https://example.invalid/edit/123"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return FakeResponse()

    monkeypatch.setattr("app.services.wordpress_report_service.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.services.pdf_service.load_fahrtenbuch_report", lambda *a, **k: ([], []))
    await post_incident_report(db, incident)
    assert len(calls) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_exercise", "opt_in", "expected"),
    [(True, False, 0), (True, True, 1), (False, False, 1)],
)
async def test_lis_status_aufrufstelle(
    guard_db, monkeypatch, is_exercise, opt_in, expected
):
    from app.services.lis import lis_sync

    db, settings, incident = guard_db
    _set_case(settings, incident, "uebung_lis_status_erlaubt", is_exercise, opt_in)
    vehicle_master = VehicleMaster(
        dept_id=settings.org_id, code="TLF", name="TLF", type="TLF"
    )
    db.add(vehicle_master)
    db.flush()
    vehicle = IncidentVehicle(
        incident_id=incident.id,
        column_id=1,
        vehicle_master_id=vehicle_master.id,
        lis_operation_unit_id="unit-1",
    )
    db.add_all([
        vehicle,
        OrgLisConfig(
            org_id=settings.org_id,
            enabled=True,
            push_vehicle_status=True,
            base_url="https://lis.invalid",
            organization_id="org-guid",
            username="user",
            password_enc="encrypted",
        ),
    ])
    db.commit()
    calls = []

    class FakeLisClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_operation_unit_status_types(self):
            return [{"Label": "S5 Am Einsatzort", "Id": "status-1"}]

        async def set_operation_unit_status(self, *args):
            calls.append(args)

    monkeypatch.setattr(lis_sync, "LisClient", FakeLisClient)
    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "password")
    await lis_sync.push_vehicle_status_to_lis(vehicle.id, "Am Einsatzort")
    assert len(calls) == expected


def test_guard_delegiert_bestehende_teams_autoprint_objekt_und_sms_flags(guard_db):
    db, settings, _incident = guard_db
    settings.einsatzinfo_sms_send_exercise = True
    db.add_all([
        TeamsAlarmConfig(org_id=settings.org_id, enabled=True, send_exercise=True),
        PrintRule(
            org_id=settings.org_id,
            name="Übungsdruck",
            aktiv=True,
            trigger="einsatz_created",
            filters={"uebung": "nur_uebung"},
        ),
        Objekt(
            org_id=settings.org_id,
            name="Übungsobjekt",
            kontakt_info_uebung=True,
        ),
    ])
    db.flush()
    for channel in ("teams", "autoprint", "objekt_kontakt", "sms"):
        assert darf_extern(
            channel, is_exercise=True, org_id=settings.org_id, db=db
        ) is True
