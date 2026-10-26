"""Adressierte DB-Transportqueue. Redis weckt auf; Polling ueberlebt Redis-Ausfaelle.

Ein atomarer Claim verhindert doppelte Sends durch Echo/Polling. Nach Beginn eines
Sends wird nie blind erneut gesendet (moegliche externe SMS-/Druckwirkung).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import WebSocket

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import GatewayMessage
from app.services import gateway_presence as presence
from app.services import ws_bus
from app.services.loop_utils import start_loop_task, stop_loop_task

logger = logging.getLogger("einsatzleiter.gateway_routing")
POLL_S = 0.5
SEND_TIMEOUT_S = 5.0


@dataclass
class LocalSocket:
    connection: presence.Connection
    token_hash: str
    websocket: WebSocket


_sockets: dict[tuple[str, int, int, str], LocalSocket] = {}
_task: asyncio.Task | None = None


def key(connection: presence.Connection) -> tuple[str, int, int, str]:
    return connection.kind, connection.org_id, connection.gateway_id, connection.connection_id


def attach(connection: presence.Connection, token_hash: str, websocket: WebSocket) -> None:
    _sockets[key(connection)] = LocalSocket(connection, token_hash, websocket)


def detach(connection: presence.Connection) -> None:
    _sockets.pop(key(connection), None)


def _db():
    from app.services.db_transactions import current_read_session

    db = current_read_session(SessionLocal)
    set_tenant_context(db, None)
    return db


def targets(kind: str, org_id: int, gateway_id: int | None = None) -> list[presence.Connection]:
    with _db() as db:
        return presence.connections(db, kind, org_id, gateway_id)


def begin(kind: str, org_id: int, gateway_id: int, token_hash: str) -> presence.Connection | None:
    with _db() as db:
        cid = uuid4().hex
        if not presence.begin(db, kind, org_id, gateway_id, token_hash, cid):
            return None
        return next((c for c in presence.connections(db, kind, org_id, gateway_id) if c.connection_id == cid), None)


def state(connection: presence.Connection, token_hash: str) -> str:
    with _db() as db:
        return presence.state(
            db, connection.kind, connection.org_id, connection.gateway_id, token_hash, connection.connection_id
        )


def touch(connection: presence.Connection, token_hash: str) -> bool:
    with _db() as db:
        return presence.touch(
            db, connection.kind, connection.org_id, connection.gateway_id, token_hash, connection.connection_id
        )


def end(connection: presence.Connection) -> bool:
    with _db() as db:
        return presence.end(db, connection.kind, connection.org_id, connection.gateway_id, connection.connection_id)


def enqueue_in(db, connection: presence.Connection, message: dict, ttl: float, job_key: str | None) -> str:
    """Aufrufende Geschaeftstransaktion besitzt den Commit."""
    now = presence.utc()
    item = GatewayMessage(
        id=uuid4().hex,
        org_id=connection.org_id,
        gateway_kind=connection.kind,
        gateway_id=connection.gateway_id,
        connection_id=connection.connection_id,
        worker_id=connection.worker_id,
        message=message,
        job_key=job_key,
        state="queued",
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    db.add(item)
    db.flush()
    return item.id


def _enqueue(connection: presence.Connection, message: dict, ttl: float, job_key: str | None) -> str:
    with _db() as db:
        item_id = enqueue_in(db, connection, message, ttl, job_key)
        db.commit()
        return item_id


async def send(connection: presence.Connection, message: dict, *, ttl: float = 30, job_key: str | None = None) -> str:
    item_id = await asyncio.to_thread(_enqueue, connection, message, ttl, job_key)
    # Lokaler Pfad funktioniert auch bei konfiguriertem, aber ausgefallenem Redis.
    await deliver(item_id)
    await ws_bus.publish(ws_bus.CH_GW, envelope(connection, item_id))
    return item_id


def envelope(connection: presence.Connection, item_id: str) -> dict:
    return {
        "kind": "route",
        "message_id": item_id,
        "gateway_kind": connection.kind,
        "org_id": connection.org_id,
        "gateway_id": connection.gateway_id,
        "connection_id": connection.connection_id,
        "worker_id": connection.worker_id,
    }


def _claim(item_id: str, address: dict | None = None):
    with _db() as db:
        item = (
            db.query(GatewayMessage)
            .filter(
                GatewayMessage.id == item_id,
                GatewayMessage.worker_id == presence.worker_id(),
                GatewayMessage.state == "queued",
            )
            .first()
        )
        if item is None:
            return None
        if address is not None and any(
            address.get(name) != getattr(item, name)
            for name in ("gateway_kind", "org_id", "gateway_id", "connection_id", "worker_id")
        ):
            return None
        socket = _sockets.get((item.gateway_kind, item.org_id, item.gateway_id, item.connection_id))
        if socket is None:
            return None
        c = socket.connection
        valid = (
            presence.state(db, c.kind, c.org_id, c.gateway_id, socket.token_hash, c.connection_id, lock=True)
            == "connected"
        )
        valid = valid and item.expires_at > presence.utc()
        changed = (
            db.query(GatewayMessage)
            .filter(
                GatewayMessage.id == item_id,
                GatewayMessage.org_id == c.org_id,
                GatewayMessage.state == "queued",
                GatewayMessage.worker_id == c.worker_id,
                GatewayMessage.connection_id == c.connection_id,
            )
            .update({"state": "sending" if valid else "expired"}, synchronize_session=False)
        )
        db.commit()
        return (socket, item.message) if changed and valid else None


def _finish(item_id: str, org_id: int, state_value: str, error: str | None = None) -> None:
    with _db() as db:
        db.query(GatewayMessage).filter(
            GatewayMessage.id == item_id,
            GatewayMessage.org_id == org_id,
            GatewayMessage.worker_id == presence.worker_id(),
            GatewayMessage.state == "sending",
        ).update({"state": state_value, "error": error}, synchronize_session=False)
        db.commit()


async def deliver(item_id: str, *, address: dict | None = None) -> None:
    claim = await asyncio.to_thread(_claim, item_id, address)
    if claim is None:
        return
    socket, message = claim
    try:
        await asyncio.wait_for(
            socket.websocket.send_text(json.dumps(message, ensure_ascii=False, default=str)), timeout=SEND_TIMEOUT_S
        )
    except Exception as exc:
        # Kein automatischer Replay nach einem moeglicherweise begonnenen Send.
        logger.warning(
            "Gateway-Send fehlgeschlagen org=%s gateway=%s connection=%s error_type=%s",
            socket.connection.org_id,
            socket.connection.gateway_id,
            socket.connection.connection_id,
            type(exc).__name__,
        )
        await asyncio.to_thread(_finish, item_id, socket.connection.org_id, "failed", "socket_send_failed")
        await asyncio.to_thread(end, socket.connection)
        detach(socket.connection)
        try:
            await asyncio.wait_for(socket.websocket.close(code=1011), timeout=SEND_TIMEOUT_S)
        except Exception:
            logger.debug("Fehlerhaften Gateway-Socket nicht mehr schliessbar")
    else:
        await asyncio.to_thread(_finish, item_id, socket.connection.org_id, "delivered")


def record_result(connection: presence.Connection, token_hash: str, job_key: str, result: dict) -> None:
    with _db() as db:
        if (
            presence.state(
                db,
                connection.kind,
                connection.org_id,
                connection.gateway_id,
                token_hash,
                connection.connection_id,
                lock=True,
            )
            != "connected"
        ):
            return
        db.query(GatewayMessage).filter(
            GatewayMessage.org_id == connection.org_id,
            GatewayMessage.gateway_kind == connection.kind,
            GatewayMessage.gateway_id == connection.gateway_id,
            GatewayMessage.connection_id == connection.connection_id,
            GatewayMessage.job_key == job_key,
            GatewayMessage.state.in_(("sending", "delivered")),
            GatewayMessage.result.is_(None),
        ).update({"result": result}, synchronize_session=False)
        db.commit()


def _result(item_id: str, org_id: int):
    with _db() as db:
        row = db.query(GatewayMessage).filter(GatewayMessage.id == item_id, GatewayMessage.org_id == org_id).first()
        if row is None:
            return "expired", None
        if row.state == "queued" and row.expires_at <= presence.utc():
            return "expired", row.result
        return row.state, row.result


def purge_old_messages() -> int:
    """Leader-Wartung: alte Transportnachrichten in kleinen Transaktionen loeschen."""
    cutoff = presence.utc() - timedelta(days=1)
    total = 0
    while True:
        # Systemwartung: explizite Tenant-IDs fuer Mutationen, nie ungefiltertes Tenant-UPDATE.
        with _db() as db:
            old = (
                db.query(GatewayMessage.id, GatewayMessage.org_id)
                .filter(GatewayMessage.created_at < cutoff)
                .execution_options(include_all_tenants=True)
                .limit(100)
                .all()
            )
            if not old:
                return total
            for item_id, org_id in old:
                total += db.query(GatewayMessage).filter(
                    GatewayMessage.id == item_id, GatewayMessage.org_id == org_id
                ).delete()
            db.commit()


def _due() -> list[str]:
    with _db() as db:
        now = presence.utc()
        return [
            row[0]
            for row in db.query(GatewayMessage.id)
            .filter(
                GatewayMessage.worker_id == presence.worker_id(),
                GatewayMessage.state == "queued",
                GatewayMessage.expires_at > now,
            )
            .execution_options(include_all_tenants=True)
            .limit(100)
            .all()
        ]


async def _poll() -> None:
    next_check = 0.0
    while True:
        try:
            for item_id in await asyncio.to_thread(_due):
                await deliver(item_id)
            # Fuenf Sekunden statt DB-Healthquery je Socket bei jedem Queue-Poll.
            sockets_to_check = list(_sockets.values()) if time.monotonic() >= next_check else []
            if sockets_to_check:
                next_check = time.monotonic() + 5
            for socket in sockets_to_check:
                reason = await asyncio.to_thread(state, socket.connection, socket.token_hash)
                if reason != "connected":
                    detach(socket.connection)
                    await asyncio.wait_for(
                        socket.websocket.close(code=4401 if reason == "authentication_failed" else 4409),
                        timeout=SEND_TIMEOUT_S,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Gateway-Routing-Poll fehlgeschlagen")
        await asyncio.sleep(POLL_S)


async def start() -> None:
    global _task
    _task = start_loop_task(_task, _poll)


async def stop() -> None:
    global _task
    task, _task = _task, None
    await stop_loop_task(task)
    for socket in list(_sockets.values()):
        await asyncio.to_thread(end, socket.connection)
    _sockets.clear()
