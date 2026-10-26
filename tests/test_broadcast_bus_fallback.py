"""Browser-Broadcasts: lokaler Fallback bei Bus-Ausfall, kein Doppel durch Echo."""
import json
from unittest.mock import AsyncMock

import pytest

from app.services import broadcast, ws_bus


class _CapWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


@pytest.fixture(params=["broadcast", "broadcast_all", "broadcast_lage", "broadcast_einheit", "broadcast_org"])
def channel(request, monkeypatch):
    manager = broadcast.ConnectionManager()
    monkeypatch.setattr(broadcast, "manager", manager)
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://fake:6379/0")
    keys = {
        "broadcast": 42,
        "broadcast_all": broadcast._ALL_KEY,
        "broadcast_lage": broadcast.LAGE_WS_OFFSET + 42,
        "broadcast_einheit": broadcast.EINHEIT_WS_OFFSET + 42,
        "broadcast_org": broadcast.ORG_WS_OFFSET + 42,
    }
    key = keys[request.param]
    sock = _CapWS()
    other = _CapWS()
    manager._connections[42 if key == broadcast._ALL_KEY else key].add(sock)
    manager._connections[43].add(other)

    async def send(event):
        if request.param == "broadcast_all":
            await manager.broadcast_all(event)
        elif request.param == "broadcast":
            await manager.broadcast(42, event)
        else:
            await getattr(broadcast, request.param)(42, event)

    return send, sock, other, key


async def test_publish_false_delivers_locally_once(channel, monkeypatch):
    send, sock, other, key = channel
    publish = AsyncMock(return_value=False)
    monkeypatch.setattr(ws_bus, "publish", publish)
    event = {"type": "fallback", "text": "Grüße"}

    await send(event)

    publish.assert_awaited_once_with(ws_bus.CH_WS, {"key": key, "event": event})
    assert [json.loads(payload) for payload in sock.sent] == [event]
    assert [json.loads(payload) for payload in other.sent] == ([event] if key == broadcast._ALL_KEY else [])


async def test_publish_true_delivers_only_through_bus_echo(channel, monkeypatch):
    send, sock, other, key = channel
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(ws_bus, "publish", publish)
    event = {"type": "echo"}

    await send(event)

    publish.assert_awaited_once_with(ws_bus.CH_WS, {"key": key, "event": event})
    assert sock.sent == []
    assert other.sent == []

    await broadcast._bus_deliver(publish.call_args.args[1])

    assert [json.loads(payload) for payload in sock.sent] == [event]
    assert [json.loads(payload) for payload in other.sent] == ([event] if key == broadcast._ALL_KEY else [])


async def test_without_redis_delivers_locally_once(channel, monkeypatch):
    send, sock, other, key = channel
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "")
    publish = AsyncMock()
    monkeypatch.setattr(ws_bus, "publish", publish)
    event = {"type": "local"}

    await send(event)

    publish.assert_not_awaited()
    assert [json.loads(payload) for payload in sock.sent] == [event]
    assert [json.loads(payload) for payload in other.sent] == ([event] if key == broadcast._ALL_KEY else [])


async def test_disconnected_redis_delivers_locally_once(channel, monkeypatch):
    send, sock, other, key = channel
    monkeypatch.setattr(ws_bus, "_redis", None)
    monkeypatch.setattr(ws_bus, "_diagnostics", {"publish_failures": 0})
    monkeypatch.setattr(ws_bus, "_last_warning", {})
    event = {"type": "disconnected"}

    await send(event)

    assert ws_bus._diagnostics["publish_failures"] == 1
    assert [json.loads(payload) for payload in sock.sent] == [event]
    assert [json.loads(payload) for payload in other.sent] == ([event] if key == broadcast._ALL_KEY else [])
