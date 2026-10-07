"""Integrationstests für die sperrenbezogene Einsatz-Anfahrt."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.road_closure import IncidentRoadClosure, IncidentRoute, RoadClosure, RoadClosureShare
from app.services.einsatz_routing import RouteResponse, RouteResult, RoutingError
from app.services.road_closure_incident_service import compute_incident_route, route_payload

START = (47.47, 9.73)
DEST = (47.47, 9.76)
NORMAL = {"type": "LineString", "coordinates": [[9.73, 47.47], [9.76, 47.47]]}
DETOUR = {"type": "LineString", "coordinates": [[9.73, 47.47], [9.745, 47.475], [9.76, 47.47]]}


class FakeProvider:
    name = "fake"

    def __init__(self, responses, *, avoid=True, error=None, sleep=0):
        self.responses = list(responses)
        self.supports_avoid_polygons = avoid
        self.error, self.sleep, self.calls = error, sleep, []

    async def calculate_route(self, start, dest, *, avoid_polygons=None, alternatives=False):
        self.calls.append({"avoid_polygons": avoid_polygons, "alternatives": alternatives})
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.error:
            raise self.error
        return self.responses.pop(0)


def response(primary=NORMAL, alternatives=()):
    return RouteResponse(
        RouteResult(primary, 3000, 300, ["Normalstraße"]),
        [RouteResult(item, 3600, 420, ["Umfahrung"]) for item in alternatives],
        "fake",
        True,
    )


@pytest.fixture
def db(setup_db):
    session = SessionLocal()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def context(db):
    suffix = uuid4().hex
    org = FireDept(slug=f"route-{suffix}", name="Route")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, routing_start_lat=START[0], routing_start_lng=START[1], routing_start_label="Wache"))
    incident = Incident(primary_org_id=org.id, lat=DEST[0], lng=DEST[1], status="active")
    db.add(incident)
    db.flush()
    route = IncidentRoute(org_id=org.id, incident_id=incident.id)
    db.add(route)
    db.flush()
    return org, incident, route


def closure(db, org_id, geometry, *, restriction="closed", status="ok"):
    now = datetime.now(UTC).replace(tzinfo=None)
    item = RoadClosure(
        org_id=org_id,
        title="Sperre",
        street="Teststraße",
        valid_from=now - timedelta(hours=1),
        restriction_type=restriction,
        geometry_geojson=json.dumps(geometry),
        geometry_status=status,
    )
    from app.services.road_closure_geo_service import bbox

    item.bbox_min_lat, item.bbox_min_lng, item.bbox_max_lat, item.bbox_max_lng = bbox(geometry)
    db.add(item)
    db.flush()
    return item


def _assoc(db, route):
    return db.query(IncidentRoadClosure).filter(IncidentRoadClosure.incident_route_id == route.id).count()


def test_no_closure_and_payload(db, context):
    org, incident, route = context
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response()])))
    assert route.status == "ok" and route.alternative_status == "none"
    assert _assoc(db, route) == 0
    assert route_payload(db, incident, org.id)["route"] == NORMAL


def test_closed_route_uses_avoid_polygon_and_detour(db, context):
    org, _, route = context
    closure(db, org.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    detour = RouteResponse(RouteResult(DETOUR, 3600, 420, ["Umfahrung"]), [], "fake", True)
    provider = FakeProvider([response(), detour])
    asyncio.run(compute_incident_route(db, route, provider=provider))
    assert route.status == "affected" and route.alternative_status == "ok"
    assert provider.calls[1]["avoid_polygons"] and route.detour_distance_m == 600


def test_needs_review_and_construction_do_not_route_around(db, context):
    org, _, route = context
    for restriction, status in (("closed", "needs_review"), ("construction", "ok")):
        closure(db, org.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]}, restriction=restriction, status=status)
    provider = FakeProvider([response()])
    asyncio.run(compute_incident_route(db, route, provider=provider))
    assert route.status == "affected" and route.alternative_status == "none" and len(provider.calls) == 1


def test_osrm_alternative_and_unavailable(db, context):
    org, _, route = context
    closure(db, org.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    provider = FakeProvider([response(), response(NORMAL, [DETOUR])], avoid=False)
    asyncio.run(compute_incident_route(db, route, provider=provider))
    assert provider.calls[1]["alternatives"] and route.alternative_status == "ok"


def test_destination_nearby_share_and_errors(db, context, monkeypatch):
    org, incident, route = context
    destination = closure(db, org.id, {"type": "Point", "coordinates": [9.7608, 47.47]})
    nearby = closure(db, org.id, {"type": "Point", "coordinates": [9.747, 47.4727]})
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response()])))
    payload = route_payload(db, incident, org.id)
    assert [item["id"] for item in payload["closures"]] == [destination.id] and payload["nearby_count"] == 1
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([], error=RoutingError("timeout"))))
    assert route.status == "error" and route.attempts == 1 and not route_payload(db, incident, org.id)["route"]
    assert _assoc(db, route) == 0
    assert nearby.id != destination.id


def test_shared_closure_is_visible(db, context):
    org, _, route = context
    other = FireDept(slug=f"other-{uuid4().hex}", name="Andere")
    db.add(other)
    db.flush()
    shared = closure(db, other.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    db.add(RoadClosureShare(road_closure_id=shared.id, org_id=org.id))
    db.flush()
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response(), response(DETOUR)])))
    assert route.status == "affected"


def test_osrm_ohne_freie_alternative_ist_unavailable(db, context):
    org, _, route = context
    closure(db, org.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    provider = FakeProvider([response(), response(NORMAL, [NORMAL])], avoid=False)
    asyncio.run(compute_incident_route(db, route, provider=provider))
    assert route.status == "affected" and route.alternative_status == "unavailable"
    assert route.alternative_route_geojson is None


def test_haengender_provider_endet_als_error(db, context, monkeypatch):
    org, incident, route = context
    monkeypatch.setattr("app.config.settings.EINSATZ_ROUTING_TIMEOUT_SECONDS", -0.9)
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response()], sleep=1)))
    assert route.status == "error" and route.attempts == 1
    assert route_payload(db, incident, org.id)["error"]


def test_kein_start_keine_koordinaten_und_deaktiviert(db, context, monkeypatch):
    org, incident, route = context
    closure(db, org.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response(), response(DETOUR)])))
    assert route.status == "affected" and _assoc(db, route) == 1

    settings_row = db.query(OrgSettings).filter_by(org_id=org.id).one()
    settings_row.routing_start_lat = None
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([])))
    assert route.status == "no_start" and route.route_geojson is None and _assoc(db, route) == 0

    settings_row.routing_start_lat = START[0]
    incident.lat = None
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([])))
    assert route.status == "no_location" and _assoc(db, route) == 0

    incident.lat = DEST[0]
    monkeypatch.setattr("app.config.settings.EINSATZ_ROUTING_ENABLED", False)
    asyncio.run(compute_incident_route(db, route))
    assert route.status == "disabled"


def test_fremde_nicht_geteilte_sperre_wird_ignoriert(db, context):
    org, _, route = context
    other = FireDept(slug=f"fremd-{uuid4().hex}", name="Fremd")
    db.add(other)
    db.flush()
    closure(db, other.id, {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    asyncio.run(compute_incident_route(db, route, provider=FakeProvider([response()])))
    assert route.status == "ok" and _assoc(db, route) == 0


def test_notify_schluckt_fehler(monkeypatch):
    from app.services import road_closure_incident_service as svc

    async def kaputt(*args, **kwargs):
        raise RuntimeError("ws down")

    monkeypatch.setattr(svc.manager, "broadcast", kaputt)
    monkeypatch.setattr(svc, "broadcast_org", kaputt)
    asyncio.run(svc.notify_route_updated(1, 1))


def test_payload_ohne_route_ist_pending(db, context):
    org, incident, route = context
    db.delete(route)
    db.flush()
    assert route_payload(db, incident, org.id) == {"status": "pending"}
