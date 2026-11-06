import importlib.util
import io
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from shapely.geometry import Point, shape

from app.core.tenant import TenantContextMissing, set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.road_closure import RoadClosure, RoadClosureChange, RoadClosureSection
from app.services import road_closure_geo_service as geo
from app.services import road_closure_service as service


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
def org(db):
    org = FireDept(slug=f"sections-{uuid4().hex}", name="Abschnittstest")
    db.add(org)
    db.flush()
    return org


def _data(street, **values):
    return {
        "title": "Abschnittstest", "street": street,
        "valid_from": datetime(2026, 11, 6), "restriction_type": "closed", **values,
    }


def _point(lng=9.75, lat=47.47):
    return {"type": "Point", "coordinates": [lng, lat]}


def _line(lng=9.75, lat=47.47):
    return {"type": "LineString", "coordinates": [[lng, lat], [lng + 0.002, lat]]}


def test_aggregate_one_and_five_sections(db, org):
    closure = service.create_closure(db, org.id, None, _data(
        "P3a-Einzelweg", sections=[{
            "street": "P3a-Einzelweg", "from_text": "Anfang", "to_text": "Ende",
            "direction": "beide", "geometry_geojson": _point(), "geometry_quality": "manuell",
        }],
    ))
    assert closure.street == "P3a-Einzelweg"
    assert (closure.from_text, closure.to_text, closure.direction) == ("Anfang", "Ende", "both")
    assert closure.geometry_status == "ok"
    assert json.loads(closure.geometry_geojson) == _point()
    streets = [f"P3a-Fuenferweg-{i}" for i in range(5)]
    service.set_sections(db, closure, [
        {"street": street, "from_text": "Start", "to_text": "Ziel", "direction": "both",
         "geometry_geojson": _line(9.75 + i * 0.01), "geometry_quality": "mittel" if i == 2 else "hoch"}
        for i, street in enumerate(streets)
    ], None)
    assert closure.street == ", ".join(streets)
    assert (closure.from_text, closure.to_text, closure.direction) == (None, None, None)
    assert closure.geometry_quality == "mittel"
    assert json.loads(closure.geometry_geojson)["type"] == "MultiLineString"
    db.flush()
    db.expire(closure, ["sections"])
    assert [section.position for section in closure.sections] == list(range(5))


def test_aggregate_deduplicates_orders_and_shortens_streets():
    streets = [f"P3a-Kuerzungsstrasse-{i}-" + "x" * 25 for i in range(5)]
    closure = RoadClosure(sections=[
        RoadClosureSection(position=i, street=street, geometry_status="missing")
        for i, street in reversed(list(enumerate(streets)))
    ])
    service._aggregate_sections(closure)
    assert closure.street == f"{streets[0]}, {streets[1]} +3"
    closure.sections.append(RoadClosureSection(position=6, street=streets[0], geometry_status="missing"))
    service._aggregate_sections(closure)
    assert closure.street.endswith(" +3")
    closure.sections = [RoadClosureSection(position=i, street="P3a-Lang-" + str(i) * 190) for i in range(2)]
    service._aggregate_sections(closure)
    assert len(closure.street) <= 200


def test_mixed_geometry_collection_helpers():
    polygon = {"type": "Polygon", "coordinates": [[[9.76, 47.47], [9.761, 47.47],
                [9.761, 47.471], [9.76, 47.47]]]}
    merged = geo.merge_geometries([_line(), polygon, _point(9.77)])
    assert merged["type"] == "GeometryCollection"
    assert geo.validate_geometry(merged) == merged
    assert geo.bbox(merged) == (47.47, 9.75, 47.471, 9.77)
    assert shape(geo.avoid_polygon(merged)).contains(Point(9.77, 47.47))
    assert geo.distance_point_to_geometry_m(47.47, 9.77, merged) < 0.01
    assert geo.classify(_line(9.769), (47.47, 9.771), [(1, merged)])[0].relevance == "route"


@pytest.mark.parametrize("geometries", [
    [], [{"type": "Unsupported"}], [_point(181)], [{"type": "LineString", "coordinates": [[9, 47]]}],
])
def test_invalid_geometry_collection(geometries):
    with pytest.raises(ValueError):
        geo.validate_geometry({"type": "GeometryCollection", "geometries": geometries})


def test_merge_homogeneous_and_empty():
    point = _point()
    assert geo.merge_geometries([]) is None
    assert geo.merge_geometries([point]) is point
    multipoint = geo.merge_geometries([point, _point(9.76)])
    assert multipoint["type"] == "MultiPoint"
    assert geo.validate_geometry(multipoint) == multipoint
    assert geo.merge_geometries([multipoint, point])["coordinates"] == [[9.75, 47.47], [9.76, 47.47], [9.75, 47.47]]
    polygon = {"type": "Polygon", "coordinates": [[[9, 47], [10, 47], [10, 48], [9, 47]]]}
    assert geo.merge_geometries([polygon, polygon]) == {"type": "MultiPolygon", "coordinates": [polygon["coordinates"]] * 2}


