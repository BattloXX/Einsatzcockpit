"""Infoscreen-Wakebus und lokale Sockets; Snapshots bleiben die einzige Autoritaet.

Keine Einsatzpayloads im Kanal. Nebenwirkungen laufen in eigenen Tasks, damit
weder Redis noch ein langsamer Monitor den Alarmpfad aufhalten.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import event, inspect, update
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState

from app.core.resilience import run_side_effect, run_side_effect_async
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDeviceEvent, InfoscreenDisplay
from app.services import ws_bus
from app.services.gateway_presence import worker_id
from app.services.infoscreen_agent_service import resolve_device_token

PING_S = 25
TTL_S = 75
# Schluessel enthalten Org UND Geraet; Connection-ID schuetzt vor altem finally.
_connections: dict[tuple[int, int, str], WebSocket] = {}
_tasks: set[asyncio.Task] = set()
_loop: asyncio.AbstractEventLoop | None = None


async def start() -> None:
    global _loop
    _loop = asyncio.get_running_loop()


async def stop() -> None:
    global _loop
    _loop = None
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def schedule(fn, *args) -> None:
    # Auch Broadcasts aus einem kurzlebigen DB-Thread-Loop behalten ihren Task.
    if _loop is not None and _loop.is_running() and _loop is not asyncio.get_running_loop():
        _loop.call_soon_threadsafe(schedule, fn, *args)
        return

    async def safe():
        await run_side_effect_async("infoscreen_wake", fn(*args))
    task = asyncio.create_task(safe())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _targets(org_id: int, display_ids: list[int] | None = None) -> list[dict]:
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        query = db.query(InfoscreenDisplay).filter(InfoscreenDisplay.org_id == org_id)
        if display_ids is not None:
            query = query.filter(InfoscreenDisplay.id.in_(display_ids))
        return [{"org_id": org_id, "device_id": d.device_id, "display_key": d.display_key,
                 "revision": d.revision} for d in query.all()]


async def _publish(payload: dict) -> None:
    if not (ws_bus.enabled() and await ws_bus.publish(ws_bus.CH_ISA, payload)):
        await deliver(payload)


async def publish_org_changed(org_id: int, reason: str, display_ids: list[int] | None = None) -> None:
    for target in await asyncio.to_thread(_targets, org_id, display_ids):
        await _publish({**target, "type": "invalidate", "reason": reason})


async def publish_device_config_changed(org_id: int, device_id: int, display_key: str, revision: int) -> None:
    """Altes Geraet wecken, wenn sein Display nach dem Commit nicht mehr zuordenbar ist."""
    await _publish({"type": "invalidate", "org_id": org_id, "device_id": device_id,
                    "display_key": display_key, "revision": revision, "reason": "config_changed"})


async def disconnect_device(org_id: int, device_id: int) -> None:
    """Nach Widerruf/Loeschen alle Worker gegen die DB abgleichen lassen."""
    await _publish({"type": "reconcile", "org_id": org_id, "device_id": device_id})


async def publish_config_changed(org_id: int, display_ids: list[int]) -> None:
    """Nach Config-Commit aufrufen; Versionierung liegt beim mutierenden Service."""
    await publish_org_changed(org_id, "config_changed", display_ids)


def _valid(org_id: int, device_id: int, connection_id: str, token_hash: str | None = None,
           *, touch: bool = False) -> bool:
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        conditions = [InfoscreenDevice.org_id == org_id, InfoscreenDevice.id == device_id,
                      InfoscreenDevice.status == "active", InfoscreenDevice.device_token_hash.is_not(None),
                      InfoscreenDevice.agent_connection_id == connection_id,
                      InfoscreenDevice.agent_presence_expires_at > now()]
        if token_hash is not None:
            conditions.append(InfoscreenDevice.device_token_hash == token_hash)
        if touch:
            result = db.connection().execute(update(InfoscreenDevice).where(*conditions).values(
                agent_presence_expires_at=now() + timedelta(seconds=TTL_S)))
            db.commit()
            return result.rowcount == 1
        return db.query(InfoscreenDevice.id).filter(*conditions).first() is not None


async def _deliver_one(key: tuple[int, int, str], socket: WebSocket, payload: dict) -> None:
    org, device, connection = key
    if not await asyncio.to_thread(_valid, org, device, connection):
        _connections.pop(key, None)
        if socket.application_state == WebSocketState.CONNECTED:
            await asyncio.wait_for(socket.close(code=4401), timeout=2)
    elif payload.get("type") == "update_available":
        await asyncio.wait_for(socket.send_json({"type": "update_available"}), timeout=2)
    elif payload.get("type") == "invalidate":
        # Niemals beliebige Bus-/Browser-Payloads an das Geraet weiterreichen.
        await asyncio.wait_for(socket.send_json({k: payload[k] for k in
                               ("type", "revision", "reason", "display_key")}), timeout=2)


async def deliver(payload: dict) -> None:
    """Bus-Handler: immer gegen aktuelle DB-Presence pruefen, auch bei spaetem Echo."""
    org_id, device_id = payload.get("org_id"), payload.get("device_id")
    await asyncio.gather(*(
        run_side_effect_async("infoscreen_deliver", _deliver_one(key, socket, payload))
        for key, socket in list(_connections.items())
        if key[0] == org_id and (device_id is None or device_id == key[1])
    ))


async def reconnect() -> None:
    orgs = {key[0] for key in _connections}
    for org_id in orgs:
        await deliver({"org_id": org_id, "type": "reconcile"})
        # Nur lokal: jeder Worker wird vom Bus bei Reconnect aufgerufen.
        for target in await asyncio.to_thread(_targets, org_id):
            await deliver({**target, "type": "invalidate", "reason": "reconnect"})


def _begin(token: str, connection: str) -> tuple[int, int, str] | None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        device = resolve_device_token(db, token)
        if device is None or device.org_id is None:
            return None
        org_id, device_id = device.org_id, device.id
        token_hash = hash_api_key(token)
        result = db.connection().execute(update(InfoscreenDevice).where(
            InfoscreenDevice.org_id == org_id, InfoscreenDevice.id == device_id,
            InfoscreenDevice.status == "active", InfoscreenDevice.device_token_hash == token_hash,
        ).values(agent_connection_id=connection, agent_worker_id=worker_id(),
                 agent_presence_expires_at=now() + timedelta(seconds=TTL_S)))
        if result.rowcount != 1:
            db.rollback()
            return None
        db.add(InfoscreenDeviceEvent(org_id=org_id, device_id=device_id, typ="connect", payload_json={}))
        db.commit()
        return org_id, device_id, token_hash


def _end(org_id: int, device_id: int, connection: str) -> None:
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        db.connection().execute(update(InfoscreenDevice).where(
            InfoscreenDevice.org_id == org_id, InfoscreenDevice.id == device_id,
            InfoscreenDevice.agent_connection_id == connection,
        ).values(agent_connection_id=None, agent_worker_id=None, agent_presence_expires_at=None))
        db.add(InfoscreenDeviceEvent(org_id=org_id, device_id=device_id, typ="disconnect", payload_json={}))
        db.commit()


async def websocket_session(socket: WebSocket) -> None:
    scheme, _, token = socket.headers.get("authorization", "").partition(" ")
    identity = None
    connection = uuid4().hex
    if scheme.lower() == "bearer" and len(token) <= 256:
        identity = await asyncio.to_thread(_begin, token.strip(), connection)
    if identity is None:
        await socket.close(code=4401)
        return
    org_id, device_id, token_hash = identity
    key = (org_id, device_id, connection)
    try:
        await socket.accept()
        _connections[key] = socket
        await _publish({"type": "reconcile", "org_id": org_id, "device_id": device_id})
        # Sofortiger Wake auch nach Redis-Ausfall; Agent holt config/state.
        for target in await asyncio.to_thread(_targets, org_id):
            if target["device_id"] == device_id:
                await deliver({**target, "type": "invalidate", "reason": "reconnect"})
        while True:
            if not await asyncio.to_thread(_valid, org_id, device_id, connection, token_hash):
                if socket.application_state == WebSocketState.CONNECTED:
                    await socket.close(code=4401)
                return
            await socket.send_json({"type": "ping"})
            # Nur Pong verlaengert Presence; beliebige Nachrichten halten sie nicht am Leben.
            async with asyncio.timeout(PING_S):
                while True:
                    raw = await socket.receive_text()
                    if len(raw) > 128:
                        await socket.close(code=4400)
                        return
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if message == {"type": "pong"}:
                        break
            if not await asyncio.to_thread(_valid, org_id, device_id, connection, token_hash, touch=True):
                if socket.application_state == WebSocketState.CONNECTED:
                    await socket.close(code=4401)
                return
            await asyncio.sleep(PING_S)
    except TimeoutError:
        if socket.application_state == WebSocketState.CONNECTED:
            await socket.close(code=4408)
    except WebSocketDisconnect:
        pass
    finally:
        _connections.pop(key, None)
        with anyio.CancelScope(shield=True):
            await asyncio.to_thread(_end, org_id, device_id, connection)


@event.listens_for(Session, "before_flush")
def _auth_changes(db: Session, flush_context, instances) -> None:
    for device in list(db.dirty):
        if not isinstance(device, InfoscreenDevice) or device.org_id is None:
            continue
        state = inspect(device)
        if (state.attrs.device_token_hash.history.has_changes()
                or (state.attrs.status.history.has_changes() and device.status == "revoked")):
            db.add(InfoscreenDeviceEvent(org_id=device.org_id, device_id=device.id,
                                        typ="token_revoked", payload_json={}))
            db.info.setdefault("isa_reconcile", set()).add((device.org_id, device.id))


@event.listens_for(Session, "after_commit")
def _committed(db: Session) -> None:
    # SQLAlchemy-Callbacks koennen auch in normalen DB-Threads laufen. Immer
    # auf den Worker-Loop stellen: Ein temporaerer asyncio.run()-Loop wuerde
    # einen dort erzeugten Task beim Verlassen sofort wieder abbrechen.
    for org_id, device_id in db.info.pop("isa_reconcile", set()):
        if _loop is not None and _loop.is_running():
            run_side_effect("infoscreen_revoke", _loop.call_soon_threadsafe, schedule, _publish,
                            {"type": "reconcile", "org_id": org_id, "device_id": device_id})


@event.listens_for(Session, "after_rollback")
def _rolled_back(db: Session) -> None:
    db.info.pop("isa_reconcile", None)


ws_bus.register(ws_bus.CH_ISA, deliver)
ws_bus.on_reconnect(reconnect)


def _channel_org(channel: int) -> int | None:
    from app.models.incident import Incident
    from app.models.major_incident import MajorIncident
    from app.services.broadcast import LAGE_WS_OFFSET, ORG_WS_OFFSET

    if ORG_WS_OFFSET <= channel < ORG_WS_OFFSET + 10_000_000:
        return channel - ORG_WS_OFFSET
    with SessionLocal() as db:
        set_tenant_context(db, None)
        if LAGE_WS_OFFSET <= channel < ORG_WS_OFFSET:
            return db.query(MajorIncident.org_id).filter(MajorIncident.id == channel - LAGE_WS_OFFSET).scalar()
        elif 0 < channel < LAGE_WS_OFFSET:
            return db.query(Incident.primary_org_id).filter(Incident.id == channel).scalar()
        else:
            return None


async def _channel_changed(channel: int, reason: str) -> None:
    org_id = await asyncio.to_thread(_channel_org, channel)
    if org_id is not None:
        await publish_org_changed(org_id, reason)


def broadcast_hook(channel: int, event_type: str) -> None:
    """Zentrale Hooks fuer Incident/GSL, Alarm-Apply, DIBOS und Planspiel.

    Uebernimmt nur den Typ; Einsatztexte/Adressen gelangen nie in die Tasks.
    Kein DB-/Netzzugriff auf dem aufrufenden Alarmpfad.
    """
    if event_type.startswith("incident_"):
        reason = {"incident_created": "incident_started", "incident_closed": "incident_finished"}.get(
            event_type, "incident_changed")
    elif (event_type in ("lage_closed", "lage_updated", "planspiel_changed")
          or event_type.startswith("gsl_")):
        reason = "gsl_finished" if event_type == "lage_closed" else "gsl_changed"
    elif event_type in ("einsatz_live", "alarm_type_changed", "address_updated"):
        reason = "incident_changed"
    else:
        return
    schedule(_channel_changed, channel, reason)


async def publish_update_available(org_id: int, device_id: int) -> None:
    """Plattform- und Geraeteverwaltung: Wake auf demselben Mehrworker-Bus."""
    await _publish({"type": "update_available", "org_id": org_id, "device_id": device_id})
