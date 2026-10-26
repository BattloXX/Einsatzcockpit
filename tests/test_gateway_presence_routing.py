"""Connection-Fencing, Mandanten und echtes Prozess-A/B-Routing ohne geteilte Registry."""

import asyncio
import json
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway
from app.models.user import SmsGatewayToken
from app.services import gateway_presence as presence
from app.services import gateway_routing as routing
from tests.gateway_helpers import CaptureSocket, bind, cleanup
from tests.test_print_agent_ws import _anlegen, _auth, _job, _recv


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_presence_owner_ttl_reconnect_and_tenant_fencing(kind):
    connection, _ = bind(kind)
    model = SmsGatewayToken if kind == "sms" else Gateway
    try:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = db.get(model, connection.gateway_id)
            token = row.token_hash if kind == "sms" else row.device_token_hash
            now = presence.utc()
            assert connection.worker_id == presence.worker_id()
            assert not presence.begin(db, kind, 999999, row.id, token, uuid4().hex)
            assert not presence.end(db, kind, 999999, row.id, connection.connection_id)
            assert presence.touch(db, kind, 1, row.id, token, connection.connection_id, now)
            newer = uuid4().hex
            assert presence.begin(db, kind, 1, row.id, token, newer, "worker-b", now)
            assert not presence.end(db, kind, 1, row.id, connection.connection_id)
            assert not presence.touch(db, kind, 1, row.id, token, connection.connection_id, now)
            current = presence.connections(db, kind, 1, row.id, now)[0]
            assert current.connection_id == newer and current.worker_id == "worker-b"
            assert not presence.connections(db, kind, 1, row.id, now + timedelta(seconds=91))
            assert not presence.touch(db, kind, 1, row.id, token, newer, now + timedelta(seconds=91))
    finally:
        cleanup(connection)


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
@pytest.mark.parametrize("rotation", [False, True])
async def test_old_token_cannot_receive_or_update_after_revoke_or_rotation(kind, rotation):
    connection, socket = bind(kind)
    try:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = db.get(SmsGatewayToken if kind == "sms" else Gateway, connection.gateway_id)
            if kind == "sms":
                if rotation:
                    row.token_hash = uuid4().hex
                else:
                    row.revoked_at = presence.utc()
            else:
                row.device_token_hash = uuid4().hex if rotation else None
            db.commit()
        assert routing.state(connection, socket.token_hash) == "authentication_failed"
        assert not routing.targets(kind, connection.org_id, connection.gateway_id)
        item = await routing.send(connection, {"type": "must_not_arrive"})
        assert routing._result(item, connection.org_id)[0] == "expired"
        assert not socket.sent
    finally:
        cleanup(connection)


