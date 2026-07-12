"""Lagedokument: Yjs-CRDT-Sync-Server (Realtime-Kollaboration).

Verwaltet pro Lage einen In-Memory-Room (Y.Doc + verbundene Clients) und
laedt/speichert den CRDT-Zustand aus/in LageDokument.ydoc_state. Reine
Python-Implementierung des Yjs-Sync-Protokolls (pycrdt/pycrdt-websocket) --
kein Node.js noetig, gleicher Stack wie JupyterLab Realtime-Collaboration.

Persistenz: debounced Voll-Snapshot (10s nach der letzten Aenderung, zusaetzlich
beim Verwerfen eines leeren Rooms). Kein Event-Log/eigene BaseYStore-Klasse --
fuer ein einzelnes Textdokument je Lage waere das unnoetige Komplexitaet.

WICHTIG: Rooms leben nur In-Memory PRO WORKER (wie die bestehende Lageführung-
Presence in app/routers/ws.py) -- bei mehreren Workern (Prod: -w 2) sehen sich
Nutzer auf unterschiedlichen Workern ohne den Redis-Relay aus PR5 nicht live.
"""
from __future__ import annotations

import asyncio
import logging

from pycrdt import Doc
from pycrdt.websocket.yroom import YRoom

logger = logging.getLogger("einsatzleiter.lagedokument_collab")

_SAVE_DEBOUNCE_S = 10

_rooms: dict[int, YRoom] = {}
_room_tasks: dict[int, asyncio.Task] = {}
_save_tasks: dict[int, asyncio.Task] = {}
_dirty: dict[int, bool] = {}
_room_org: dict[int, int] = {}
_lock = asyncio.Lock()


def _load_ydoc_state(lage_id: int) -> bytes | None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import LageDokument

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dok = db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first()
        return dok.ydoc_state if dok else None
    finally:
        db.close()


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


def _mark_dirty(lage_id: int, _event=None) -> None:
    _dirty[lage_id] = True


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
        existing = await asyncio.to_thread(_load_ydoc_state, lage_id)
        if existing:
            try:
                doc.apply_update(existing)
            except Exception:
                logger.exception(
                    "Lagedokument %s: gespeicherter CRDT-Zustand beschaedigt, starte leer", lage_id,
                )
        room = YRoom(ydoc=doc)
        _rooms[lage_id] = room
        _room_org[lage_id] = org_id
        _dirty[lage_id] = False
        room.ydoc.observe(lambda event, _lid=lage_id: _mark_dirty(_lid, event))
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
        await room.stop()
        if room_task:
            room_task.cancel()
        _rooms.pop(lage_id, None)
        _dirty.pop(lage_id, None)
        _room_org.pop(lage_id, None)