def test_status_ok_only_when_all_sections_ok(db, org):
    closure = service.create_closure(db, org.id, None, _data("P3a-Statusweg"))
    sections = [
        {"street": f"P3a-Statusabschnitt-{i}", "geometry_geojson": _point(9.75 + i * 0.01),
         "geometry_status": "ok", "geometry_quality": quality}
        for i, quality in enumerate(["manuell", "niedrig"])
    ]
    service.set_sections(db, closure, sections, None)
    assert closure.geometry_status == "ok"
    assert closure.geometry_quality == "niedrig"
    sections[1]["geometry_status"] = "needs_review"
    service.set_sections(db, closure, sections, None)
    assert closure.geometry_status == "needs_review"
    sections[1]["geometry_geojson"] = None
    service.set_sections(db, closure, sections, None)
    assert closure.geometry_status == "needs_review"
    sections[0]["geometry_geojson"] = None
    service.set_sections(db, closure, sections, None)
    assert closure.geometry_status == "missing"
    assert closure.bbox_min_lat is None


def test_legacy_update_creates_and_synchronizes_section_zero(db, org):
    closure = RoadClosure(org_id=org.id, **_data("P3a-Altbestandsweg"), priority="normal", geometry_status="missing")
    db.add(closure)
    db.flush()
    service.update_closure(db, closure, None, {"from_text": "P3a-Altanfang"})
    assert len(closure.sections) == 1
    assert closure.sections[0].position == 0
    service.update_closure(db, closure, None, {"street": "P3a-Altneu", "geometry_geojson": _line()})
    assert closure.sections[0].street == "P3a-Altneu"
    assert closure.sections[0].geometry_geojson == closure.geometry_geojson
    service.confirm_geometry(db, closure, None, _point())
    assert closure.sections[0].geometry_status == "ok"
    assert closure.sections[0].geometry_quality == "manuell"
    assert json.loads(closure.sections[0].geometry_geojson) == _point()


def test_set_sections_version_change_audit_and_noop(db, org, monkeypatch):
    from app.services import road_closure_notify_service
    closure = service.create_closure(db, org.id, None, _data("P3a-Auditweg"))
    version = closure.version
    stale = MagicMock()
    enqueue = MagicMock()
    monkeypatch.setattr(service, "mark_routes_stale_for_closure", stale)
    monkeypatch.setattr(road_closure_notify_service, "enqueue", enqueue)
    sections = [{"street": "P3a-Auditabschnitt", "geometry_geojson": _point()}]
    changed = service.set_sections(db, closure, sections, None, source="mcp", mcp_tool="p3a_test")
    db.flush()
    assert closure.version == version + 1
    assert changed[0]["feld"] == "sections"
    change = db.query(RoadClosureChange).filter_by(road_closure_id=closure.id, field="sections").one()
    assert json.loads(change.before_json)[0]["street"] == "P3a-Auditweg"
    assert json.loads(change.after_json)[0]["street"] == "P3a-Auditabschnitt"
    assert change.source == "mcp"
    assert change.mcp_tool == "p3a_test"
    stale.assert_called_once()
    enqueue.assert_called_once()
    assert service.set_sections(db, closure, sections, None) == []
    assert closure.version == version + 1
    stale.assert_called_once()
    enqueue.assert_called_once()
    service.update_closure(db, closure, None, {"sections": [{"street": "P3a-Updateabschnitt"}]})
    assert closure.street == "P3a-Updateabschnitt"
    assert len(closure.sections) == 1


def test_set_sections_validates_before_replacing(db, org):
    closure = service.create_closure(db, org.id, None, _data("P3a-Validierungsweg"))
    original = closure.sections[0]
    for bad in ({}, {"street": "P3a-Ungueltig", "direction": "unknown"},
                {"street": "P3a-Ungueltig", "geometry_geojson": _point(181)}):
        with pytest.raises(ValueError):
            service.set_sections(db, closure, [{"street": "P3a-Gueltig"}, bad], None)
        assert closure.sections == [original]


def test_classify_point_on_third_multiline_section():
    merged = geo.merge_geometries([_line(9.75), _line(9.8), _line(9.85)])
    result = geo.classify(None, (47.47, 9.851), [(3, merged)])
    assert result[0].closure_id == 3
    assert result[0].relevance == "destination"
    assert result[0].distance_to_destination_m == 0


def test_section_tenant_isolation_and_fail_closed(db, org):
    other = FireDept(slug=f"sections-other-{uuid4().hex}", name="Andere Organisation")
    db.add(other)
    db.flush()
    closure = service.create_closure(db, org.id, None, _data("P3a-Tenantweg"))
    db.flush()
    section_id = closure.sections[0].id
    set_tenant_context(db, other.id)
    assert db.query(RoadClosureSection).filter_by(id=section_id).first() is None
    db.info.pop("current_org_id")
    with pytest.raises(TenantContextMissing):
        db.query(RoadClosureSection).all()
    set_tenant_context(db, None)


