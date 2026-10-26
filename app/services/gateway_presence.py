"""Gemeinsame DB-Presence fuer SMS, Linux und Windows; letzter Connect-Commit gewinnt.

Die vorhandenen Gateway.agent_*-Spalten bleiben die einzige Presence fuer Druckgateways.
Ein Socket/Token-Hash wird niemals Teil eines oeffentlichen Presence-Snapshots.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import uuid4

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models.gateway import Gateway
from app.models.user import SmsGatewayToken

SMS = "sms"
TTL_S = 90


def utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


@lru_cache(maxsize=8)
def _worker_id(pid: int) -> str:
    return f"{socket.gethostname()[:32]}:{pid}:{uuid4().hex}"


def worker_id() -> str:
    # Nach Gunicorn-Fork nie die Identitaet des Masters erben.
    return _worker_id(os.getpid())


@dataclass(frozen=True)
class Connection:
    kind: str
    org_id: int
    gateway_id: int
    connection_id: str
    worker_id: str
    expires_at: datetime
    status: str = "online"


def fields(kind: str):
    if kind == SMS:
        return SmsGatewayToken, "connection_id", "worker_id", "connected_at", "presence_expires_at", "last_heartbeat_at"
    if kind not in ("linux", "windows_agent"):
        raise ValueError("Unbekannter Gateway-Typ")
    return (
        Gateway,
        "agent_connection_id",
        "agent_worker_id",
        "agent_connected_at",
        "agent_presence_expires_at",
        "agent_heartbeat_at",
    )


def authenticated(model, kind: str, token_hash: str | None = None):
    if kind == SMS:
        terms = [model.revoked_at.is_(None)]
        if token_hash is not None:
            terms.append(model.token_hash == token_hash)
    else:
        terms = [model.kind == kind, model.device_token_hash.isnot(None)]
        if token_hash is not None:
            terms.append(model.device_token_hash == token_hash)
    return terms


def online(row, now: datetime | None = None) -> bool:
    sms = isinstance(row, SmsGatewayToken)
    valid = (
        (row.revoked_at is None and row.connection_token_hash == row.token_hash)
        if sms
        else (bool(row.device_token_hash) and row.agent_connection_token_hash == row.device_token_hash)
    )
    cid = row.connection_id if sms else row.agent_connection_id
    expiry = row.presence_expires_at if sms else row.agent_presence_expires_at
    return bool(valid and cid and expiry and utc(expiry) > utc(now))


def begin(
    db: Session,
    kind: str,
    org_id: int,
    gateway_id: int,
    token_hash: str,
    connection_id: str,
    owner: str | None = None,
    now: datetime | None = None,
) -> bool:
    if not isinstance(org_id, int) or org_id <= 0:
        return False
    model, cid, wid, connected, expiry, _heartbeat = fields(kind)
    moment = utc(now)
    values: dict[Any, Any] = {
        cid: connection_id,
        wid: owner or worker_id(),
        connected: moment,
        expiry: moment + timedelta(seconds=TTL_S),
    }
    values["connection_token_hash" if kind == SMS else "agent_connection_token_hash"] = token_hash
    if kind != SMS:
        values.update(last_seen_at=moment, status="online")
        if kind == "linux":
            values["print_protocol"] = 1
    else:
        values["last_used_at"] = moment
        values["sms_protocol"] = 1
    changed = (
        db.query(model)
        .filter(model.org_id == org_id, model.id == gateway_id, *authenticated(model, kind, token_hash))
        .update(values, synchronize_session=False)
    )
    db.commit()
    return bool(changed)


def state(
    db: Session,
    kind: str,
    org_id: int,
    gateway_id: int,
    token_hash: str,
    connection_id: str,
    now: datetime | None = None,
    *,
    lock: bool = False,
) -> str:
    model, cid, _wid, _connected, _expiry, _heartbeat = fields(kind)
    query = (
        db.query(model)
        .filter(model.org_id == org_id, model.id == gateway_id, *authenticated(model, kind, token_hash))
        .populate_existing()
    )
    row = query.with_for_update().first() if lock else query.first()
    if row is None:
        return "authentication_failed"
    if getattr(row, "connection_token_hash" if kind == SMS else "agent_connection_token_hash") != token_hash:
        return "authentication_failed"
    if getattr(row, cid) != connection_id:
        return "superseded"
    return "connected" if online(row, now) else "heartbeat_overdue"


def touch(
    db: Session,
    kind: str,
    org_id: int,
    gateway_id: int,
    token_hash: str,
    connection_id: str,
    now: datetime | None = None,
    *,
    heartbeat: bool = True,
) -> bool:
    model, cid, _wid, _connected, expiry, hb = fields(kind)
    moment = utc(now)
    values: dict[Any, Any] = {expiry: moment + timedelta(seconds=TTL_S)}
    if heartbeat:
        values[hb] = moment
    if kind != SMS:
        values.update(last_seen_at=moment, status="online")
    changed = (
        db.query(model)
        .filter(
            model.org_id == org_id,
            model.id == gateway_id,
            getattr(model, cid) == connection_id,
            getattr(model, expiry) > moment,
            *authenticated(model, kind, token_hash),
        )
        .update(values, synchronize_session=False)
    )
    db.commit()
    return bool(changed)


def end(
    db: Session, kind: str, org_id: int, gateway_id: int, connection_id: str, *, expired_before: datetime | None = None
) -> bool:
    model, cid, wid, _connected, expiry, _hb = fields(kind)
    query = db.query(model).filter(model.org_id == org_id, model.id == gateway_id, getattr(model, cid) == connection_id)
    if kind != SMS:
        query = query.filter(model.kind == kind)
    if expired_before is not None:
        query = query.filter(getattr(model, expiry) <= expired_before)
    values: dict[Any, Any] = {
        cid: None,
        wid: None,
        expiry: None,
        "connection_token_hash" if kind == SMS else "agent_connection_token_hash": None,
    }
    if kind != SMS:
        values.update(
            status=case((model.device_token_hash.is_(None), "unpaired"), else_="offline"), serial_connected=False
        )
    changed = query.update(values, synchronize_session=False)
    db.commit()
    return bool(changed)


def connections(
    db: Session, kind: str, org_id: int, gateway_id: int | None = None, now: datetime | None = None
) -> list[Connection]:
    model, cid, wid, connected, expiry, _hb = fields(kind)
    query = db.query(model).filter(
        model.org_id == org_id,
        getattr(model, cid).isnot(None),
        getattr(model, expiry) > utc(now),
        *authenticated(model, kind),
    )
    if gateway_id is not None:
        query = query.filter(model.id == gateway_id)
    query = (
        query.filter(model.connection_token_hash == model.token_hash)
        if kind == SMS
        else query.filter(model.agent_connection_token_hash == model.device_token_hash)
    )
    rows = query.order_by(getattr(model, connected).desc(), model.id.desc()).all()
    return [
        Connection(kind, org_id, row.id, getattr(row, cid), getattr(row, wid) or "", getattr(row, expiry))
        for row in rows
    ]


def expire(db: Session, now: datetime | None = None) -> list[tuple[int, int]]:
    """System-Sweep; jedes UPDATE bleibt explizit nach Org/ID/Connection/TTL gefenced."""
    moment = utc(now)
    ended = []
    for kind in (SMS, "linux", "windows_agent"):
        model, cid, _wid, _connected, expiry, _hb = fields(kind)
        query = db.query(model).filter(getattr(model, cid).isnot(None), getattr(model, expiry) <= moment)
        if kind != SMS:
            query = query.filter(model.kind == kind)
        rows = query.execution_options(include_all_tenants=True).all()
        for row in rows:
            if row.org_id is not None and end(db, kind, row.org_id, row.id, getattr(row, cid), expired_before=moment):
                if kind != SMS:
                    ended.append((row.org_id, row.id))
    return ended


def describe(db: Session, kind: str, org_id: int, gateway_id: int, now: datetime | None = None) -> dict | None:
    """Mandantengetrennter Diagnose-Snapshot, einschliesslich abgelaufener Presence."""
    model, cid, wid, connected, expiry, _hb = fields(kind)
    query = db.query(model).filter(model.org_id == org_id, model.id == gateway_id)
    if kind != SMS:
        query = query.filter(model.kind == kind)
    row = query.first()
    if row is None:
        return None
    return {
        "kind": kind,
        "org_id": org_id,
        "gateway_id": gateway_id,
        "connection_id": getattr(row, cid),
        "worker_id": getattr(row, wid),
        "connected_at": getattr(row, connected),
        "expires_at": getattr(row, expiry),
        "status": "online" if online(row, now) else "offline",
    }
