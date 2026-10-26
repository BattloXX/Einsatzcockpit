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
    import fakeredis
    import fakeredis.aioredis as far
    server = fakeredis.FakeServer()

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


@pytest.fixture
async def workers(monkeypatch):
    """Getrennte Room-Registries samt Lifecycle-Tasks fuer zwei Worker."""
    from app.services import gateway_presence

    states = {}
    for worker in ("A", "B"):
        states[worker] = {name: {} for name in (
            "_rooms", "_room_tasks", "_save_tasks", "_dirty", "_room_org",
        )}

    def activate(worker):
        for name, value in states[worker].items():
            monkeypatch.setattr(collab, name, value)
        monkeypatch.setattr(gateway_presence, "worker_id", lambda: worker)

    monkeypatch.setattr(collab, "_lock", asyncio.Lock())
    monkeypatch.setattr(collab, "_applying_remote_update", set())
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "")
    yield activate
    for worker in states:
        activate(worker)
        for lage_id in list(collab._rooms):
            await collab.release_room_if_empty(lage_id)


@pytest.fixture
async def persisted_lage(setup_db):
    from uuid import uuid4

    from tests.test_lagedokument_pr2_collab import _make_user_with_lage

    suffix = uuid4().hex[:12]
    org_id, lage_id = await asyncio.to_thread(
        _make_user_with_lage, f"ld5_{suffix}", org_slug=f"ld5-{suffix}", rolle="incident_leader",
    )
    await asyncio.to_thread(collab._save_ydoc_state, lage_id, Doc().get_update(), org_id)
    return org_id, lage_id


def _assert_both(doc):
    text = str(doc.get("content", type=Text))
    assert "Text von A" in text
    assert "Text von B" in text


@pytest.mark.parametrize("first", ["A", "B"])
async def test_stale_rooms_merge_on_save(workers, persisted_lage, first):
    org_id, lage_id = persisted_lage
    workers("A")
    room_a = await collab.get_or_create_room(lage_id, org_id)
    room_a.ydoc.get("content", type=Text).insert(0, "Text von A")
    workers("B")
    room_b = await collab.get_or_create_room(lage_id, org_id)
    assert str(room_b.ydoc.get("content", type=Text)) == ""
    room_b.ydoc.get("content", type=Text).insert(0, "Text von B")

    for worker in (first, "B" if first == "A" else "A"):
        workers(worker)
        await collab.release_room_if_empty(lage_id)
    state, _ = await asyncio.to_thread(collab._load_ydoc_state, lage_id)
    saved = Doc()
    saved.apply_update(state)
    _assert_both(saved)
    _assert_both(room_b.ydoc if first == "A" else room_a.ydoc)


async def test_join_sync_holt_ungespeicherten_text_ohne_echo(workers, persisted_lage, monkeypatch):
    org_id, lage_id = persisted_lage
    workers("A")
    room_a = await collab.get_or_create_room(lage_id, org_id)
    room_a.ydoc.get("content", type=Text).insert(0, "Text von A")
    workers("B")
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://fake")
    published = []

    async def publish(channel, payload):
        assert channel == ws_bus.CH_LAGEDOKUMENT
        published.append(payload)
        return True

    monkeypatch.setattr(ws_bus, "publish", publish)
    room_b = await collab.get_or_create_room(lage_id, org_id)
    request = published.pop()
    assert request["worker"] == "B"
    assert "sync_request" in request
    assert str(room_b.ydoc.get("content", type=Text)) == ""
    await collab._bus_deliver_update(request)
    assert published == []  # Eigener Request wird ignoriert.
    workers("A")
    await collab._bus_deliver_update(request)
    response = published.pop()
    assert "update" in response
    workers("B")
    await collab._bus_deliver_update(response)
    await asyncio.sleep(0)
    assert str(room_b.ydoc.get("content", type=Text)) == "Text von A"
    assert published == []  # Remote-Anwendung erzeugt kein Echo.

    # Nach Reconnect fragen alle lokalen Rooms erneut nach fehlenden Updates.
    await collab._sync_rooms()
    assert published[0]["worker"] == "B"
    request = published.pop()
    workers("A")
    await collab._bus_deliver_update(request)
    assert published == []  # Identischer Stand braucht keine Antwort.


async def test_merge_on_save_identischer_stand_ist_idempotent(persisted_lage):
    org_id, lage_id = persisted_lage
    doc = Doc()
    doc.get("content", type=Text).insert(0, "Unveraendert")
    first = await asyncio.to_thread(collab._save_ydoc_state, lage_id, doc.get_update(), org_id)
    second = await asyncio.to_thread(collab._save_ydoc_state, lage_id, first, org_id)
    assert first == second
    state, _ = await asyncio.to_thread(collab._load_ydoc_state, lage_id)
    assert state == first


async def test_debounced_save_wendet_db_merge_lokal_an(workers, persisted_lage, monkeypatch):
    org_id, lage_id = persisted_lage
    workers("A")
    room_a = await collab.get_or_create_room(lage_id, org_id)
    room_a.ydoc.get("content", type=Text).insert(0, "Text von A")
    workers("B")
    room_b = await collab.get_or_create_room(lage_id, org_id)
    room_b.ydoc.get("content", type=Text).insert(0, "Text von B")
    await collab.release_room_if_empty(lage_id)
    workers("A")
    monkeypatch.setattr(collab, "_SAVE_DEBOUNCE_S", .01)
    # Den bereits mit 10s wartenden Saver durch einen neuen ersetzen.
    collab._save_tasks[lage_id].cancel()
    collab._save_tasks[lage_id] = asyncio.create_task(collab._debounced_saver(lage_id, org_id))
    published = []

    async def publish(channel, payload):
        published.append((channel, payload))
        return True

    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://fake")
    monkeypatch.setattr(ws_bus, "publish", publish)
    assert await _wait_until(lambda: "Text von B" in str(room_a.ydoc.get("content", type=Text)))
    _assert_both(room_a.ydoc)
    assert published == []
    state, _ = await asyncio.to_thread(collab._load_ydoc_state, lage_id)
    saved = Doc()
    saved.apply_update(state)
    _assert_both(saved)
