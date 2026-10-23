"""Presence ueber Workergrenzen, Reconnect-Fencing und Tokenwiderruf."""
import json
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway
from app.routers import ws as sockets
from app.services import print_agent_presence as presence
from tests.test_print_agent_ws import _anlegen, _auth, _recv


def session():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def test_worker_b_reads_worker_a_without_local_socket(client):
    data = _anlegen(998101)
    with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])) as socket:
        _recv(socket, "config_sync")
        code = ("from app.routers.ws import gateway_online_id; "
                f"assert gateway_online_id({data['gateway_id']}, {data['org_id']}); "
                f"assert not gateway_online_id({data['gateway_id']}, {data['org_id'] + 1})")
        subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
        with session() as db:
            gw = db.get(Gateway, data["gateway_id"])
            assert presence.online(gw)
            assert gw.agent_connected_at is not None
    assert not sockets.gateway_online_id(data["gateway_id"], data["org_id"])


def test_old_disconnect_never_overwrites_new_connection_on_other_worker(client, monkeypatch):
    data = _anlegen(998102)
    events = []

    async def capture(org_id, event):
        if event.get("type") == "gateway_status":
            events.append(event)

    monkeypatch.setattr(sockets, "broadcast_org", capture)
    with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])) as old:
        _recv(old, "config_sync")
        with session() as db:
            gateway = db.get(Gateway, data["gateway_id"])
            old_id, token_hash = gateway.agent_connection_id, gateway.device_token_hash
        new_id = uuid4().hex
        with session() as db:
            assert presence.begin(db, data["org_id"], data["gateway_id"], token_hash, new_id)
    with session() as db:
        current = db.get(Gateway, data["gateway_id"])
        assert current.agent_connection_id == new_id
        assert current.status == "online"
        assert presence.online(current)
        assert not presence.end(db, data["org_id"], current.id, old_id)
    assert not any(not event["online"] for event in events)


def test_heartbeat_committed_and_inventory_does_not_extend_presence(client):
    data = _anlegen(998103)
    with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])) as socket:
        _recv(socket, "config_sync")
        socket.send_json({"type": "heartbeat"})
        socket.send_json({"type": "ping"})
        _recv(socket, "pong")
        with session() as db:
            gw = db.get(Gateway, data["gateway_id"])
            assert gw.agent_heartbeat_at is not None
            assert gw.last_seen_at == gw.agent_heartbeat_at
            deadline = gw.agent_presence_expires_at
        socket.send_json({"type": "inventory", "payload": {"queues": [{"queue": "HP"}]}})
        socket.send_json({"type": "ping"})
        _recv(socket, "pong")
        with session() as db:
            assert db.get(Gateway, data["gateway_id"]).agent_presence_expires_at == deadline


def test_worker_crash_expires_and_old_heartbeat_cannot_resurrect_connection(client):
    data = _anlegen(998104)
    start = presence.utc()
    with session() as db:
        gw = db.get(Gateway, data["gateway_id"])
        token, cid = gw.device_token_hash, uuid4().hex
        assert presence.begin(db, gw.org_id, gw.id, token, cid, start)
        assert not presence.online(db.get(Gateway, gw.id), start + timedelta(seconds=91))
        assert not presence.touch(db, gw, cid, token, now=start + timedelta(seconds=91))
        assert (gw.org_id, gw.id) in presence.expire(db, start + timedelta(seconds=91))
        db.expire_all()
        assert db.get(Gateway, gw.id).status == "offline"
        assert presence.begin(db, gw.org_id, gw.id, token, uuid4().hex, start + timedelta(seconds=92))


@pytest.mark.parametrize("change", ["revoke", "rotate"])
def test_revoked_or_rotated_token_closes_existing_socket(client, change):
    data = _anlegen(998105 if change == "revoke" else 998106)
    with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])) as socket:
        _recv(socket, "config_sync")
        with session() as db:
            gw = db.get(Gateway, data["gateway_id"])
            from app.services.gateway_service import revoke_token, rotate_token
            if change == "revoke":
                revoke_token(gw)
            else:
                rotate_token(db, gw)
            db.commit()
            assert not presence.online(gw)
        socket.send_json({"type": "heartbeat"})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])):
            pass


