"""Lagedokument: Yjs-CRDT-Sync-Server (Realtime-Kollaboration).

Verwaltet pro Lage einen In-Memory-Room (Y.Doc + verbundene Clients) und
laedt/speichert den CRDT-Zustand aus/in LageDokument.ydoc_state. Reine
Python-Implementierung des Yjs-Sync-Protokolls (pycrdt/pycrdt-websocket) --
kein Node.js noetig, gleicher Stack wie JupyterLab Realtime-Collaboration.

Persistenz: debounced Voll-Snapshot (10s nach der letzten Aenderung, zusaetzlich
beim Verwerfen eines leeren Rooms). Kein Event-Log/eigene BaseYStore-Klasse --
fuer ein einzelnes Textdokument je Lage waere das unnoetige Komplexitaet.

Mehr-Worker-Korrektheit (Prod: -w 2): Rooms leben weiterhin In-Memory PRO
WORKER (wie die bestehende Lageführung-Presence in app/routers/ws.py), aber
Inhalts-Updates UND Awareness (Presence/Cursor) werden zusaetzlich ueber den
bestehenden Redis-Bus (app/services/ws_bus.py, CH_LAGEDOKUMENT[_AWARENESS])
an alle Worker weitergereicht, die lokal denselben Room offen haben -- ohne
REDIS_URL (Dev/-w 1) ist der Bus inaktiv und ws_bus.publish() ein No-Op, dann
bleibt alles wie zuvor rein In-Process.
"""
from __future__ import annotations

import asyncio
import base64
import logging

from pycrdt import Doc, YMessageType, read_message
from pycrdt.websocket.yroom import YRoom

from app.services import ws_bus

logger = logging.getLogger("einsatzleiter.lagedokument_collab")

_SAVE_DEBOUNCE_S = 10

_rooms: dict[int, YRoom] = {}
_room_tasks: dict[int, asyncio.Task] = {}
_save_tasks: dict[int, asyncio.Task] = {}
_dirty: dict[int, bool] = {}
_room_org: dict[int, int] = {}
_lock = asyncio.Lock()

# Waehrend ein via Redis empfangenes Remote-Update/-Awareness-Frame auf den
# lokalen Room angewendet wird, fuer diese Lage gesetzt -- verhindert, dass
# der eigene ydoc-Observer/on_message-Hook es sofort wieder zurueck an den
# Bus publiziert (Echo-Schleife zwischen den Workern).
_applying_remote_update: set[int] = set()
_applying_remote_awareness: set[int] = set()


def _load_ydoc_state(lage_id: int) -> tuple[bytes | None, str | None]:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import LageDokument

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dok = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first()
        if dok is None:
            return None, None
        return dok.ydoc_state, dok.content_html
    finally:
        db.close()


