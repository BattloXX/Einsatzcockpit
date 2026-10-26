"""Redis-WS-Bus (app.services.ws_bus): Fallback ohne Redis, lokale Zustellung über
den Bus-Handler und worker-übergreifender Print-Dispatch (Future-Auflösung via Bus).

Kein echtes Redis nötig: ``ws_bus.publish`` wird auf die registrierten Handler
umgeleitet – so wird die Redis-Fan-out an alle Worker (inkl. Publisher) simuliert.
"""
from __future__ import annotations

import asyncio

from app.services import ws_bus


class _CapWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def _route_publish_to_handlers(monkeypatch) -> None:
    """Simuliert Redis: publish() ruft synchron den lokalen Kanal-Handler auf."""
    async def fake_publish(channel: str, message: dict) -> bool:
        handler = ws_bus._handlers.get(channel)
        if handler is not None:
            await handler(message)
        return True
    monkeypatch.setattr(ws_bus, "publish", fake_publish)


# ── Fallback ohne Redis ─────────────────────────────────────────────────────────

def test_enabled_false_ohne_redis_url():
    assert ws_bus.enabled() is False


async def test_broadcast_lokal_ohne_bus():
    from app.services.broadcast import manager
    sock = _CapWS()
    manager._connections[912345].add(sock)
    try:
        await manager.broadcast(912345, {"type": "ping"})
        assert sock.sent and "ping" in sock.sent[0]
    finally:
        manager._connections.pop(912345, None)


# ── Bus-Handler (CH_WS): lokale Zustellung ──────────────────────────────────────

async def test_bus_ws_handler_liefert_lokal():
    from app.services import broadcast
    sock = _CapWS()
    broadcast.manager._connections[922222].add(sock)
    try:
        await broadcast._bus_deliver({"key": 922222, "event": {"type": "hallo"}})
        assert sock.sent and "hallo" in sock.sent[0]
    finally:
        broadcast.manager._connections.pop(922222, None)


async def test_broadcast_mit_bus_publiziert_und_liefert(monkeypatch):
    from app.services import broadcast
    monkeypatch.setattr(ws_bus, "enabled", lambda: True)
    _route_publish_to_handlers(monkeypatch)
    sock = _CapWS()
    broadcast.manager._connections[933333].add(sock)
    try:
        await broadcast.manager.broadcast(933333, {"type": "via_bus"})
        assert sock.sent and "via_bus" in sock.sent[0]
    finally:
        broadcast.manager._connections.pop(933333, None)


# ── Bus-Handler (CH_GW): Gateway-Ebene ──────────────────────────────────────────

async def test_unaddressed_legacy_status_cannot_resolve_another_workers_future():
    import app.routers.ws as ws
    future = asyncio.get_running_loop().create_future()
    ws._job_pending["999991"] = future
    try:
        await ws._bus_gateway_deliver({"kind": "job_status", "job_id": 999991, "payload": {"status": "done"}})
        assert not future.done()
    finally:
        ws._job_pending.pop("999991", None)


async def test_bus_dispatch_offline_gateway_remains_queued(monkeypatch):
    import app.routers.ws as ws
    from app.services import gateway_routing as routing
    from tests.gateway_helpers import bind, cleanup
    from tests.test_gateway_dispatch import make_job

    monkeypatch.setattr(ws.ws_bus, "enabled", lambda: True)
    connection, socket = bind("linux")
    job_id = make_job(connection)
    routing.end(connection)
    try:
        result = await ws.dispatch_print_job(connection.org_id, job_id, {"job_id": job_id}, timeout=0)
        assert result["status"] == "queued"
        assert not socket.sent
    finally:
        cleanup(connection)


async def test_addressed_dispatch_and_publisher_echo_deliver_once(monkeypatch):
    import app.routers.ws as ws
    from app.services import gateway_routing as routing
    from tests.gateway_helpers import CaptureSocket, bind, cleanup
    from tests.test_gateway_dispatch import make_job
    connection, socket = bind("linux", socket=CaptureSocket(reply=True))
    job_id = make_job(connection)
    _route_publish_to_handlers(monkeypatch)
    try:
        result = await ws.dispatch_print_job(1, job_id, {"job_id": job_id}, timeout=2)
        assert result["status"] == "done" and len(socket.sent) == 1
        assert routing._sockets
    finally:
        cleanup(connection)
