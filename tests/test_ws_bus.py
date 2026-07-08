"""Redis-WS-Bus (app.services.ws_bus): Fallback ohne Redis, lokale Zustellung über
den Bus-Handler und worker-übergreifender Print-Dispatch (Future-Auflösung via Bus).

Kein echtes Redis nötig: ``ws_bus.publish`` wird auf die registrierten Handler
umgeleitet – so wird die Redis-Fan-out an alle Worker (inkl. Publisher) simuliert.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import ws_bus


class _CapWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def _route_publish_to_handlers(monkeypatch) -> None:
    """Simuliert Redis: publish() ruft synchron den lokalen Kanal-Handler auf."""
    async def fake_publish(channel: str, message: dict) -> None:
        handler = ws_bus._handlers.get(channel)
        if handler is not None:
            await handler(message)
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

async def test_bus_gw_job_status_loest_future_auf():
    import app.routers.ws as ws
    key = "999991"
    fut = asyncio.get_event_loop().create_future()
    ws._job_pending[key] = fut
    try:
        await ws._bus_gateway_deliver({
            "kind": "job_status", "job_id": 999991, "payload": {"status": "done"},
        })
        assert fut.done() and fut.result()["status"] == "done"
    finally:
        ws._job_pending.pop(key, None)


async def test_bus_dispatch_no_gateway_raises(monkeypatch):
    import app.routers.ws as ws
    monkeypatch.setattr(ws.ws_bus, "enabled", lambda: True)
    monkeypatch.setattr(ws, "gateway_online", lambda org_id: False)
    with pytest.raises(RuntimeError, match="Kein Gateway"):
        await ws.dispatch_print_job(773001, 1, {"job_id": 1})


async def test_bus_dispatch_cross_worker_done(monkeypatch):
    """Worker-übergreifend: print_job → lokales Gateway → job_status via Bus → Future."""
    import app.routers.ws as ws
    org_id, job_id = 773002, 91
    monkeypatch.setattr(ws.ws_bus, "enabled", lambda: True)
    monkeypatch.setattr(ws, "gateway_online", lambda o: True)
    _route_publish_to_handlers(monkeypatch)

    class _GwWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, payload: str) -> None:
            self.sent.append(payload)
            # Wie der echte ws-Handler nach der Gateway-Antwort: job_status auf den Bus.
            await ws.ws_bus.publish(ws.ws_bus.CH_GW, {
                "kind": "job_status", "org_id": org_id, "job_id": job_id,
                "payload": {"job_id": job_id, "status": "done"},
            })

    gw = _GwWS()
    ws._print_gateways[org_id] = [gw]
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=2.0)
        assert result["status"] == "done"
        assert gw.sent, "Gateway hätte den print_job erhalten müssen"
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)
