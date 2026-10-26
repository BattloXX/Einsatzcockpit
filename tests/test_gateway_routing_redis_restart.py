"""Redis-Transportabbruch mit erneuter Subscription; DB-Routing bleibt verfuegbar."""

import asyncio
import threading

import fakeredis
import pytest

from app.services import gateway_routing as routing
from app.services import ws_bus
from tests.gateway_helpers import bind, cleanup


async def until(condition, timeout=8):  # noqa: ASYNC109
    async with asyncio.timeout(timeout):
        while not condition():  # noqa: ASYNC110 -- Redis-TCP-Zustand hat kein lokales Event.
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
async def test_redis_tcp_restart_resubscribes_and_db_queue_still_delivers(monkeypatch, kind):
    server = fakeredis.TcpFakeServer(("127.0.0.1", 0))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", f"redis://127.0.0.1:{address[1]}/0")
    connection, socket = bind(kind)
    try:
        await ws_bus.start()
        first = routing._enqueue(connection, {"type": "before"}, 20, None)
        assert await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, first))
        await until(lambda: len(socket.sent) == 1)
        # Der TCP-Testserver beendet auch bestehende Redis-Verbindungen.
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        thread.join(timeout=2)
        await until(lambda: not ws_bus.status()["connected"])
        # Nachricht stammt von einem anderen Worker; hier nur Polling, kein lokaler Send.
        queued = routing._enqueue(connection, {"type": "during"}, 20, None)
        await routing.start()
        await until(lambda: len(socket.sent) == 2)
        replacement = fakeredis.TcpFakeServer(address)
        replacement.daemon_threads = True
        server = replacement
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        await until(lambda: ws_bus.status()["connected"])
        last = routing._enqueue(connection, {"type": "after"}, 20, None)
        assert await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, last))
        await until(lambda: len(socket.sent) == 3)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, last))
        await asyncio.sleep(0.1)
        assert [m["type"] for m in socket.sent] == ["before", "during", "after"]
        assert routing._result(queued, 1)[0] == "delivered"
    finally:
        await ws_bus.stop()
        await routing.stop()
        cleanup(connection)
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        thread.join(timeout=2)


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
async def test_real_redis_container_restart(monkeypatch, kind):
    """Opt-in gegen einen ausschliesslich fuer diesen Test angelegten Redis-Container."""
    import os
    import subprocess

    import pytest

    container = os.environ.get("ECP_REDIS_TEST_CONTAINER", "")
    url = os.environ.get("ECP_REDIS_TEST_URL", "")
    if not container or not url:
        pytest.skip("ECP_REDIS_TEST_CONTAINER und ECP_REDIS_TEST_URL nicht gesetzt")
    assert container.startswith("ecp-phase2-test-")
    assert url.startswith("redis://127.0.0.1:")
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", url)
    connection, socket = bind(kind)
    probes = []
    channel = "ec:phase2-test-probe"

    async def probe(message):
        probes.append(message)

    ws_bus.register(channel, probe)
    try:
        await ws_bus.start()
        first = routing._enqueue(connection, {"type": "real_before"}, 30, None)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, first))
        await until(lambda: len(socket.sent) == 1)
        await asyncio.to_thread(
            subprocess.run, ["docker", "restart", "--time", "1", container], check=True, capture_output=True, timeout=20
        )
        async with asyncio.timeout(10):
            while not probes:
                await ws_bus.publish(channel, {"probe": True})
                await asyncio.sleep(0.1)
        last = routing._enqueue(connection, {"type": "real_after"}, 30, None)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, last))
        await until(lambda: len(socket.sent) == 2)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, last))
        await asyncio.sleep(0.1)
        assert [m["type"] for m in socket.sent] == ["real_before", "real_after"]
    finally:
        ws_bus._handlers.pop(channel, None)
        await ws_bus.stop()
        cleanup(connection)