async def test_target_connection_org_and_publisher_echo_are_fenced(monkeypatch):
    a, socket_a = bind("linux", 1)
    b, socket_b = bind("linux", 2)
    try:

        async def echo(channel, message):
            await routing.deliver(message["message_id"])
            await routing.deliver(message["message_id"])

        monkeypatch.setattr(routing.ws_bus, "publish", echo)
        queued = routing._enqueue(a, {"type": "config_sync", "payload": {"name": "only-a"}}, 30, None)
        await routing.deliver(queued, address={**routing.envelope(a, queued), "org_id": b.org_id})
        assert not socket_a.sent and not socket_b.sent
        await routing.deliver(queued, address={**routing.envelope(a, queued), "connection_id": "wrong"})
        assert not socket_a.sent
        item = queued
        await routing.deliver(item, address=routing.envelope(a, item))
        await asyncio.gather(routing.deliver(item), routing.deliver(item))
        assert len(socket_a.sent) == 1 and not socket_b.sent
        assert routing._result(item, 2) == ("expired", None)
        newer = routing.begin("linux", 1, a.gateway_id, socket_a.token_hash)
        new_socket = CaptureSocket()
        routing.attach(newer, socket_a.token_hash, new_socket)
        await routing.send(a, {"type": "stale"})
        assert len(socket_a.sent) == 1 and not new_socket.sent
        assert not routing.end(a)
        routing.detach(newer)
    finally:
        cleanup(a)
        cleanup(b)


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_real_socket_on_worker_a_dispatch_on_process_b(client, kind):
    data = _anlegen(998901, kind="linux" if kind == "sms" else kind, routen=False)
    if kind == "sms":
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = SmsGatewayToken(org_id=data["org_id"], token_hash=hash_api_key(data["token"]), label="Worker A")
            db.add(row)
            db.commit()
            gid = row.id
    else:
        gid = data["gateway_id"]
    job_id = _job(data, enqueue=False)
    endpoint = {"sms": "/ws/sms-gateway", "linux": "/ws/gateway", "windows_agent": "/ws/print-agent"}[kind]
    code = """
import asyncio,json,sys
from app.routers import ws
from app.services import gateway_routing as routing
assert not routing._sockets
kind,org,gid,jid=json.loads(sys.stdin.readline())
async def run():
    if kind == 'sms': return await ws.dispatch_sms(org,str(jid),'+436600000','test',timeout=10)
    if kind == 'linux': return await ws.dispatch_print_job(org,jid,{'job_id':jid},timeout=10)
    await ws.push_agent_message(org,[gid],{'type':'job_available','payload':{'job_ids':[jid]}})
    return {'ok':True}
print(json.dumps(asyncio.run(run())),flush=True)
"""
    with client.websocket_connect(endpoint, headers=_auth(data["token"])) as socket:
        _recv(socket, "config" if kind == "sms" else "config_sync")
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            process.stdin.write(json.dumps([kind, data["org_id"], gid, job_id]) + "\n")
            process.stdin.flush()
            message = _recv(socket, {"sms": "sms.send", "linux": "print_job", "windows_agent": "job_available"}[kind])
            if kind == "sms":
                socket.send_json({"type": "sms.result", "id": message["id"], "ok": True})
            elif kind == "linux":
                socket.send_json({"type": "job_status", "payload": {"job_id": job_id, "status": "done"}})
            else:
                assert message["payload"]["job_ids"] == [job_id]
            output, error = process.communicate(timeout=20)
            assert process.returncode == 0, error
            result = json.loads(output)
            assert result.get("ok") or result.get("status") == "done"
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_real_ws_reconnect_old_disconnect_and_revocation(client, kind):
    data = _anlegen(998902, kind="linux" if kind == "sms" else kind, routen=False)
    model = Gateway
    gid = data["gateway_id"]
    if kind == "sms":
        model = SmsGatewayToken
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = model(org_id=data["org_id"], token_hash=hash_api_key(data["token"]), label="Reconnect")
            db.add(row)
            db.commit()
            gid = row.id
    endpoint = {"sms": "/ws/sms-gateway", "linux": "/ws/gateway", "windows_agent": "/ws/print-agent"}[kind]
    first = "config" if kind == "sms" else "config_sync"
    with client.websocket_connect(endpoint, headers=_auth(data["token"])) as old:
        _recv(old, first)
        old_connection = routing.targets(kind, data["org_id"], gid)[0]
        with client.websocket_connect(endpoint, headers=_auth(data["token"])) as new:
            _recv(new, first)
            current = routing.targets(kind, data["org_id"], gid)[0]
            assert current.connection_id != old_connection.connection_id
            assert not routing.end(old_connection)
            assert routing.targets(kind, data["org_id"], gid)[0] == current
            old.close()
            new.send_json({"type": "ping"})
            _recv(new, "pong")
            with SessionLocal() as db:
                set_tenant_context(db, None)
                row = db.get(model, gid)
                if kind == "sms":
                    row.revoked_at = presence.utc()
                else:
                    row.device_token_hash = None
                db.commit()
            with pytest.raises(WebSocketDisconnect):
                new.send_json({"type": "ping"})
                new.receive_json()
    assert not routing.targets(kind, data["org_id"], gid)


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_reconnect_moves_real_socket_to_another_process(client, kind):
    import selectors

    data = _anlegen(998903, kind="linux" if kind == "sms" else kind, routen=False)
    gid = data["gateway_id"]
    if kind == "sms":
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = SmsGatewayToken(org_id=data["org_id"], token_hash=hash_api_key(data["token"]), label="Move")
            db.add(row)
            db.commit()
            gid = row.id
    endpoint = {"sms": "/ws/sms-gateway", "linux": "/ws/gateway", "windows_agent": "/ws/print-agent"}[kind]
    code = """
import json,sys
from fastapi.testclient import TestClient
from app.main import app
from app.services import gateway_routing as routing
kind,org,gid,endpoint,token=json.loads(sys.stdin.readline())
with TestClient(app) as client:
    with client.websocket_connect(endpoint,headers={'Authorization':'Bearer '+token}) as socket:
        socket.receive_json()
        c=routing.targets(kind,org,gid)[0]
        print(json.dumps({'connection_id':c.connection_id,'worker_id':c.worker_id}),flush=True)
        command=json.loads(sys.stdin.readline())
        print(json.dumps(socket.receive_json()),flush=True)
        sys.stdin.readline()
"""
    process = None
    try:
        with client.websocket_connect(endpoint, headers=_auth(data["token"])) as old:
            _recv(old, "config" if kind == "sms" else "config_sync")
            previous = routing.targets(kind, data["org_id"], gid)[0]
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            process.stdin.write(json.dumps([kind, data["org_id"], gid, endpoint, data["token"]]) + "\n")
            process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=20), "Worker B did not connect"
            current_data = json.loads(process.stdout.readline())
            current = routing.targets(kind, data["org_id"], gid)[0]
            assert current.connection_id == current_data["connection_id"]
            assert current.worker_id != previous.worker_id
            assert not routing.end(previous)
            old.close()
        assert routing.targets(kind, data["org_id"], gid)[0].connection_id == current.connection_id
        asyncio.run(routing.send(current, {"type": "worker_b_only"}))
        process.stdin.write('{"receive":true}\n')
        process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "Worker B did not receive its message"
        assert json.loads(process.stdout.readline())["type"] == "worker_b_only"
        process.communicate(input="close\n", timeout=15)
        assert process.returncode == 0
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_public_snapshot_expires_without_sweep_or_disconnect(kind):
    connection, socket = bind(kind)
    try:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            snapshot = presence.describe(db, kind, 1, connection.gateway_id)
            assert snapshot["status"] == "online"
            assert snapshot["org_id"] == 1 and snapshot["gateway_id"] == connection.gateway_id
            assert snapshot["connection_id"] == connection.connection_id
            assert snapshot["worker_id"] == connection.worker_id
            assert snapshot["expires_at"] == connection.expires_at
            assert "token_hash" not in snapshot
            assert presence.describe(db, kind, 99999, connection.gateway_id) is None
            expired = presence.describe(
                db, kind, 1, connection.gateway_id, now=connection.expires_at + timedelta(seconds=1)
            )
            assert expired["status"] == "offline"
    finally:
        cleanup(connection)


