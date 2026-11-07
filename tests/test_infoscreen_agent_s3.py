"""S3: technische Metadaten, Wakebus, Presence und Snapshot-Autoritaet."""
import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDeviceEvent, InfoscreenDisplay
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_service as service
from app.services import ws_bus
from tests.conftest import TestingSession as SessionLocal
from tests.test_infoscreen_agent_s2_api import BASE, setup_display


def body(key):
    return {"agent_version": "1.2.3", "os_version": "Ubuntu 26.04", "hostname": "hall-pc",
            "last_revision": {key: 2}, "config_version": 1,
            "displays": [{"display_key": key, "power": "off", "verified": False,
                          "backend": "logical_blank", "page_state": "ready", "errors": ["power_unverified"]}]}


def test_heartbeat_ok_no_revision_authority(client):
    device_id, display_id, key, headers, _ = setup_display()
    snapshot = client.get(BASE + "/state", params={"display": key}, headers=headers).json()
    response = client.post(BASE + "/heartbeat", json=body(key), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"config_version": 1, "revisions": {key: snapshot["revision"]}}
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device, display = db.get(InfoscreenDevice, device_id), db.get(InfoscreenDisplay, display_id)
        assert device.reported_config_version == 1 and device.config_version == 1
        assert device.last_seen_at and device.agent_version == "1.2.3"
        assert device.os_version == "Ubuntu 26.04" and device.hostname == "hall-pc"
        assert display.last_revision == 2
        assert {
            k: v for k, v in body(key)["displays"][0].items() if k != "display_key"} == display.reported_status
        assert display.revision == snapshot["revision"]


@pytest.mark.parametrize("change", [
    {"hostname": "x" * 256}, {"agent_version": "x" * 101}, {"os_version": "x" * 101},
    {"config_version": -1}, {"config_version": True}, {"incident": "secret"},
    {"last_revision": {"foreign": -1}},
])
def test_heartbeat_invalid_and_no_unexpected_data(client, change):
    device_id, display_id, key, headers, _ = setup_display()
    response = client.post(BASE + "/heartbeat", json=body(key) | change, headers=headers)
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        assert db.get(InfoscreenDisplay, display_id).reported_status is None
        assert db.get(InfoscreenDevice, device_id).last_seen_at is None


@pytest.mark.parametrize("change", [{"errors": ["patient_name"]}, {"url": "https://private"},
                                    {"verified": "false"}, {"page_state": "patient data"},
                                    {"backend": "arbitrary text"}, {"power": "invalid"}])
def test_heartbeat_report_allowlists(client, change):
    _, _, key, headers, _ = setup_display()
    data = body(key)
    data["displays"][0].update(change)
    assert client.post(BASE + "/heartbeat", json=data, headers=headers).status_code == 422


def test_heartbeat_revoked_and_no_auth(client):
    device_id, _, key, headers, _ = setup_display()
    assert client.post(BASE + "/heartbeat", json=body(key)).status_code == 401
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        service.sperren(db.get(InfoscreenDevice, device_id))
        db.commit()
    assert client.post(BASE + "/heartbeat", json=body(key), headers=headers).status_code == 401


def _drain_connect(socket):
    assert socket.receive_json()["type"] == "invalidate"
    assert socket.receive_json() == {"type": "ping"}


def test_ws_auth(client):
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect("/ws/infoscreen-agent", headers=headers):
            pass
        assert error.value.code == 4401


def test_ws_presence_displacement_and_old_finally(client):
    device_id, _, _, headers, _ = setup_display()
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as old:
        _drain_connect(old)
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            device = db.get(InfoscreenDevice, device_id)
            first = device.agent_connection_id
            assert first and device.agent_worker_id and device.agent_presence_expires_at > events.now()
        with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as new:
            _drain_connect(new)
            with pytest.raises(WebSocketDisconnect) as error:
                old.receive_json()
            assert error.value.code == 4401
            old.send_json({"type": "pong"})
            with SessionLocal() as db:
                set_tenant_context(db, 1)
                assert db.get(InfoscreenDevice, device_id).agent_connection_id != first
            new.send_json({"type": "pong"})
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        assert db.get(InfoscreenDevice, device_id).agent_connection_id is None
        types = {e.typ for e in db.query(InfoscreenDeviceEvent).filter(InfoscreenDeviceEvent.device_id == device_id)}
        assert {"connect", "disconnect"} <= types


