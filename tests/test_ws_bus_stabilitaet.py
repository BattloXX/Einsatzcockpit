"""TCP-Fanout und gezielt verzoegerte PONGs fuer den lokalen Redis-Hinweisbus."""

import asyncio
import importlib.util
import json
import threading
from unittest.mock import AsyncMock

import fakeredis
import pytest
import pytest_asyncio

from app.services import ws_bus
from tests.test_gateway_routing_redis_restart import until


def neuer_bus(monkeypatch):
    # Eigener Modulnamensraum entspricht dem prozesslokalen Zustand eines Workers.
    spec = importlib.util.spec_from_file_location("test_bus", ws_bus.__file__)
    bus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bus)
    monkeypatch.setattr(bus, "HEALTH_INTERVAL_S", .05)
    monkeypatch.setattr(bus, "HEALTH_TIMEOUT_S", .2)
    monkeypatch.setattr(bus, "READ_TIMEOUT_S", .01)
    monkeypatch.setattr(bus, "BACKOFF_MIN_S", .01)
    monkeypatch.setattr(bus, "BACKOFF_MAX_S", .05)
    return bus


@pytest_asyncio.fixture
async def tcp(monkeypatch):
    server = fakeredis.TcpFakeServer(("127.0.0.1", 0))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", f"redis://127.0.0.1:{server.server_address[1]}/0")
    bus = neuer_bus(monkeypatch)
    try:
        yield bus, server
    finally:
        await bus.stop()
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        thread.join(timeout=2)


async def test_stille_ueber_mehrere_heartbeats(tcp):
    bus, _ = tcp
    await bus.start()
    await asyncio.sleep(.35)
    assert bus.status()["last_pong_at"]
    assert bus.status()["subscriber_failures"] == 0
    assert bus.status()["reconnects"] == 1
    assert bus.status()["connected"]


async def test_verbindungsabbruch_resubscribes_ohne_publisher_abbruch(tcp):
    bus, _ = tcp
    erhalten = []
    bus.register(bus.CH_WS, AsyncMock(side_effect=erhalten.append))
    await bus.start()
    publisher = bus._redis
    await bus._subscriber.connection_pool.disconnect()
    await until(lambda: bus.status()["reconnects"] >= 2)
    assert bus._redis is publisher and bus.status()["publisher_ok"]
    assert await bus.publish(bus.CH_WS, {"nach": "abbruch"})
    await until(lambda: len(erhalten) == 1)


async def test_neustart_resubscribes(tcp):
    bus, server = tcp
    erhalten = []
    bus.register(bus.CH_WS, AsyncMock(side_effect=erhalten.append))
    await bus.start()
    # Gleicher TCP-Endpunkt; der Ersatzserver hat einen frischen Redis-Zustand.
    adresse = server.server_address
    await asyncio.to_thread(server.shutdown)
    await asyncio.to_thread(server.server_close)
    await until(lambda: not bus.status()["subscriber_connected"])
    ersatz = fakeredis.TcpFakeServer(adresse)
    ersatz.daemon_threads = True
    thread = threading.Thread(target=ersatz.serve_forever, daemon=True)
    thread.start()
    try:
        await until(lambda: bus.status()["connected"])
        assert bus.status()["reconnects"] >= 2
        async with asyncio.timeout(2):
            while not await bus.publish(bus.CH_WS, {"nach": "neustart"}):
                await asyncio.sleep(.02)
        await until(lambda: len(erhalten) == 1)
    finally:
        await bus.stop()
        await asyncio.to_thread(ersatz.shutdown)
        await asyncio.to_thread(ersatz.server_close)
        thread.join(timeout=2)


async def test_langsamer_handler_blockiert_keine_pongs(tcp):
    bus, _ = tcp
    begonnen = asyncio.Event()
    freigabe = asyncio.Event()

    async def langsam(message):
        begonnen.set()
        await freigabe.wait()

    bus.register(bus.CH_WS, langsam)
    await bus.start()
    await bus.publish(bus.CH_WS, {})
    await asyncio.wait_for(begonnen.wait(), 2)
    await asyncio.sleep(.4)
    assert bus.status()["subscriber_failures"] == 0
    assert bus.status()["last_pong_at"]
    consumer = bus._consumer_task
    await bus.stop()
    assert consumer.done()