async def test_config_commands_and_foreign_tenant_never_fan_out():
    from app.models.gateway import Printer
    from app.routers.ws import push_config_sync, push_gateway_command

    a, sa = bind("linux", 1)
    b, sb = bind("linux", 1)
    foreign, sf = bind("linux", 2)
    try:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            printer = Printer(org_id=1, gateway_id=a.gateway_id, name="Target", uri="ipp://test", aktiv=True)
            db.add(printer)
            db.commit()
            printer_id = printer.id
        await push_config_sync(1, a.gateway_id)
        assert len(sa.sent) == 1 and not sb.sent and not sf.sent
        assert await push_gateway_command(1, {"type": "test_page", "printer_id": printer_id})
        assert len(sa.sent) == 2 and not sb.sent and not sf.sent
        assert not await push_gateway_command(2, {"type": "test_page", "printer_id": printer_id})
        assert not await push_gateway_command(1, {"type": "cancel_job", "payload": {"job_id": -999}})
        assert await push_gateway_command(1, {"type": "discover_printers"})
        assert len(sa.sent) == 3 and len(sb.sent) == 1 and not sf.sent
    finally:
        cleanup(a)
        cleanup(b)
        cleanup(foreign)


def test_status_connection_must_match_job_tenant_and_target():
    from app.routers.ws import _apply_job_status
    from tests.test_gateway_dispatch import make_job

    a, _ = bind("linux", 1)
    other, socket = bind("linux", 2)
    try:
        jid = make_job(a)
        assert (
            _apply_job_status(
                jid, "done", None, org_id=1, gateway_id=a.gateway_id, connection=other, token_hash=socket.token_hash
            )
            is None
        )
    finally:
        cleanup(a)
        cleanup(other)


def test_invalid_linux_status_does_not_complete_transport_result(client):
    data = _anlegen(998904, kind="linux", routen=False)
    jid = _job(data, enqueue=False)
    with client.websocket_connect("/ws/gateway", headers=_auth(data["token"])) as socket:
        _recv(socket, "config_sync")
        connection = routing.targets("linux", data["org_id"], data["gateway_id"])[0]
        item_id = routing._enqueue(connection, {"type": "print_job", "id": str(jid)}, 10, str(jid))
        _recv(socket, "print_job")
        socket.send_json({"type": "job_status", "payload": {"job_id": jid, "status": "bad-status"}})
        socket.send_json({"type": "ping"})
        _recv(socket, "pong")
        assert routing._result(item_id, data["org_id"])[1] is None
        socket.send_json({"type": "job_status", "payload": {"job_id": jid, "status": "done"}})
        socket.send_json({"type": "ping"})
        _recv(socket, "pong")
        assert routing._result(item_id, data["org_id"])[1]["status"] == "done"
