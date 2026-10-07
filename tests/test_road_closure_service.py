import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.road_closure import IncidentRoute, RoadClosure, RoadClosureChange, RoadClosureShare
from app.models.user import AuditLog
from app.services import road_closure_service as service


def _closure(**values):
    now = datetime.now(UTC).replace(tzinfo=None)
    data = {
        "title": "Test",
        "street": "Hauptstraße",
        "valid_from": now,
        "restriction_type": "closed",
        "priority": "normal",
        "geometry_status": "missing",
    }
    data.update(values)
    return RoadClosure(**data)


def test_compute_status():
    now = datetime(2026, 1, 2)
    assert service.compute_status(_closure(valid_from=now + timedelta(days=1)), now) == "planned"
    assert service.compute_status(_closure(valid_from=now - timedelta(days=1)), now) == "active"
    assert (
        service.compute_status(_closure(valid_from=now - timedelta(days=2), valid_until=now - timedelta(days=1)), now)
        == "expired"
    )
    assert service.compute_status(_closure(valid_from=now, cancelled_at=now), now) == "cancelled"


def test_validate_closure_data_rejects_bad_input():
    base = {
        "title": "Sperre",
        "street": "Hauptstraße",
        "valid_from": datetime(2026, 1, 1),
        "restriction_type": "closed",
        "priority": "normal",
    }
    assert service.validate_closure_data(base, partial=False)["title"] == "Sperre"
    for changes in (
        {"valid_until": datetime(2025, 1, 1)},
        {"restriction_type": "unknown"},
        {"max_weight_t": 61},
        {"geometry_geojson": "{"},
        {"unknown": 1},
    ):
        data = base | changes
        with pytest.raises(ValueError):
            service.validate_closure_data(data, partial=False)


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
def orgs(db):
    suffix = uuid4().hex
    org_a = FireDept(slug=f"sperre-a-{suffix}", name="Sperre A")
    org_b = FireDept(slug=f"sperre-b-{suffix}", name="Sperre B")
    db.add_all([org_a, org_b])
    db.flush()
    return org_a, org_b


def _geometry(lat=47.47, lng=9.75):
    return {"type": "Point", "coordinates": [lng, lat]}


def _data(now, **changes):
    values = {
        "title": "Bregenzer Sperre",
        "street": "Bregenzer Str.",
        "from_text": "Nord",
        "to_text": "Sued",
        "valid_from": now - timedelta(hours=1),
        "valid_until": now + timedelta(hours=1),
        "restriction_type": "closed",
        "geometry_geojson": _geometry(),
    }
    values.update(changes)
    return values


def test_create_update_and_audit(db, orgs):
    org_a, _ = orgs
    now = datetime(2026, 1, 2, 12)
    closure = service.create_closure(db, org_a.id, None, _data(now))
    db.flush()

    assert closure.priority == "normal"
    assert closure.geometry_status == "ok"
    assert closure.bbox_min_lat == pytest.approx(47.47)
    assert db.query(RoadClosureChange).filter_by(road_closure_id=closure.id, action="created").count() == 1
    created_audit = db.query(AuditLog).filter_by(action="road_closure.created").one()
    assert json.loads(created_audit.payload_json)["title"] == closure.title

    changed = service.update_closure(
        db,
        closure,
        None,
        {"title": "Neue Sperre", "geometry_geojson": None},
        expected_version=1,
    )
    db.flush()
    assert {entry["feld"] for entry in changed} == {"title", "geometry_geojson", "geometry_status"}
    assert closure.geometry_status == "missing"
    assert closure.version == 2
    assert db.query(RoadClosureChange).filter_by(road_closure_id=closure.id, field="geometry_status").count() == 1
    updated_audit = db.query(AuditLog).filter_by(action="road_closure.updated").one()
    assert "geometry_status" in json.loads(updated_audit.payload_json)["felder"]

    assert service.update_closure(db, closure, None, {}, expected_version=2) == []
    assert closure.version == 2
    with pytest.raises(ValueError):
        service.update_closure(db, closure, None, {}, expected_version=1)


def test_invalid_create_does_not_write_closure(db, orgs):
    org_a, _ = orgs
    before = db.query(RoadClosure).filter_by(org_id=org_a.id).count()
    with pytest.raises(ValueError):
        service.create_closure(
            db,
            org_a.id,
            None,
            _data(datetime(2026, 1, 2), restriction_type="unbekannt"),
        )
    assert db.query(RoadClosure).filter_by(org_id=org_a.id).count() == before