async def test_publisherfehler_isoliert_und_erholt_sich(tcp, monkeypatch):
    bus, _ = tcp
    await bus.start()
    subscriber = bus._subscriber
    with monkeypatch.context() as patch:
        patch.setattr(bus._redis, "publish", AsyncMock(side_effect=ConnectionError("geheim")))
        assert not await bus.publish(bus.CH_WS, {})
        assert not bus.status()["publisher_ok"]
        assert bus.status()["subscriber_connected"]
    await until(lambda: bus.status()["publisher_ok"])
    assert bus._subscriber is subscriber
    assert bus.status()["subscriber_failures"] == 0


async def test_zwei_subscriber_und_isolierter_ausfall(tcp, monkeypatch):
    bus, _ = tcp
    anderer = neuer_bus(monkeypatch)
    empfang = [[], []]
    for instanz, liste in zip((bus, anderer), empfang, strict=True):
        instanz.register(bus.CH_WS, AsyncMock(side_effect=liste.append))
    try:
        await bus.start()
        await anderer.start()
        await bus.publish(bus.CH_WS, {"nummer": 1})
        await until(lambda: all(len(liste) == 1 for liste in empfang))
        await bus._subscriber.connection_pool.disconnect()
        await until(lambda: bus.status()["reconnects"] >= 2)
        assert anderer.status()["subscriber_failures"] == 0
        await anderer.publish(bus.CH_WS, {"nummer": 2})
        await until(lambda: all(len(liste) == 2 for liste in empfang))
    finally:
        await anderer.stop()


async def test_diagnose_ohne_zugangsdaten(tcp, monkeypatch, caplog):
    bus, _ = tcp
    await bus.start()
    await until(lambda: bus.status()["last_pong_at"] is not None)
    monkeypatch.setattr(bus.settings, "REDIS_URL", "redis://secretuser:secretpass@invalid/0")
    monkeypatch.setattr(bus, "_reader", AsyncMock(side_effect=ConnectionError(bus.settings.REDIS_URL)))
    await bus._subscriber.connection_pool.disconnect()
    await until(lambda: bus.status()["subscriber_failures"] >= 1)
    daten = bus.status()
    assert daten["last_exception_class"] == "ConnectionError"
    assert daten["connection_duration_s"] >= 0
    assert daten["last_pong_at"] and daten["worker_id"] and daten["reconnects"]
    assert "fehlerklasse=ConnectionError" in caplog.text
    for geheim in ("secretuser", "secretpass", "redis://"):
        assert geheim not in json.dumps(daten) + caplog.text


async def test_queue_verwirft_aeltesten_hinweis(monkeypatch):
    bus = neuer_bus(monkeypatch)
    erhalten = []
    bus.register(bus.CH_WS, AsyncMock(side_effect=erhalten.append))
    bus._queue = asyncio.Queue(maxsize=1000)
    for nummer in range(1002):
        await bus._dispatch({"type": "message", "channel": bus.CH_WS, "data": json.dumps({"n": nummer})})
    assert bus.status()["dropped"] == 2
    assert bus._queue.qsize() == 1000
    consumer = asyncio.create_task(bus._consume(bus._queue))
    try:
        await asyncio.wait_for(bus._queue.join(), 2)
        assert [m["n"] for m in erhalten] == list(range(2, 1002))
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer


