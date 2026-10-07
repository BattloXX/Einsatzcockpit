"""Tests fuer den entkoppelten Einsatzrouten-Worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentOrg
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.road_closure import IncidentRoute, RoadClosure
from app.services.einsatz_routing import RouteResponse, RouteResult, RoutingError
from app.services.incident_route_loop import claim_due_routes, discover_routes, process_route, run_incident_route_once

START = (47.47, 9.73)
DEST = (47.47, 9.76)
GEOMETRY = {"type": "LineString", "coordinates": [[9.73, 47.47], [9.76, 47.47]]}


class Provider:
    name = "test"
    supports_avoid_polygons = True

    def __init__(self, error: BaseException | None = None):
        self.error = error
        self.destinations = []

    async def calculate_route(self, start, dest, *, avoid_polygons=None, alternatives=False):
        self.destinations.append(dest)
        if self.error:
            raise self.error
        return RouteResponse(RouteResult(GEOMETRY, 1000, 100, []), [], "test", True)


@pytest.fixture
def db(setup_db, monkeypatch):
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", True)
    session = SessionLocal()
    set_tenant_context(session, None)
    flag = session.query(SystemSettings).filter_by(key="strassensperren_module_enabled").first()
    if flag is None:
        session.add(SystemSettings(key="strassensperren_module_enabled", value="true"))
    else:
        flag.value = "true"
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def org(db, active=True):
    item = FireDept(slug=f"route-loop-{uuid4().hex}", name="Route Loop")
    db.add(item)
    db.flush()
    db.add(OrgSettings(org_id=item.id, strassensperren_modul_aktiv=active,
                       routing_start_lat=START[0], routing_start_lng=START[1]))
    return item


def test_discover_primary_collaborator_closed_and_disabled(db, monkeypatch):
    primary, collaborator, disabled = org(db), org(db), org(db, False)
    active = Incident(primary_org_id=primary.id, lat=DEST[0], lng=DEST[1], status="active")
    closed = Incident(primary_org_id=primary.id, lat=DEST[0], lng=DEST[1], status="closed")
    db.add_all([active, closed])
    db.flush()
    db.add_all([IncidentOrg(incident_id=active.id, org_id=collaborator.id), IncidentOrg(incident_id=active.id, org_id=disabled.id)])
    assert discover_routes(db) == 2
    assert {(row.incident_id, row.org_id) for row in db.query(IncidentRoute).all()} >= {
        (active.id, primary.id), (active.id, collaborator.id)
    }
    assert db.query(IncidentRoute).filter_by(incident_id=closed.id).count() == 0
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", False)
    assert discover_routes(db) == 0


def test_recalculate_after_destination_and_closure_change(db, monkeypatch):
    item = org(db)
    incident = Incident(primary_org_id=item.id, lat=DEST[0], lng=DEST[1], status="active")
    db.add(incident)
    db.commit()
    provider = Provider()
    monkeypatch.setattr("app.services.road_closure_incident_service.einsatz_routing.get_provider", lambda: provider)
    assert asyncio.run(run_incident_route_once()) >= 1
    route = db.query(IncidentRoute).filter_by(incident_id=incident.id, org_id=item.id).one()
    assert route.status == "ok"
    incident.lat = 47.48
    db.commit()
    assert discover_routes(db) == 1
    assert claim_due_routes(db) == [route.id]
    asyncio.run(process_route(route.id))
    assert provider.destinations[-1] == (47.48, DEST[1])
    closure = RoadClosure(org_id=item.id, title="Neu", valid_from=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
                          restriction_type="closed", geometry_geojson='{"type":"Point","coordinates":[9.75,47.47]}', geometry_status="ok")
    db.add(closure)
    db.commit()
    assert discover_routes(db) == 1
    assert route.id in claim_due_routes(db)
    asyncio.run(process_route(route.id))
    closure.valid_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()
    assert discover_routes(db) == 1


def test_error_backoff_lease_and_unexpected_error(db, monkeypatch):
    item = org(db)
    incident = Incident(primary_org_id=item.id, lat=DEST[0], lng=DEST[1], status="active")
    db.add(incident)
    db.commit()
    provider = Provider(RoutingError("unreachable"))
    monkeypatch.setattr("app.services.road_closure_incident_service.einsatz_routing.get_provider", lambda: provider)
    now = datetime.now(UTC).replace(tzinfo=None)
    discover_routes(db, now)
    route = db.query(IncidentRoute).filter_by(incident_id=incident.id).one()
    assert route.id in claim_due_routes(db, now)
    assert claim_due_routes(db, now) == []
    asyncio.run(process_route(route.id))
    db.expire_all()
    assert route.status == "error" and route.attempts == 1
    assert route.next_attempt_at and route.next_attempt_at >= now + timedelta(seconds=29)
    assert claim_due_routes(db, now + timedelta(seconds=10)) == []
    assert route.id in claim_due_routes(db, now + timedelta(seconds=31))

    async def broken(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("app.services.incident_route_loop.compute_incident_route", broken)
    asyncio.run(process_route(route.id))
    db.expire_all()
    assert route.status == "error" and route.routing_error == "Interner Fehler bei der Routenberechnung"


def test_main_registers_incident_route_loop():
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert "from app.services.incident_route_loop import incident_route_loop" in source
    assert "incident_route_loop(),\n    )" in source


def test_running_mit_abgelaufenem_lease_wird_wieder_aufgenommen(db):
    owner = org(db)
    incident = Incident(primary_org_id=owner.id, lat=DEST[0], lng=DEST[1], status="active")
    db.add(incident)
    db.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    route = IncidentRoute(org_id=owner.id, incident_id=incident.id, status="running",
                          lease_until=now - timedelta(seconds=1))
    db.add(route)
    db.commit()
    assert route.id in claim_due_routes(db, now)


def test_notify_nur_bei_echter_aenderung(db, monkeypatch):
    calls = []

    async def zaehle(incident_id, org_id):
        calls.append((incident_id, org_id))

    monkeypatch.setattr("app.services.incident_route_loop.notify_route_updated", zaehle)
    monkeypatch.setattr("app.services.einsatz_routing.get_provider", lambda: Provider())
    owner = org(db)
    incident = Incident(primary_org_id=owner.id, lat=DEST[0], lng=DEST[1], status="active")
    db.add(incident)
    db.flush()
    route = IncidentRoute(org_id=owner.id, incident_id=incident.id, status="pending")
    db.add(route)
    db.commit()
    asyncio.run(process_route(route.id))
    assert len(calls) == 1
    asyncio.run(process_route(route.id))
    assert len(calls) == 1


def test_routing_down_blockiert_einsatzanlage_nicht(client, db, monkeypatch):
    """Wichtigster Regressionstest: Routing nicht erreichbar -> Einsatz und Alarm-Jobs entstehen trotzdem sofort."""
    import time

    from app.core.security import generate_api_key, hash_api_key
    from app.models.incident import IncidentAlarmJob
    from app.models.user import ApiKey

    async def no_process(*args, **kwargs):
        return None

    async def no_geocode(*args, **kwargs):
        return None

    class HaengenderProvider(Provider):
        async def calculate_route(self, start, dest, *, avoid_polygons=None, alternatives=False):
            await asyncio.sleep(30)
            raise RoutingError("unreachable")

    monkeypatch.setattr("app.services.alarm_outbox.process_incident_alarm", no_process)
    monkeypatch.setattr("app.routers.api_v1._geocode_incident", no_geocode)
    monkeypatch.setattr("app.services.einsatz_routing.get_provider", lambda: HaengenderProvider())
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_TIMEOUT_SECONDS", -0.8)

    owner = org(db)
    raw = generate_api_key()
    db.add(ApiKey(key_hash=hash_api_key(raw), label="Routing-down", org_id=owner.id))
    db.commit()

    payload = {"Key": f"routing-down-{uuid4().hex}", "Stufe": "t1", "Ort": "Wolfurt",
               "Strasse": f"Sperrgasse-{uuid4().hex[:6]}", "HausNr": "1", "Uebung": True}
    begonnen = time.monotonic()
    response = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": raw})
    dauer = time.monotonic() - begonnen
    assert response.status_code == 200 and response.json()["created"] is True
    assert dauer < 5, f"Einsatzanlage dauerte {dauer:.1f}s"
    incident_id = response.json()["id"]

    db.expire_all()
    jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident_id).all()
    assert {job.channel for job in jobs} == {"sms", "push", "teams"}
    # Die Anlage selbst erzeugt keine Route – der Loop ist der einzige Erzeuger.
    assert db.query(IncidentRoute).filter_by(incident_id=incident_id).count() == 0

    incident = db.get(Incident, incident_id)
    incident.lat, incident.lng = DEST
    db.commit()
    asyncio.run(run_incident_route_once())
    db.expire_all()
    route = db.query(IncidentRoute).filter_by(incident_id=incident_id).one()
    assert route.status == "error"
    assert db.get(Incident, incident_id).status == "active"