def test_staticmap_collection_and_multipoint(monkeypatch):
    import staticmap
    from app.services import staticmap_service

    map_mock = MagicMock()
    map_mock.render.return_value = Image.new("RGB", (80, 45))
    monkeypatch.setattr(staticmap, "StaticMap", MagicMock(return_value=map_mock))
    staticmap_service._ROAD_CLOSURE_RENDER_CACHE.clear()
    geometry = geo.merge_geometries([_line(), geo.merge_geometries([_point(), _point(9.76)])])
    image = staticmap_service.render_road_closure_map_png(geometry, size=(80, 45))
    assert Image.open(io.BytesIO(image)).size == (80, 45)
    assert map_mock.add_marker.call_count == 2
    assert map_mock.add_line.call_count == 2


def test_migration_backfills_existing_fields_and_downgrades():
    spec = importlib.util.spec_from_file_location("sections_migration", Path("alembic/versions/0351_road_closure_sections.py"))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("fire_dept", metadata, sa.Column("id", sa.BigInteger, primary_key=True))
    columns = [
        sa.Column("id", sa.BigInteger, primary_key=True), sa.Column("org_id", sa.BigInteger),
        *[sa.Column(field, sa.Text) for field in service._SECTION_FIELDS],
        sa.Column("created_at", sa.DateTime), sa.Column("updated_at", sa.DateTime),
    ]
    old = sa.Table("road_closure", metadata, *columns)
    metadata.create_all(engine)
    now = datetime(2026, 11, 6)
    with engine.begin() as connection:
        for index, street, geometry in [(1, "P3a-Migrationsweg", None), (2, None, json.dumps(_point())), (3, None, None)]:
            connection.execute(old.insert().values(
                id=index, org_id=17, street=street, geometry_geojson=geometry,
                from_text="P3a-Migrationsanfang", to_text="P3a-Migrationsende", direction="both",
                geometry_status="ok" if geometry else "missing", geometry_quality="hoch",
                geometry_meta_json='{"p3a": true}', created_at=now, updated_at=now,
            ))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            rows = connection.execute(sa.text("SELECT * FROM road_closure_section ORDER BY road_closure_id")).mappings().all()
            assert [row["road_closure_id"] for row in rows] == [1, 2]
            assert all(row["org_id"] == 17 and row["position"] == 0 for row in rows)
            assert rows[0]["street"] == "P3a-Migrationsweg"
            assert rows[0]["from_text"] == "P3a-Migrationsanfang"
            assert rows[1]["geometry_geojson"] == json.dumps(_point())
            assert rows[1]["geometry_quality"] == "hoch"
            migration.downgrade()
            assert not sa.inspect(connection).has_table("road_closure_section")


def test_create_with_sections_enqueues_once_and_keeps_version_one(db, org, monkeypatch):
    from app.services import road_closure_notify_service
    enqueue = MagicMock()
    monkeypatch.setattr(road_closure_notify_service, "enqueue", enqueue)
    closure = service.create_closure(db, org.id, None, _data("P3a-Anlageweg", sections=[
        {"street": "P3a-AnlageA", "geometry_geojson": _line(9.71)},
        {"street": "P3a-AnlageB", "geometry_geojson": _line(9.72)},
    ]))
    db.flush()
    assert closure.version == 1
    assert len(closure.sections) == 2
    assert closure.street == "P3a-AnlageA, P3a-AnlageB"
    assert [call.args[2] for call in enqueue.call_args_list] == ["neu"]
    assert db.query(RoadClosureChange).filter_by(road_closure_id=closure.id, field="sections").count() == 0


def test_update_fields_and_sections_bumps_version_and_enqueues_once(db, org, monkeypatch):
    from app.services import road_closure_notify_service
    closure = service.create_closure(db, org.id, None, _data("P3a-Kombiweg"))
    db.flush()
    version = closure.version
    enqueue = MagicMock()
    monkeypatch.setattr(road_closure_notify_service, "enqueue", enqueue)
    changed = service.update_closure(db, closure, None, {
        "title": "P3a-Kombititel",
        "sections": [{"street": "P3a-KombiA"}, {"street": "P3a-KombiB"}],
    })
    assert closure.version == version + 1
    assert {item["feld"] for item in changed} == {"title", "sections"}
    assert [call.args[2] for call in enqueue.call_args_list] == ["geaendert"]
    assert closure.street == "P3a-KombiA, P3a-KombiB"


def test_confirm_geometry_multi_sections_marks_all_ok(db, org):
    closure = service.create_closure(db, org.id, None, _data("P3a-Bestaetigungsweg", sections=[
        {"street": "P3a-BestA", "geometry_geojson": _line(9.73), "geometry_status": "needs_review"},
        {"street": "P3a-BestB", "geometry_geojson": _line(9.74), "geometry_status": "needs_review"},
    ]))
    db.flush()
    assert closure.geometry_status == "needs_review"
    with pytest.raises(ValueError):
        service.confirm_geometry(db, closure, None, _line(9.9))
    service.confirm_geometry(db, closure, None)
    assert all(section.geometry_status == "ok" for section in closure.sections)
    service._aggregate_sections(closure)
    assert closure.geometry_status == "ok"