@pytest.mark.parametrize("rotate", [False, True])
def test_ws_revocation_after_commit(client, rotate):
    device_id, _, _, headers, _ = setup_display()
    def revoke():
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            device = db.get(InfoscreenDevice, device_id)
            if rotate:
                service.rotate_token(db, device)
            else:
                service.sperren(device)
            db.commit()
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        _drain_connect(socket)
        revoke()
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        event = db.query(InfoscreenDeviceEvent).filter_by(device_id=device_id, typ="token_revoked").one()
        assert event.payload_json == {}


def test_ws_reconnect_snapshot_monotonic(client):
    _, display_id, key, headers, _ = setup_display()
    first = client.get(BASE + "/state", params={"display": key}, headers=headers).json()
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        hint = socket.receive_json()
        assert hint["revision"] == first["revision"]
        assert set(hint) == {"type", "reason", "display_key", "revision"}
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        db.get(InfoscreenDisplay, display_id).aktiv = False
        db.commit()
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        # Hinweis darf alt sein. Nur der Snapshot entscheidet und inkrementiert.
        assert socket.receive_json()["revision"] == first["revision"]
        second = client.get(BASE + "/state", params={"display": key}, headers=headers).json()
        assert second["revision"] > first["revision"] and second["mode"] == "locked"
        assert client.get(BASE + "/state", params={"display": key}, headers=headers).json()["revision"] == second["revision"]


@pytest.mark.asyncio
async def test_bus_two_workers_and_reconnect(monkeypatch):
    device_id, _, key, headers, _ = setup_display()
    first = events._begin(headers["Authorization"].split()[1], "worker-two")
    org = first[0]
    socket = AsyncMock()
    # Publisher-Worker ohne lokale Verbindung, Subscriber-Worker mit Verbindung.
    worker_one = {}
    worker_two = {(org, device_id, "worker-two"): socket}
    async def redis_publish(channel, payload):
        assert channel == "ec:isa"
        monkeypatch.setattr(events, "_connections", worker_two)
        await ws_bus._handlers[channel](payload)
        monkeypatch.setattr(events, "_connections", worker_one)
        return True
    monkeypatch.setattr(events, "_connections", worker_one)
    monkeypatch.setattr(ws_bus, "enabled", lambda: True)
    monkeypatch.setattr(ws_bus, "publish", redis_publish)
    await events.publish_org_changed(org, "incident_started")
    assert socket.send_json.call_count == 1
    assert socket.send_json.call_args.args[0] == {"type": "invalidate", "reason": "incident_started",
                                               "revision": 0, "display_key": key}
    monkeypatch.setattr(events, "_connections", worker_two)
    assert events.reconnect in ws_bus._reconnect_callbacks
    await events.reconnect()
    assert socket.send_json.call_args.args[0]["reason"] == "reconnect"
    events._end(org, device_id, "worker-two")


@pytest.mark.asyncio
async def test_hook_error_and_slow_bus_do_not_block_alarm(monkeypatch):
    from app.services.broadcast import broadcast_org
    started = asyncio.Event()
    release = asyncio.Event()
    async def slow(*args):
        started.set()
        await release.wait()
        raise RuntimeError("bus unavailable")
    monkeypatch.setattr(events, "_channel_changed", slow)
    await asyncio.wait_for(broadcast_org(1, {"type": "incident_created", "patient": "never forwarded"}), .5)
    await asyncio.wait_for(started.wait(), .5)
    release.set()
    await asyncio.gather(*list(events._tasks))
    def broken(*args):
        raise RuntimeError("hook failed")
    monkeypatch.setattr(events, "broadcast_hook", broken)
    await broadcast_org(1, {"type": "incident_updated"})


