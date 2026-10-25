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
    import fakeredis
    import fakeredis.aioredis as far

    server = fakeredis.FakeServer()

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


async def _wait_until(cond, timeout=2.0):  # noqa: ASYNC109
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


async def test_live_print_dispatch_ueber_redis(fake_redis):
    from app.services import gateway_routing as routing
    from tests.gateway_helpers import CaptureSocket, bind, cleanup

    connection, socket = bind("linux", socket=CaptureSocket(reply=True))
    await ws_bus.start()
    try:
        item_id = routing._enqueue(connection, {"type": "print_job", "id": "5150"}, 5, "5150")
        assert await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, item_id))
        result = await routing.wait_result(item_id, 1, 3)
        assert result["status"] == "done"
        assert await _wait_until(lambda: bool(socket.sent))
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, item_id))
        await asyncio.sleep(0.05)
        assert len(socket.sent) == 1
    finally:
        cleanup(connection)
        await ws_bus.stop()
