"""Keepalive, Reconnect-Bereinigung und Diagnose der Gateway-WebSockets."""

import asyncio
import json
import logging
import threading
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app.routers import ws
from tests.test_print_agent_ws import _anlegen, _auth, _recv


class Socket:
    def __init__(self, *messages):
        self.messages = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.sent = []
        self.closed = []
        self.on_send = None

    async def accept(self):
        return None

    async def receive_text(self):
        message = await self.messages.get()
        if isinstance(message, Exception):
            raise message
        return message

    async def send_text(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if self.on_send:
            self.on_send(message)

    async def close(self, code, reason=""):
        self.closed.append((code, reason))


@pytest.fixture
def sms(monkeypatch):
    """DB-Einstiegspunkte muessen ausserhalb der Event-Loop laufen."""
    loop_thread = threading.get_ident()
    calls = []

    def threaded(name, result=None):
        def call(*_args):
            assert threading.get_ident() != loop_thread, name
            calls.append(name)
            return result

        return call

    token = SimpleNamespace(org_id=997301, id=31, token_hash="test-hash")
    monkeypatch.setattr(ws, "_resolve_sms_gateway_token", threaded("resolve", token))
    monkeypatch.setattr(ws, "_touch_sms_gateway_token", threaded("touch"))
    monkeypatch.setattr(ws, "_heartbeat_sms_gateway_token", threaded("heartbeat"))
    monkeypatch.setattr(ws, "_sms_receive_enabled", threaded("config", False))
    monkeypatch.setattr(ws, "record_inbound_sms", threaded("inbound", 42))
    monkeypatch.setattr(ws, "_start_sms_nachversand", lambda _org_id: None)
    from datetime import timedelta

    from app.services import gateway_presence as presence
    connection = presence.Connection("sms", token.org_id, token.id, "test-cid", "test-worker",
                                     presence.utc() + timedelta(seconds=90))
    monkeypatch.setattr(ws.gateway_routing, "begin", threaded("begin", connection))
    monkeypatch.setattr(ws.gateway_routing, "state", threaded("state", "connected"))
    monkeypatch.setattr(ws.gateway_routing, "touch", threaded("heartbeat", True))
    monkeypatch.setattr(ws.gateway_routing, "end", threaded("end", True))
    monkeypatch.setattr(ws.gateway_routing, "attach", lambda *_args: None)
    monkeypatch.setattr(ws.gateway_routing, "detach", lambda *_args: None)

    yield token, calls
    ws._sms_gateways.pop(token.org_id, None)


@pytest.mark.parametrize("close_fails", [False, True])
async def test_sms_reconnect_replaces_all_old_sockets(sms, caplog, close_fails):
    token, _ = sms
    old, zombie, other = Socket(), Socket(), Socket()
    new = Socket(WebSocketDisconnect(1001, "maintenance"))
    ws._sms_gateways[token.org_id] = [(token.id, old), (token.id, zombie), (32, other)]

    if close_fails:

        async def broken_close(code, reason):
            old.closed.append((code, reason))
            raise RuntimeError("already closed")

        old.close = broken_close

    def registered(_message):
        assert ws._sms_gateways[token.org_id] == [(32, other), (token.id, new)]

    new.on_send = registered
    with caplog.at_level(logging.INFO, logger=ws.logger.name):
        await ws.sms_gateway_ws(new)
    assert old.closed == zombie.closed == [(1012, "replaced")]
    assert other.closed == []
    assert ws._sms_gateways[token.org_id] == [(32, other)]
    assert "code=1001, reason=maintenance, duration_s=" in caplog.text
    assert ("Schliessen fehlgeschlagen" in caplog.text) == close_fails
    if close_fails:
        assert "RuntimeError" in caplog.text


async def test_sms_idle_ping_then_dead_close(sms, monkeypatch, caplog):
    token, calls = sms
    monkeypatch.setattr(ws, "SMS_GATEWAY_IDLE_PING_S", 0.005)
    monkeypatch.setattr(ws, "SMS_GATEWAY_DEAD_AFTER_S", 0.025)
    socket = Socket()
    with caplog.at_level(logging.INFO, logger=ws.logger.name):
        await asyncio.wait_for(ws.sms_gateway_ws(socket), timeout=2)
    assert {"type": "ping"} in socket.sent
    assert socket.closed == [(4408, "idle timeout")]
    assert ws._sms_gateways[token.org_id] == []
    assert {"resolve", "touch", "begin", "config", "state", "end"} <= set(calls)
    assert "heartbeat" not in calls
    assert "code=4408" in caplog.text


async def test_sms_every_message_resets_deadline(sms, monkeypatch):
    _, calls = sms
    monkeypatch.setattr(ws, "SMS_GATEWAY_IDLE_PING_S", 0.001)
    monkeypatch.setattr(ws, "SMS_GATEWAY_DEAD_AFTER_S", 0.1)
    clock = [0.0]
    monkeypatch.setattr(ws, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    socket = Socket()
    messages = iter(["invalid JSON", '{"type":"unknown"}', '{"type":"pong"}'])

    def reply(message):
        if message["type"] == "ping":
            clock[0] += 0.09
            raw = next(messages, None)
            if raw is not None:
                socket.messages.put_nowait(raw)

    socket.on_send = reply
    await asyncio.wait_for(ws.sms_gateway_ws(socket), timeout=2)
    assert calls.count("heartbeat") == 3
    assert sum(message["type"] == "ping" for message in socket.sent) == 5
    assert socket.closed == [(4408, "idle timeout")]


async def test_sms_inbound_db_is_offloaded(sms, monkeypatch):
    _, calls = sms
    processed = asyncio.Event()

    async def process(inbox_id):
        assert inbox_id == 42
        processed.set()

    monkeypatch.setattr(ws, "process_inbound_sms", process)
    socket = Socket(
        '{"type":"sms.received","from":"+43660123","text":"Test","id":"in-1"}',
        WebSocketDisconnect(),
    )
    await ws.sms_gateway_ws(socket)
    await asyncio.wait_for(processed.wait(), timeout=2)
    assert "inbound" in calls
    assert {"type": "sms.received.ack", "id": "in-1"} in socket.sent


async def test_sms_send_failure_logs_and_discards(sms, caplog):
    token, _ = sms
    socket = Socket()

    def broken_send(_message):
        raise RuntimeError("broken pipe")

    socket.on_send = broken_send
    with pytest.raises(RuntimeError, match="broken pipe"):
        await ws.sms_gateway_ws(socket)
    assert ws._sms_gateways[token.org_id] == []
    assert "SMS-Gateway Fehler" in caplog.text
    assert "RuntimeError" in caplog.text


def test_print_disconnect_logs_close_code_and_duration(client, caplog):
    data = _anlegen(997302)
    with caplog.at_level(logging.INFO, logger=ws.logger.name):
        with client.websocket_connect("/ws/print-agent", headers=_auth(data["token"])) as socket:
            _recv(socket, "config_sync")
            socket.close(code=1001, reason="maintenance")
    line = next(record.message for record in caplog.records if "agent_disconnected " in record.message)
    assert "code=1001 reason=maintenance duration_s=" in line


async def test_print_db_calls_run_outside_event_loop(monkeypatch):
    from app.services import print_agent_presence

    data = _anlegen(997303)
    loop_thread = threading.get_ident()
    calls = []

    def guard(target, name):
        original = getattr(target, name)

        def checked(*args, **kwargs):
            assert threading.get_ident() != loop_thread, name
            calls.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(target, name, checked)

    for name in ("_resolve_gateway", "_agent_call"):
        guard(ws, name)
    for name in ("begin", "state", "touch"):
        guard(print_agent_presence, name)
    socket = Socket(
        '{"type":"heartbeat"}',
        '{"type":"job_status","payload":{"job_id":-1}}',
        WebSocketDisconnect(1000),
    )
    socket.headers = {"authorization": f"Bearer {data['token']}"}
    socket.query_params = {}
    await ws.print_agent_ws(socket)
    assert {"_resolve_gateway", "_agent_call", "begin", "state", "touch"} <= set(calls)
    assert calls.count("_agent_call") == 3  # Config, Heartbeat und Jobstatus.
    assert ws._print_agents[data["org_id"]] == []


async def test_print_send_failure_logs_and_cleans_presence(monkeypatch, caplog):
    data = _anlegen(997304)
    socket = Socket()
    socket.headers = {"authorization": f"Bearer {data['token']}"}
    socket.query_params = {}

    def broken_send(_message):
        raise RuntimeError("broken pipe")

    socket.on_send = broken_send
    with pytest.raises(RuntimeError, match="broken pipe"):
        await ws.print_agent_ws(socket)
    assert not ws.gateway_online_id(data["gateway_id"], data["org_id"])
    assert ws._print_agents[data["org_id"]] == []
    assert "agent_error" in caplog.text
    assert "exception=RuntimeError" in caplog.text