def test_retention_org_batches_and_boundary():
    from app.services.infoscreen_agent_retention import purge_old_device_events
    from app.models.master import FireDept
    from uuid import uuid4

    device_id, _, _, _, _ = setup_display()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(name="Retention", slug=uuid4().hex)
        db.add(org)
        db.flush()
        other = InfoscreenDevice(org_id=org.id, name="Retention")
        db.add(other)
        db.flush()
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=91)
        recent = old + timedelta(days=2)
        for org_id, did in ((1, device_id), (org.id, other.id)):
            db.add_all([InfoscreenDeviceEvent(org_id=org_id, device_id=did, typ="connect", ts=old) for _ in range(3)])
            db.add(InfoscreenDeviceEvent(org_id=org_id, device_id=did, typ="connect", ts=recent))
        db.commit()
        other_id = other.id
    assert purge_old_device_events(chunk_size=2) >= 6
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for did in (device_id, other_id):
            rows = db.query(InfoscreenDeviceEvent).filter(InfoscreenDeviceEvent.device_id == did).all()
            assert len(rows) == 1 and rows[0].ts == recent


def test_0357_upgrade_downgrade_preserves_data(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0357_infoscreen_heartbeat.py"
    spec = importlib.util.spec_from_file_location("migration_0357", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0357" and migration.down_revision == "0356"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'heartbeat.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE infoscreen_device (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE infoscreen_display (id INTEGER PRIMARY KEY, revision BIGINT)")
        connection.exec_driver_sql("INSERT INTO infoscreen_display VALUES (1, 7)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT reported_status FROM infoscreen_display").scalar() is None
            migration.downgrade()
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT revision FROM infoscreen_display").scalar() == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reason", [
    ("incident_created", "incident_started"), ("incident_updated", "incident_changed"),
    ("incident_closed", "incident_finished"), ("gsl_changed", "gsl_changed"),
    ("lage_updated", "gsl_changed"), ("lage_closed", "gsl_finished"),
    ("planspiel_changed", "gsl_changed"), ("einsatz_live", "incident_changed"),
    ("alarm_type_changed", "incident_changed"), ("address_updated", "incident_changed"),
])
async def test_actual_broadcast_hooks_scoped_ids_only(monkeypatch, kind, reason):
    from app.models.incident import Incident
    from app.models.major_incident import MajorIncident
    from app.services.broadcast import LAGE_WS_OFFSET, ORG_WS_OFFSET, manager

    device_id, display_id, key, headers, _ = setup_display()
    events._begin(headers["Authorization"].split()[1], "hook-connection")
    socket = AsyncMock()
    monkeypatch.setattr(events, "_connections", {(1, device_id, "hook-connection"): socket})
    monkeypatch.setattr(ws_bus, "enabled", lambda: False)
    if kind in ("lage_updated", "lage_closed"):
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            lage = MajorIncident(org_id=1, name="Private Lage", status="active")
            db.add(lage)
            db.commit()
            channel = LAGE_WS_OFFSET + lage.id
    elif kind == "incident_closed":
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            incident = Incident(primary_org_id=1, status="closed")
            db.add(incident)
            db.commit()
            channel = incident.id
    else:
        channel = ORG_WS_OFFSET + 1
    await manager.broadcast(channel, {"type": kind, "description": "private Einsatzdaten"})
    await asyncio.gather(*list(events._tasks))
    assert socket.send_json.call_args.args[0] == {
        "type": "invalidate", "reason": reason, "display_key": key, "revision": 0}
    events._end(1, device_id, "hook-connection")


@pytest.mark.asyncio
async def test_config_changed_targets_and_failing_socket(monkeypatch):
    device_id, display_id, key, headers, _ = setup_display()
    events._begin(headers["Authorization"].split()[1], "target")
    socket = AsyncMock()
    dead = AsyncMock()
    dead.send_json.side_effect = RuntimeError("socket failed")
    monkeypatch.setattr(events, "_connections", {(1, device_id, "dead"): dead, (1, device_id, "target"): socket})
    monkeypatch.setattr(events, "_valid", lambda *args: True)
    monkeypatch.setattr(ws_bus, "enabled", lambda: False)
    await service.publish_config_changed(1, [])
    socket.send_json.assert_not_called()
    await service.publish_config_changed(1, [display_id])
    assert socket.send_json.call_args.args[0]["reason"] == "config_changed"
    assert socket.send_json.call_count == 1
    events._end(1, device_id, "target")