def test_two_agents_and_reconnect_are_independent(client):
    a, b = _anlegen(998107), _anlegen(998107)
    with client.websocket_connect("/ws/print-agent", headers=_auth(b["token"])) as second:
        _recv(second, "config_sync")
        for _ in range(2):
            with client.websocket_connect("/ws/print-agent", headers=_auth(a["token"])) as first:
                _recv(first, "config_sync")
                assert sockets.gateway_online_id(a["gateway_id"], a["org_id"])
                assert sockets.gateway_online_id(b["gateway_id"], b["org_id"])
            assert not sockets.gateway_online_id(a["gateway_id"], a["org_id"])
            assert sockets.gateway_online_id(b["gateway_id"], b["org_id"])


def test_inventory_alone_and_foreign_connection_cannot_claim_online(client):
    data = _anlegen(998108)
    with session() as db:
        gw = db.get(Gateway, data["gateway_id"])
        gw.agent_connection_id = None
        gw.agent_info = {"reported_at": presence.utc().isoformat(), "queues": [{"queue": "HP"}]}
        db.commit()
        assert not presence.online(gw)
        assert not presence.begin(db, gw.org_id + 1, gw.id, gw.device_token_hash, uuid4().hex)
        assert not presence.end(db, gw.org_id + 1, gw.id, uuid4().hex)
    assert not sockets.gateway_online_id(data["gateway_id"], data["org_id"])


def test_http_diagnostic_without_registry_and_no_secret_disclosure(client):
    from tests.test_print_agent_ui import _admin, _agent
    _admin(client, "presence_diagnostic")
    gateway_id = _agent()
    response = client.get(f"/gateway/{gateway_id}?diagnose=1")
    assert response.status_code == 200
    assert "Verbindungsdiagnose" in response.text
    assert "Verbunden" in response.text
    assert "Noch nicht empfangen" in response.text
    assert "agent_connection_id" not in response.text
    assert "device_token" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with session() as db:
        gw = db.get(Gateway, gateway_id)
        gw.agent_presence_expires_at = presence.utc() - timedelta(seconds=1)
        gw.agent_info = {"reported_at": presence.utc().isoformat(), "queues": [{"queue": "HP"}]}
        db.commit()
    assert "Keine aktive WebSocket-Verbindung" in client.get(f"/gateway/{gateway_id}?diagnose=1").text
    assert client.get(f"/gateway/{gateway_id + 99999}?diagnose=1").status_code == 404


def test_real_http_worker_b_while_websocket_is_on_worker_a(client):
    from app.core.security import hash_api_key
    from tests.test_print_agent_ui import _admin, _agent

    _admin(client, "presence_http_process")
    gateway_id = _agent()
    token = "presence-test-" + uuid4().hex
    with session() as db:
        gw = db.get(Gateway, gateway_id)
        gw.device_token_hash = hash_api_key(token)
        db.commit()
    with client.websocket_connect("/ws/print-agent", headers=_auth(token)) as socket:
        _recv(socket, "config_sync")
        # Eigene HTTP-App/Registry in Prozess B; Test-Cookies nur ueber stdin, nie argv.
        worker = """
import json
import sys
from fastapi.testclient import TestClient
from app.main import app
request = json.load(sys.stdin)
with TestClient(app) as http:
    http.cookies.update(request["cookies"])
    response = http.get(request["path"])
    assert response.status_code == 200, response.status_code
    assert "<dd>Online</dd>" in response.text
    assert "Verbunden" in response.text
    assert response.headers["cache-control"] == "no-store"
"""
        subprocess.run([sys.executable, "-c", worker], check=True, timeout=30,
                       input=json.dumps({"cookies": dict(client.cookies),
                                         "path": f"/gateway/{gateway_id}?diagnose=1"}), text=True)
