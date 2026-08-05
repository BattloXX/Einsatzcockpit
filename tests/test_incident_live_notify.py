"""Tests fuer WebSocket-Fan-out und persistente Live-Push-Drosselung."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentVehicle
from app.services import broadcast, push_service
from app.services.incident_live_notify import _dispatch_live_push, notify_incident_live


@pytest.fixture
def live_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    db = factory()
    set_tenant_context(db, None)
    incident = Incident(
        alarm_type_code="B2", status="active", primary_org_id=1,
        address_street="Hauptstrasse", address_no="5", address_city="Testort",
        started_at=datetime(2026, 8, 5, 8, 0), is_exercise=False,
    )
    db.add(incident)
    db.flush()
    db.add(IncidentVehicle(
        incident_id=incident.id, column_id=1, vehicle_master_id=1,
        unit_status="Einsatz uebernommen",
    ))
    db.commit()
    yield db, incident
    db.close()
    Base.metadata.drop_all(engine)


def test_same_phase_and_minimum_interval_are_suppressed(live_db, monkeypatch):
    db, incident = live_db
    calls = []
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: calls.append(kw) or 1)

    _dispatch_live_push(incident.id, 1, "unit_status")
    _dispatch_live_push(incident.id, 1, "unit_status")
    assert len(calls) == 1

    vehicle = db.query(IncidentVehicle).filter_by(incident_id=incident.id).one()
    vehicle.unit_status = "Am Einsatzort"
    db.commit()
    _dispatch_live_push(incident.id, 1, "unit_status")
    assert len(calls) == 1


def test_closed_always_pushes_with_same_tag(live_db, monkeypatch):
    _db, incident = live_db
    extras = []
    monkeypatch.setattr(
        push_service, "notify_org_web",
        lambda *a, **kw: extras.append(kw["extra"]) or 1,
    )
    _dispatch_live_push(incident.id, 1, "unit_status")
    _dispatch_live_push(incident.id, 1, "closed")

    assert len(extras) == 2
    assert extras[1]["kind"] == "einsatz_live_end"
    assert extras[1]["tag"] == extras[0]["tag"] == f"ec-einsatz-{incident.id}"


@pytest.mark.asyncio
async def test_no_request_context_never_uses_fcm(live_db, monkeypatch):
    db, incident = live_db
    web_calls = []
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: web_calls.append(kw) or 1)
    monkeypatch.setattr(
        push_service, "_notify_fcm_users",
        lambda *a, **kw: pytest.fail("Der Live-Pfad darf FCM nicht aufrufen"),
    )
    monkeypatch.setattr(broadcast, "broadcast_org", lambda *a, **kw: _async_none())

    await notify_incident_live(
        db, incident, org_id=1, reason="unit_status", background_tasks=None,
    )
    assert len(web_calls) == 1


@pytest.mark.asyncio
async def test_ws_broadcast_is_not_throttled(live_db, monkeypatch):
    db, incident = live_db
    ws_events = []

    async def fake_broadcast(_org_id, event):
        ws_events.append(event)

    monkeypatch.setattr(broadcast, "broadcast_org", fake_broadcast)
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: 1)

    await notify_incident_live(db, incident, org_id=1, reason="unit_status")
    await notify_incident_live(db, incident, org_id=1, reason="unit_status")

    assert len(ws_events) == 2
    assert all(event["type"] == "einsatz_live" for event in ws_events)


async def _async_none():
    return None