def _strip_html_to_text(html: str) -> str:
    """Best-effort Klartext-Extraktion fuer den einmaligen Bootstrap eines
    Y.Text aus einem vorhandenen content_html-Snapshot (Uebergangsfall: ein
    Lagedokument, das per klassischem Speichern-Formular -- PR1, vor dem ersten
    Live-Kollaborations-Aufruf -- entstanden ist, hat noch keinen ydoc_state).
    Verliert Formatierung, erhaelt aber den Text -- kein sicherheitsrelevanter
    Pfad (nur Ausgangszustand des Editors, kein gespeicherter/gerenderter Wert)."""
    import re
    from html import unescape

    text = re.sub(r"<(p|div|br|li)[^>]*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _save_ydoc_state(lage_id: int, state: bytes, org_id: int) -> None:
    from datetime import UTC, datetime

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import LageDokument

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dok = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first()
        if dok is None:
            dok = LageDokument(major_incident_id=lage_id, org_id=org_id, updated_at=datetime.now(UTC))
            db.add(dok)
        dok.ydoc_state = state
        db.commit()
    finally:
        db.close()


def _on_ydoc_event(lage_id: int, event) -> None:
    """ydoc.observe-Callback: markiert den Room als dirty (fuer den debounced
    Save) UND reicht eigene (nicht via Bus empfangene) Aenderungen an die
    anderen Worker weiter."""
    _dirty[lage_id] = True
    if lage_id in _applying_remote_update or not ws_bus.enabled():
        return
    asyncio.create_task(_publish_update(lage_id, event.update))


async def _publish_update(lage_id: int, update: bytes) -> None:
    await ws_bus.publish(ws_bus.CH_LAGEDOKUMENT, {
        "lage_id": lage_id,
        "update": base64.b64encode(update).decode("ascii"),
    })


async def _bus_deliver_update(payload: dict) -> None:
    """Bus-Handler (CH_LAGEDOKUMENT): wendet ein auf einem anderen Worker
    entstandenes Update auf den lokalen Room an (No-Op, falls hier kein
    lokaler Room fuer diese Lage offen ist)."""
    lage_id = payload.get("lage_id")
    update_b64 = payload.get("update")
    if lage_id is None or not update_b64:
        return
    room = _rooms.get(lage_id)
    if room is None:
        return
    try:
        update = base64.b64decode(update_b64)
    except Exception:
        logger.exception("Lagedokument %s: ungueltiges Bus-Update ignoriert", lage_id)
        return
    _applying_remote_update.add(lage_id)
    try:
        room.ydoc.apply_update(update)
    finally:
        _applying_remote_update.discard(lage_id)


def _on_room_message(lage_id: int, message: bytes) -> bool:
    """room.on_message-Hook: reicht Awareness-Rohnachrichten (Presence/Cursor)
    zusaetzlich an die anderen Worker weiter. Gibt nie True zurueck -- die
    normale Verarbeitung durch YRoom.serve() (lokale Zustellung, Anwenden auf
    room.awareness) laeuft immer unveraendert weiter, dies ist nur ein
    zusaetzlicher Abzweig."""
    if message and message[0] == YMessageType.AWARENESS and lage_id not in _applying_remote_awareness \
            and ws_bus.enabled():
        asyncio.create_task(_publish_awareness(lage_id, message))
    return False


async def _publish_awareness(lage_id: int, message: bytes) -> None:
    await ws_bus.publish(ws_bus.CH_LAGEDOKUMENT_AWARENESS, {
        "lage_id": lage_id,
        "message": base64.b64encode(message).decode("ascii"),
    })


async def _bus_deliver_awareness(payload: dict) -> None:
    """Bus-Handler (CH_LAGEDOKUMENT_AWARENESS): reicht ein auf einem anderen
    Worker empfangenes Awareness-Frame an die lokal verbundenen Clients
    weiter und wendet es auf den lokalen room.awareness-Zustand an."""
    lage_id = payload.get("lage_id")
    message_b64 = payload.get("message")
    if lage_id is None or not message_b64:
        return
    room = _rooms.get(lage_id)
    if room is None:
        return
    try:
        message = base64.b64decode(message_b64)
    except Exception:
        logger.exception("Lagedokument %s: ungueltiges Bus-Awareness-Frame ignoriert", lage_id)
        return
    _applying_remote_awareness.add(lage_id)
    try:
        for client in list(room.clients):
            asyncio.create_task(client.send(message))
        room.awareness.apply_awareness_update(read_message(message[1:]), room)
    finally:
        _applying_remote_awareness.discard(lage_id)


# Beim Import registrieren, damit ws_bus.start() (main.py, nach Router-Import)
# beide Kanaele abonniert -- ui_lagedokument.py importiert dieses Modul daher
# auf Modulebene (nicht erst lazy im WS-Handler), siehe dortiger Kommentar.
ws_bus.register(ws_bus.CH_LAGEDOKUMENT, _bus_deliver_update)
ws_bus.register(ws_bus.CH_LAGEDOKUMENT_AWARENESS, _bus_deliver_awareness)


async def _debounced_saver(lage_id: int, org_id: int) -> None:
    while True:
        await asyncio.sleep(_SAVE_DEBOUNCE_S)
        room = _rooms.get(lage_id)
        if room is None:
            return
        if _dirty.get(lage_id):
            _dirty[lage_id] = False
            state = room.ydoc.get_update()
            await asyncio.to_thread(_save_ydoc_state, lage_id, state, org_id)


async def get_or_create_room(lage_id: int, org_id: int) -> YRoom:
    """Liefert den (In-Memory-)Room fuer eine Lage, erzeugt ihn bei Bedarf inkl.
    Laden des zuletzt gespeicherten CRDT-Zustands aus der DB."""
    async with _lock:
        room = _rooms.get(lage_id)
        if room is not None:
            return room
        doc = Doc()
        existing_state, existing_html = await asyncio.to_thread(_load_ydoc_state, lage_id)
        if existing_state:
            try:
                doc.apply_update(existing_state)
            except Exception:
                logger.exception(
                    "Lagedokument %s: gespeicherter CRDT-Zustand beschaedigt, starte leer", lage_id,
                )
        elif existing_html:
            # Bootstrap: noch kein Yjs-Zustand vorhanden, aber ein klassischer
            # content_html-Snapshot aus PR1 -- Klartext als Ausgangsstand uebernehmen
            # (Formatierung geht dabei verloren, siehe _strip_html_to_text).
            from pycrdt import Text as _Text
            text = _strip_html_to_text(existing_html)
            if text:
                doc.get("content", type=_Text).insert(0, text)
        room = YRoom(ydoc=doc)
        _rooms[lage_id] = room
        _room_org[lage_id] = org_id
        _dirty[lage_id] = False
        room.ydoc.observe(lambda event, _lid=lage_id: _on_ydoc_event(_lid, event))
        room.on_message = lambda message, _lid=lage_id: _on_room_message(_lid, message)
        _room_tasks[lage_id] = asyncio.create_task(room.start())
        await room.started.wait()
        _save_tasks[lage_id] = asyncio.create_task(_debounced_saver(lage_id, org_id))
        return room


async def release_room_if_empty(lage_id: int) -> None:
    """Nach Verbindungsabbau aufrufen: speichert einen letzten Snapshot und
    verwirft den Room, wenn kein Client mehr verbunden ist."""
    async with _lock:
        room = _rooms.get(lage_id)
        if room is None or room.clients:
            return
        if _dirty.get(lage_id):
            org_id = _room_org.get(lage_id)
            if org_id is not None:
                state = room.ydoc.get_update()
                await asyncio.to_thread(_save_ydoc_state, lage_id, state, org_id)
        save_task = _save_tasks.pop(lage_id, None)
        if save_task:
            save_task.cancel()
        room_task = _room_tasks.pop(lage_id, None)
        try:
            await room.stop()
        except RuntimeError:
            # room.started wird von pycrdt gesetzt, bevor die interne Awareness-Task
            # ihr eigenes Setup abgeschlossen hat (Race) -- kann bei einem Release
            # unmittelbar nach dem Erzeugen auftreten (z.B. sofortiger Verbindungsabbruch).
            # Dann ist ohnehin nichts zu stoppen.
            logger.debug("Lagedokument %s: Room war beim Stop noch nicht vollstaendig gestartet", lage_id)
        if room_task:
            room_task.cancel()
        _rooms.pop(lage_id, None)
        _dirty.pop(lage_id, None)
        _room_org.pop(lage_id, None)
        _applying_remote_update.discard(lage_id)
        _applying_remote_awareness.discard(lage_id)
