"""Lagedokument PR5: Multi-Worker-Sync via Redis (echter fakeredis-Live-Nachweis).

Simuliert zwei Worker durch zwei unabhaengige YRoom-Instanzen fuer dieselbe
Lage-ID im selben Prozess: der lokale Room-Registry-Dict (collab._rooms) wird
zwischen den Schritten umgehaengt, um jeweils "den anderen Worker" zu
repraesentieren. Nutzt denselben fakeredis-Ansatz wie test_ws_bus_live.py
(echter redis.asyncio-Client ueber einen gemeinsamen FakeServer, kein Mock der
Handler direkt) -- das bildet exakt nach, was mehrere echte Worker-Prozesse
ueber denselben Redis sehen wuerden.
"""
from __future__ import annotations

import asyncio

import pytest
from pycrdt import Doc, Text
from pycrdt.websocket.yroom import YRoom

from app.services import lagedokument_collab as collab
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


async def _wait_until(cond, timeout=2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def _start_room(lage_id: int) -> YRoom:
    """Baut einen eigenstaendigen YRoom auf -- wie get_or_create_room()s Verkabelung,
    aber ohne den globalen _rooms-Dict zu belegen (das uebernimmt der jeweilige Test)."""
    room = YRoom(ydoc=Doc())
    room.ydoc.observe(lambda event, _lid=lage_id: collab._on_ydoc_event(_lid, event))
    room.on_message = lambda message, _lid=lage_id: collab._on_room_message(_lid, message)
    task = asyncio.create_task(room.start())
    await room.started.wait()
    room._test_task = task  # type: ignore[attr-defined]
    return room


async def _stop_room(room: YRoom) -> None:
    try:
        await room.stop()
    except RuntimeError:
        pass
    room._test_task.cancel()  # type: ignore[attr-defined]


async def test_content_update_erreicht_ueber_redis_einen_anderen_worker(fake_redis):
    lage_id = 9990001
    await ws_bus.start()
    room_a = await _start_room(lage_id)
    room_b = await _start_room(lage_id)
    try:
        # room_a repraesentiert Worker A: als lokaler Room registrieren, damit
        # sein ydoc-Observer (collab._on_ydoc_event) die Aenderung auf den Bus publiziert.
        collab._rooms[lage_id] = room_a
        room_a.ydoc.get("content", type=Text).insert(0, "Hallo von A")

        # Worker B haette denselben Lage-Slot lokal, aber sein EIGENES YRoom/Y.Doc --
        # umhaengen simuliert, dass jetzt B "der lokale Room" fuer eingehende Bus-Frames ist.
        collab._rooms[lage_id] = room_b
        ok = await _wait_until(lambda: str(room_b.ydoc.get("content", type=Text)) == "Hallo von A")
        assert ok, "Content-Update kam nicht ueber Redis beim anderen Worker an"
    finally:
        collab._rooms.pop(lage_id, None)
        await _stop_room(room_a)
        await _stop_room(room_b)
        await ws_bus.stop()


async def test_awareness_erreicht_ueber_redis_einen_anderen_worker(fake_redis):
    lage_id = 9990002
    await ws_bus.start()
    room_a = await _start_room(lage_id)
    room_b = await _start_room(lage_id)
    try:
        collab._rooms[lage_id] = room_a
        room_a.awareness.set_local_state({"user": {"name": "Anna", "color": "#ff0000"}})
        from pycrdt import create_awareness_message
        data = room_a.awareness.encode_awareness_update([room_a.awareness.client_id])
        message = create_awareness_message(data)
        # Simuliert den Empfang dieser Rohnachricht von einem echten Client
        # (normalerweise ruft YRoom.serve() das intern beim Eintreffen auf).
        collab._on_room_message(lage_id, message)

        collab._rooms[lage_id] = room_b

        def _got_anna():
            return any(
                state.get("user", {}).get("name") == "Anna"
                for state in room_b.awareness.states.values()
            )

        ok = await _wait_until(_got_anna)
        assert ok, "Awareness-Update kam nicht ueber Redis beim anderen Worker an"
    finally:
        collab._rooms.pop(lage_id, None)
        await _stop_room(room_a)
        await _stop_room(room_b)
        await ws_bus.stop()


async def test_ohne_redis_kein_publish_versuch(monkeypatch):
    """Ohne REDIS_URL bleibt ws_bus.enabled() False -- der ydoc-Observer/on_message-
    Hook duerfen dann keine asyncio.create_task()-Publish-Versuche anstossen."""
    lage_id = 9990003
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "")
    assert not ws_bus.enabled()

    published = []

    async def _fake_publish(channel, payload):
        published.append((channel, payload))
    monkeypatch.setattr(ws_bus, "publish", _fake_publish)

    room = await _start_room(lage_id)
    try:
        room.ydoc.get("content", type=Text).insert(0, "x")
        room.awareness.set_local_state({"user": {"name": "Y"}})
        from pycrdt import create_awareness_message
        data = room.awareness.encode_awareness_update([room.awareness.client_id])
        collab._on_room_message(lage_id, create_awareness_message(data))
        await asyncio.sleep(0.05)
        assert not published
    finally:
        await _stop_room(room)