def test_deactivate_reactivate_delete_and_visibility(db, orgs):
    org_a, org_b = orgs
    now = datetime(2026, 1, 2, 12)
    closure = service.create_closure(db, org_a.id, None, _data(now))
    db.flush()
    service.deactivate_closure(db, closure, None, "Baustelle")
    db.flush()
    assert service.compute_status(closure, now) == "cancelled"
    with pytest.raises(ValueError):
        service.deactivate_closure(db, closure, None, "Doppelt")
    assert (
        json.loads(db.query(AuditLog).filter_by(action="road_closure.deactivated").one().payload_json)["grund"]
        == "Baustelle"
    )
    service.reactivate_closure(db, closure, None)
    assert closure.cancelled_at is None

    with pytest.raises(ValueError):
        service.get_closure_for_org(db, org_b.id, closure.id, writable=False)
    db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_b.id))
    db.flush()
    assert service.get_closure_for_org(db, org_b.id, closure.id, writable=False).id == closure.id
    with pytest.raises(ValueError):
        service.get_closure_for_org(db, org_b.id, closure.id, writable=True)
    assert closure in service.list_closures(db, org_a.id, scope="own")
    assert closure in service.list_closures(db, org_b.id, scope="shared")

    service.delete_closure(db, closure, None)
    db.flush()
    assert db.get(RoadClosure, closure.id) is None
    deleted = db.query(AuditLog).filter_by(action="road_closure.deleted").one()
    assert json.loads(deleted.payload_json)["title"] == "Bregenzer Sperre"


def test_list_bbox_and_duplicates(db, orgs):
    org_a, _ = orgs
    now = datetime(2026, 1, 2, 12)
    active = service.create_closure(db, org_a.id, None, _data(now))
    planned = service.create_closure(
        db,
        org_a.id,
        None,
        _data(
            now,
            title="Geplant",
            valid_from=now + timedelta(days=1),
            valid_until=now + timedelta(days=2),
        ),
    )
    expired = service.create_closure(
        db,
        org_a.id,
        None,
        _data(now, title="Abgelaufen", valid_from=now - timedelta(days=2), valid_until=now - timedelta(days=1)),
    )
    cancelled = service.create_closure(db, org_a.id, None, _data(now, title="Deaktiviert"))
    service.deactivate_closure(db, cancelled, None, "Ende")
    db.flush()

    assert active in service.list_closures(db, org_a.id, status="active", now=now)
    assert planned in service.list_closures(db, org_a.id, status="planned", now=now)
    assert expired in service.list_closures(db, org_a.id, status="expired", now=now)
    assert cancelled in service.list_closures(db, org_a.id, status="cancelled", now=now)
    assert planned in service.list_closures(db, org_a.id, status="current", now=now)
    assert active in service.list_closures(db, org_a.id, text="Bregenzer", restriction_type="closed")
    assert active in service.list_closures(db, org_a.id, von=now, bis=now)
    assert active in service.active_closures_in_bbox(db, org_a.id, 47.46, 9.74, 47.48, 9.76, now)
    assert not service.active_closures_in_bbox(db, org_a.id, 48, 10, 48.1, 10.1, now)

    duplicates = service.find_duplicates(
        db,
        org_a.id,
        street="Bregenzer Straße",
        from_text="Nord",
        to_text="Sued",
        valid_from=now,
        valid_until=now + timedelta(hours=2),
    )
    assert active in duplicates
    assert active not in service.find_duplicates(
        db,
        org_a.id,
        street="Bregenzer Straße",
        from_text="Nord",
        to_text="Sued",
        valid_from=now + timedelta(days=3),
        valid_until=now + timedelta(days=4),
    )
    assert active not in service.find_duplicates(
        db,
        org_a.id,
        street="Bregenzer Straße",
        from_text="Nord",
        to_text="Sued",
        valid_from=now,
        valid_until=now + timedelta(hours=2),
        exclude_id=active.id,
    )


def test_mark_routes_stale_for_closure(db, orgs):
    org_a, org_b = orgs
    now = datetime(2026, 1, 2, 12)
    closure = service.create_closure(db, org_a.id, None, _data(now))
    db.flush()
    db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_b.id))
    active = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    far = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    closed = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="closed")
    shared = Incident(primary_org_id=org_b.id, alarm_type_code="T1", status="active")
    db.add_all([active, far, closed, shared])
    db.flush()
    near_route = json.dumps({"type": "LineString", "coordinates": [[9.74, 47.47], [9.76, 47.47]]})
    far_route = json.dumps({"type": "LineString", "coordinates": [[9.9, 47.6], [9.91, 47.6]]})
    routes = [
        IncidentRoute(org_id=org_a.id, incident_id=active.id, route_geojson=near_route, dest_lat=47.47, dest_lng=9.75),
        IncidentRoute(org_id=org_a.id, incident_id=far.id, route_geojson=far_route, dest_lat=47.6, dest_lng=9.91),
        IncidentRoute(org_id=org_a.id, incident_id=closed.id, route_geojson=near_route, dest_lat=47.47, dest_lng=9.75),
        IncidentRoute(org_id=org_b.id, incident_id=shared.id, route_geojson=near_route, dest_lat=47.47, dest_lng=9.75),
    ]
    db.add_all(routes)
    db.flush()
    service.mark_routes_stale_for_closure(db, closure)
    assert routes[0].stale is True
    assert routes[1].stale is False
    assert routes[2].stale is False
    assert routes[3].stale is True
