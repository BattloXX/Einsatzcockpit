"""Live-Nachweis des Redis-WS-Bus über einen echten Redis-Protokoll-Server (fakeredis).

Anders als test_ws_bus (routet publish direkt auf die Handler) läuft hier der echte
Pfad: redis.asyncio-Client → PUBLISH → Subscriber-Loop (ws_bus._reader) → Handler →
lokale Socket-Zustellung bzw. Future-Auflösung. Das validiert Serialisierung, das
Subscribe/Listen und die Fan-out-Semantik (Publisher erhält die eigene Nachricht).

Ersetzt die auf dieser (Windows-)Maschine nicht mögliche echte 2-Prozess-Variante
(gunicorn -w 2 + redis-server); der prozessübergreifende Fall unterscheidet sich davon
nur dadurch, dass zwei Prozesse denselben Redis abonnieren – exakt das, was fakeredis
über einen gemeinsamen FakeServer nachbildet.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import ws_bus


@pytest.fixture
def fake_redis(monkeypatch):
    import fakeredis.aioredis as far
    server = far.FakeServer()

    def _from_url(url, **kw):  # noqa: ARG001
        return far.FakeRedis(server=server, decode_responses=True)

    # ws_bus.start() macht `import redis.asyncio as aioredis; aioredis.from_url(...)`.
    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://fake:6379/0")
    yield server


class _CapWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


async def test_start_faellt_bei_unerreichbarem_redis_zurueck(monkeypatch):
    """REDIS_URL gesetzt, aber Server unerreichbar → start() darf NICHT crashen;
    Bus bleibt inaktiv (In-Process), App bootet weiter."""
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://127.0.0.1:6390/0")

    async def _boom(url, **kw):  # noqa: ARG001
        raise ConnectionError("kein Redis")

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _RaisingClient())
    await ws_bus.start()  # darf keine Exception werfen
    assert ws_bus._redis is None
    await ws_bus.stop()


class _RaisingClient:
    def pubsub(self):
        raise ConnectionError("kein Redis erreichbar")

    async def aclose(self):
        pass


async def _wait_until(cond, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_live_broadcast_ueber_redis(fake_redis):
    """PUBLISH auf CH_WS erreicht über den echten Subscriber-Loop die lokalen Sockets."""
    from app.services import broadcast
    await ws_bus.start()
    assert ws_bus.enabled()
    sock = _CapWS()
    broadcast.manager._connections[880001].add(sock)
    try:
        await broadcast.manager.broadcast(880001, {"type": "live_ping"})
        assert await _wait_until(lambda: bool(sock.sent)), "Broadcast kam nicht über Redis an"
        assert "live_ping" in sock.sent[0]
    finally:
        broadcast.manager._connections.pop(880001, None)
        await ws_bus.stop()


async def test_live_print_dispatch_ueber_redis(fake_redis, monkeypatch):
    """print_job fließt über Redis zum Gateway; job_status löst das Future auf."""
    import app.routers.ws as ws
    org_id, job_id = 880002, 5150
    await ws_bus.start()
    monkeypatch.setattr(ws, "gateway_online", lambda o: True)  # DB-Heartbeat umgehen

    class _GwWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, payload: str) -> None:
            self.sent.append(payload)
            # Gateway antwortet – wie der echte ws-Handler – mit job_status auf den Bus.
            await ws.ws_bus.publish(ws.ws_bus.CH_GW, {
                "kind": "job_status", "org_id": org_id, "job_id": job_id,
                "payload": {"job_id": job_id, "status": "done"},
            })

    gw = _GwWS()
    ws._print_gateways[org_id] = [gw]
    try:
        result = await ws.dispatch_print_job(org_id, job_id, {"job_id": job_id}, timeout=3.0)
        assert result["status"] == "done"
        assert gw.sent, "Gateway hätte den print_job über Redis erhalten müssen"
    finally:
        ws._print_gateways.pop(org_id, None)
        ws._job_pending.pop(str(job_id), None)
        await ws_bus.stop()