@pytest.mark.parametrize("verzoegerung,fehler", [(2, False), (5, True)])
async def test_verzoegertes_pong_virtuelle_uhr(monkeypatch, verzoegerung, fehler):
    bus = neuer_bus(monkeypatch)
    bus.HEALTH_INTERVAL_S = 1
    bus.HEALTH_TIMEOUT_S = 4
    zeit = 0
    gesendet = None
    monkeypatch.setattr(bus, "monotonic", lambda: zeit)

    class Pubsub:
        async def get_message(self, **kwargs):
            nonlocal zeit
            await asyncio.sleep(0)
            zeit += 1
            if gesendet is not None and zeit - gesendet >= verzoegerung:
                return {"type": "pong"}
            return None

        async def ping(self):
            nonlocal gesendet
            gesendet = zeit

    if fehler:
        urspruenglicher_leser = bus._reader
        neu = object()
        verbinden = AsyncMock(return_value=neu)

        async def lesen(pubsub):
            if pubsub is neu:
                raise asyncio.CancelledError
            await urspruenglicher_leser(pubsub)

        monkeypatch.setattr(bus, "_connect", verbinden)
        monkeypatch.setattr(bus, "_reader", lesen)
        monkeypatch.setattr(bus, "_close", AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(bus._supervise(Pubsub()), 2)
        assert bus.status()["subscriber_failures"] == 1
        assert bus.status()["subscriber_exception_class"] == "TimeoutError"
        verbinden.assert_awaited_once()
    else:
        task = asyncio.create_task(bus._reader(Pubsub()))
        for _ in range(30):
            await asyncio.sleep(0)
        assert bus.status()["last_pong_at"]
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_doppelter_gateway_hinweis_wird_nur_einmal_geclaimt(tcp):
    from app.routers.ws import _bus_gateway_deliver
    from app.services import gateway_routing as routing
    from tests.gateway_helpers import bind, cleanup

    bus, _ = tcp
    connection, socket = bind("linux")

    verarbeitet = 0

    async def zustellen(hinweis):
        nonlocal verarbeitet
        await _bus_gateway_deliver(hinweis)
        verarbeitet += 1

    bus.register(bus.CH_GW, zustellen)
    try:
        await bus.start()
        nachricht = routing._enqueue(connection, {"type": "einmal"}, 20, None)
        hinweis = routing.envelope(connection, nachricht)
        assert await bus.publish(bus.CH_GW, hinweis)
        assert await bus.publish(bus.CH_GW, hinweis)
        await until(lambda: verarbeitet == 2)
        assert len(socket.sent) == 1
        assert routing._result(nachricht, connection.org_id)[0] == "delivered"
    finally:
        cleanup(connection)


async def test_backoff_reset_erst_nach_stabiler_verbindung(monkeypatch):
    bus = neuer_bus(monkeypatch)
    bus.BACKOFF_MIN_S = .1
    bus.BACKOFF_MAX_S = 5
    zeit = 0
    pausen = []
    monkeypatch.setattr(bus, "monotonic", lambda: zeit)
    monkeypatch.setattr(bus.random, "uniform", lambda unten, oben: unten)

    async def verbinden():
        bus._connected_since = zeit
        return object()

    async def lesen(pubsub):
        nonlocal zeit
        # Die vierte Verbindung ist lange genug stabil, um den Backoff zu resetten.
        zeit += 31 if len(pausen) == 3 else 1
        raise ConnectionError("abbruch")

    async def warten(dauer):
        pausen.append(dauer)
        if len(pausen) == 5:
            raise asyncio.CancelledError

    monkeypatch.setattr(bus, "_connect", verbinden)
    monkeypatch.setattr(bus, "_reader", lesen)
    monkeypatch.setattr(bus, "_close", AsyncMock())
    # Nur diese isolierte Simulation verwendet sleep; keine konkurrierenden Tasks.
    monkeypatch.setattr(bus.asyncio, "sleep", warten)
    with pytest.raises(asyncio.CancelledError):
        await bus._supervise(None)
    assert pausen == [.1, .2, .4, .1, .2]


async def test_client_optionen_und_sofortiger_stop(tcp):
    bus, _ = tcp
    await bus.start()
    publisher = bus._redis.connection_pool.connection_kwargs
    subscriber = bus._subscriber.connection_pool.connection_kwargs
    assert publisher["socket_timeout"] == 5
    assert publisher["socket_connect_timeout"] == 2
    assert subscriber["socket_timeout"] is None
    assert subscriber["health_check_interval"] == 0
    assert subscriber["protocol"] == 2
    consumer = bus._consumer_task
    await bus.stop()
    assert consumer.done()
    assert bus._subscriber is None and bus._redis is None
    assert bus._queue is None
    assert not bus.status()["publisher_ok"]
    assert bus._pubsub_task is None and bus._publisher_task is None
