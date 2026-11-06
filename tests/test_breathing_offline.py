"""Offline-Ereignisse: atomare Replays, Zeitstempel und Druckkonflikte."""
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.breathing import BreathingClientEvent, BreathingKontrolle, PressureLog
from app.services.breathing_events import claim_client_event, cleanup_client_events, parse_erfasst_at, valid_client_event_id
from tests.test_breathing_routes_phase1 import _active, _user, breathing_routes  # noqa: F401


@pytest.mark.parametrize("route", ["druck", "kontrolle", "status", "notfall", "meldung",
                                   "einsatzziel", "enddruck", "ack", "funkverlust",
                                   "standort", "auftrag", "details"])
def test_route_replay(breathing_routes, route):
    ctx = breathing_routes
    _active(ctx)
    member = ctx.troop.active_members[0]
    data = {"druck": {"troop_member_id": member.id, "pressure_bar": 200},
            "kontrolle": {"stufe": "frei", "pressure_bar": 200},
            "status": {"status": "rueckzug"}, "notfall": {"bestaetigt": "1"},
            "meldung": {"text": "Offline"},
            "einsatzziel": {"troop_member_id": member.id, "pressure_bar": 200},
            "enddruck": {"troop_member_id": member.id, "pressure_bar": 100},
            "ack": {"kind": "max_time"}, "funkverlust": {"aktiv": "1"},
            "standort": {"location_text": "Stiege"}, "auftrag": {"task_text": "Suche"},
            "details": {"funkkanal": "3"}}[route]
    if route == "enddruck":
        ctx.troop.status = "rueckzug"
        ctx.db.commit()
    key = uuid4().hex
    data.update(client_event_id=key, erfasst_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    expected = 303 if route == "status" else 204
    assert ctx.post(f"{ctx.url}/{route}", data).status_code == expected
    calls = ctx.broadcast.await_count
    assert calls > 0
    assert ctx.post(f"{ctx.url}/{route}", data).status_code == expected
    assert ctx.broadcast.await_count == calls
    ctx.db.expire_all()
    assert ctx.db.query(BreathingClientEvent).filter_by(incident_id=ctx.incident_id, client_event_id=key).count() == 1
    if route in ("druck", "kontrolle"):
        logs = ctx.db.query(PressureLog).filter_by(troop_id=ctx.troop.id).all()
        assert len(logs) == 1 and logs[0].nachgetragen
        assert logs[0].ts == datetime.fromisoformat(data["erfasst_at"]).replace(tzinfo=None)
    if route == "kontrolle":
        kontrolle = ctx.db.query(BreathingKontrolle).filter_by(troop_id=ctx.troop.id).one()
        assert kontrolle.nachgetragen and kontrolle.erledigt_at == logs[0].ts
    if route == "status":
        assert ctx.troop.withdraw_at == datetime.fromisoformat(data["erfasst_at"]).replace(tzinfo=None)


@pytest.mark.parametrize("extra", [{"client_event_id": "invalid!"}, {"client_event_id": "short"},
                                   {"erfasst_at": "bad"}, {"erfasst_at": "2000-01-01T00:00:00Z"}])
def test_invalid_event(breathing_routes, extra):
    ctx = breathing_routes
    assert ctx.post(f"{ctx.url}/meldung", {"text": "Test", **extra}).status_code == 400
    ctx.broadcast.assert_not_awaited()


def test_pressure_order_correction_and_control(breathing_routes):
    ctx = breathing_routes
    _active(ctx)
    member = ctx.troop.active_members[0]
    now = datetime.now(UTC)
    def pressure(value, **extra):
        return ctx.post(f"{ctx.url}/druck", {"troop_member_id": member.id, "pressure_bar": value, **extra})
    assert pressure(180, erfasst_at=now.isoformat()).status_code == 204
    assert pressure(250, erfasst_at=(now - timedelta(minutes=5)).isoformat()).status_code == 204
    ctx.db.refresh(member)
    ctx.db.refresh(ctx.troop)
    assert member.current_press == 180
    assert ctx.troop.last_meldung_at == now.replace(tzinfo=None)
    key = uuid4().hex
    response = pressure(250, client_event_id=key)
    assert response.status_code == 409 and "Druck kann nicht steigen" in response.text
    assert ctx.db.query(BreathingClientEvent).filter_by(client_event_id=key).count() == 0
    assert pressure(250, client_event_id=key, korrektur_grund="  Ablesefehler  ", note="Test").status_code == 204
    ctx.db.expire_all()
    log = ctx.db.query(PressureLog).filter_by(troop_id=ctx.troop.id).order_by(PressureLog.id.desc()).first()
    assert log.note == "Korrektur: Ablesefehler; Test"
    assert ctx.post(f"{ctx.url}/kontrolle", {"stufe": "frei", "pressure_bar": 290,
                                            "troop_member_id": member.id}).status_code == 204
    ctx.db.refresh(member)
    assert member.current_press == 290


def test_timestamp_boundaries():
    now = datetime(2026, 11, 6, 12)
    assert parse_erfasst_at(None, now) == (now, False)
    assert parse_erfasst_at("", now) == (now, False)
    assert parse_erfasst_at("2026-11-06T13:00:00+01:00", now) == (now, False)
    assert parse_erfasst_at("2026-11-06T12:02:01Z", now) == (now, False)
    assert parse_erfasst_at("2026-11-06T12:02:00Z", now) == (now + timedelta(seconds=120), False)
    assert parse_erfasst_at("2026-11-06T11:59:30Z", now)[1] is False
    assert parse_erfasst_at("2026-11-06T00:00:00Z", now)[1] is True
    for value in ("2026-11-05T23:59:59Z", "2026-11-06T12:00:00", "bad"):
        with pytest.raises(ValueError):
            parse_erfasst_at(value, now)
    for length in (8, 40):
        assert valid_client_event_id("a" * length) == "a" * length
    with pytest.raises(ValueError):
        valid_client_event_id("a" * 41)


def test_claim_scope_rollback_and_cleanup(breathing_routes):
    ctx = breathing_routes
    db = ctx.db
    now = datetime.now(UTC).replace(tzinfo=None)
    user_id = _user(ctx).id
    key = uuid4().hex
    assert claim_client_event(db, ctx.incident_id, user_id, key, "meldung", now, False)
    assert not claim_client_event(db, ctx.incident_id, user_id, key, "meldung", now, False)
    assert claim_client_event(db, ctx.other_incident_id, user_id, key, "meldung", now, False)
    db.rollback()
    assert db.query(BreathingClientEvent).filter_by(client_event_id=key).count() == 0
    for i in range(201):
        db.add(BreathingClientEvent(incident_id=ctx.incident_id, client_event_id=uuid4().hex,
                                   route="meldung", empfangen_at=now - timedelta(days=15)))
    fresh = BreathingClientEvent(incident_id=ctx.incident_id, client_event_id=uuid4().hex,
                                route="meldung", empfangen_at=now - timedelta(days=14))
    db.add(fresh)
    db.commit()
    assert cleanup_client_events(db, now)
    db.flush()
    assert db.query(BreathingClientEvent).filter_by(incident_id=ctx.incident_id).count() == 2
    assert cleanup_client_events(db, now)
    db.flush()
    assert not cleanup_client_events(db, now)
    assert db.get(BreathingClientEvent, fresh.id) is fresh


def test_migration_upgrade_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0350_asuew_phase5.py"
    spec = importlib.util.spec_from_file_location("migration_0350", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0350", "0349")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'offline.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for name in ("incident", "user", "pressure_log", "breathing_kontrolle"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
            conn.exec_driver_sql(f'INSERT INTO "{name}" (id) VALUES (1)')
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            inspector = sa.inspect(conn)
            table = BreathingClientEvent.__table__
            columns = inspector.get_columns(table.name)
            assert {c["name"] for c in columns} == set(table.c.keys())
            for column in columns:
                expected = table.c[column["name"]]
                assert column["nullable"] == expected.nullable
                assert column["type"]._type_affinity == expected.type._type_affinity
                if isinstance(expected.type, sa.String):
                    assert column["type"].length == expected.type.length
                if expected.name == "id" or expected.name.endswith("_id") and expected.name != "client_event_id":
                    assert isinstance(expected.type, sa.BigInteger)
            fks = inspector.get_foreign_keys(table.name)
            assert {fk["constrained_columns"][0]: fk["options"]["ondelete"] for fk in fks} == {
                "incident_id": "CASCADE", "user_id": "SET NULL"}
            assert inspector.get_unique_constraints(table.name)[0]["column_names"] == ["incident_id", "client_event_id"]
            assert inspector.get_indexes(table.name)[0]["column_names"] == ["empfangen_at"]
            for name in ("pressure_log", "breathing_kontrolle"):
                assert conn.exec_driver_sql(f"SELECT nachgetragen FROM {name}").scalar() == 0
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert table.name not in sa.inspect(conn).get_table_names()
            for name in ("pressure_log", "breathing_kontrolle"):
                assert [c["name"] for c in sa.inspect(conn).get_columns(name)] == ["id"]
    engine.dispose()


def test_future_clamped_and_status_conflict(breathing_routes):
    from app.models.incident import IncidentChange
    import json

    ctx = breathing_routes
    _active(ctx)
    before = datetime.now(UTC).replace(tzinfo=None)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert ctx.post(f"{ctx.url}/meldung", {"text": "Future", "erfasst_at": future}).status_code == 204
    ctx.db.refresh(ctx.troop)
    assert before <= ctx.troop.last_meldung_at <= datetime.now(UTC).replace(tzinfo=None)
    change = ctx.db.query(IncidentChange).filter_by(incident_id=ctx.incident_id, action="troop.meldung").one()
    after = json.loads(change.after_json)
    assert after["nachgetragen"] is False
    assert after["erfasst_at"] == ctx.troop.last_meldung_at.isoformat()
    assert ctx.post(f"{ctx.url}/status", {"status": "zurueck", "client_event_id": uuid4().hex}).status_code == 303
    calls = ctx.broadcast.await_count
    key = uuid4().hex
    assert ctx.post(f"{ctx.url}/status", {"status": "rueckzug", "client_event_id": key}).status_code == 409
    assert ctx.broadcast.await_count == calls
    assert ctx.db.query(BreathingClientEvent).filter_by(client_event_id=key).count() == 0