def test_presence_expiry_old_connection_and_token():
    device_id, _, _, headers, _ = setup_display()
    org_id, _, token_hash = events._begin(headers["Authorization"].split()[1], "first")
    assert events._valid(org_id, device_id, "first", token_hash, touch=True)
    assert not events._valid(org_id, device_id, "first", hash_api_key("wrong"), touch=True)
    events._begin(headers["Authorization"].split()[1], "second")
    events._end(org_id, device_id, "first")
    assert events._valid(org_id, device_id, "second", token_hash)
    assert not events._valid(org_id, device_id, "first", token_hash)
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        db.get(InfoscreenDevice, device_id).agent_presence_expires_at = events.now() - timedelta(seconds=1)
        db.commit()
    assert not events._valid(org_id, device_id, "second", token_hash, touch=True)
    events._end(org_id, device_id, "second")


def test_ws_missing_pong_expires(client, monkeypatch):
    _, _, _, headers, _ = setup_display()
    monkeypatch.setattr(events, "PING_S", .05)
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        _drain_connect(socket)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4408


def test_revoke_rollback_keeps_connection(client):
    device_id, _, _, headers, _ = setup_display()
    async def rollback():
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            service.sperren(db.get(InfoscreenDevice, device_id))
            db.flush()
            db.rollback()
        await events.publish_org_changed(1, "config_changed")
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        _drain_connect(socket)
        client.portal.call(rollback)
        assert socket.receive_json()["reason"] == "config_changed"
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        assert db.query(InfoscreenDeviceEvent).filter_by(device_id=device_id, typ="token_revoked").count() == 0


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "mariadb"])
def test_0357_ddl_portable(dialect):
    from io import StringIO
    from tests.test_migration_0332_objekt_dokument_georef import migration

    output = StringIO()
    with Operations.context(MigrationContext.configure(
        dialect_name=dialect, opts={"as_sql": True, "output_buffer": output},
    )):
        migration("0357", "infoscreen_heartbeat").upgrade()
        migration("0357", "infoscreen_heartbeat").downgrade()
    sql = output.getvalue()
    assert "reported_status JSON" in sql
    assert "reported_config_version" in sql and "last_revision" in sql
    assert "FOREIGN KEY" not in sql


def test_heartbeat_limit_is_per_device_not_nat_ip(client):
    from app.core.rate_limit import limiter

    if limiter is None:
        pytest.skip("Limiter deaktiviert")
    _, _, key_a, headers_a, _ = setup_display()
    _, _, key_b, headers_b, _ = setup_display()
    for _ in range(6):
        assert client.post(BASE + "/heartbeat", json=body(key_a), headers=headers_a).status_code == 200
    assert client.post(BASE + "/heartbeat", json=body(key_a), headers=headers_a).status_code == 429
    assert client.post(BASE + "/heartbeat", json=body(key_b), headers=headers_b).status_code == 200


def test_hook_from_temporary_loop_keeps_wake_task(client):
    from app.services.broadcast import broadcast_org

    _, _, key, headers, _ = setup_display()
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        _drain_connect(socket)
        asyncio.run(broadcast_org(1, {"type": "incident_updated"}))
        assert socket.receive_json() == {"type": "invalidate", "revision": 0,
                                         "reason": "incident_changed", "display_key": key}


@pytest.mark.asyncio
async def test_ws_pong_then_disconnect_cleans_presence_promptly(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from starlette.websockets import WebSocketState

    monkeypatch.setattr(events, "PING_S", 60)
    monkeypatch.setattr(events, "_begin", lambda *args: (1, 42, "hash"))
    monkeypatch.setattr(events, "_valid", lambda *args, **kwargs: True)
    monkeypatch.setattr(events, "_targets", lambda *args: [])
    monkeypatch.setattr(events, "_publish", AsyncMock())
    end = Mock()
    monkeypatch.setattr(events, "_end", end)
    socket = AsyncMock()
    socket.headers = {"authorization": "Bearer device"}
    socket.application_state = WebSocketState.CONNECTED
    socket.receive_text.side_effect = ['{"type":"pong"}', WebSocketDisconnect()]
    await asyncio.wait_for(events.websocket_session(socket), timeout=1)
    end.assert_called_once()
    assert not any(key[1] == 42 for key in events._connections)
