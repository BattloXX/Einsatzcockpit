"""DB-Presence mit TTL und Verbindungs-Fencing, unabhaengig von Redis/Worker.

Nur Hello/Heartbeat verlaengern die Gültigkeit. Inventar ist kein Lebenszeichen.
Alle Mutationen sind nach Org und Verbindungs-ID eingeschraenkte atomare UPDATEs.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models.gateway import GATEWAY_KIND_WINDOWS_AGENT, Gateway

logger = logging.getLogger("einsatzleiter.print_agent_presence")
TTL_S = 90


def utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def online(gateway: Gateway, now: datetime | None = None) -> bool:
    return bool(gateway.device_token_hash and gateway.agent_connection_id
                and gateway.agent_presence_expires_at
                and utc(gateway.agent_presence_expires_at) > utc(now))


def begin(db: Session, org_id: int, gateway_id: int, token_hash: str, connection_id: str,
          now: datetime | None = None) -> bool:
    moment = utc(now)
    changed = db.query(Gateway).filter(
        Gateway.org_id == org_id, Gateway.id == gateway_id,
        Gateway.kind == GATEWAY_KIND_WINDOWS_AGENT, Gateway.device_token_hash == token_hash,
    ).update({Gateway.agent_connection_id: connection_id, Gateway.agent_connected_at: moment,
              Gateway.agent_presence_expires_at: moment + timedelta(seconds=TTL_S),
              Gateway.last_seen_at: moment, Gateway.status: "online"}, synchronize_session=False)
    db.commit()
    if changed:
        logger.info("agent_connected org_id=%s gateway_id=%s connection_id=%s at=%s",
                    org_id, gateway_id, connection_id, moment.isoformat())
    return bool(changed)


def state(db: Session, org_id: int, gateway_id: int, token_hash: str, connection_id: str,
          now: datetime | None = None) -> str:
    gateway = db.query(Gateway).filter(Gateway.org_id == org_id, Gateway.id == gateway_id).populate_existing().first()
    if not gateway or not gateway.is_windows_agent or gateway.device_token_hash != token_hash:
        return "authentication_failed"
    if gateway.agent_connection_id != connection_id:
        return "superseded"
    return "connected" if online(gateway, now) else "heartbeat_overdue"


def touch(db: Session, gateway: Gateway, connection_id: str, token_hash: str, *, heartbeat: bool = False,
          now: datetime | None = None) -> bool:
    moment = utc(now)
    values: dict[Any, Any] = {Gateway.last_seen_at: moment, Gateway.status: "online",
              Gateway.agent_presence_expires_at: moment + timedelta(seconds=TTL_S)}
    if heartbeat:
        values[Gateway.agent_heartbeat_at] = moment
    changed = db.query(Gateway).filter(
        Gateway.org_id == gateway.org_id, Gateway.id == gateway.id,
        Gateway.agent_connection_id == connection_id, Gateway.device_token_hash == token_hash,
        Gateway.agent_presence_expires_at > moment,
    ).update(values, synchronize_session=False)
    db.commit()
    return bool(changed)


def end(db: Session, org_id: int, gateway_id: int, connection_id: str) -> bool:
    changed = db.query(Gateway).filter(
        Gateway.org_id == org_id, Gateway.id == gateway_id, Gateway.agent_connection_id == connection_id,
    ).update({Gateway.agent_connection_id: None, Gateway.agent_presence_expires_at: None,
              Gateway.status: case((Gateway.device_token_hash.is_(None), "unpaired"), else_="offline")},
             synchronize_session=False)
    db.commit()
    if changed:
        logger.info("agent_offline org_id=%s gateway_id=%s connection_id=%s at=%s",
                    org_id, gateway_id, connection_id, utc().isoformat())
    return bool(changed)


def expire(db: Session, now: datetime | None = None) -> list[tuple[int, int]]:
    moment = utc(now)
    stale = db.query(Gateway).filter(
        Gateway.kind == GATEWAY_KIND_WINDOWS_AGENT, Gateway.agent_connection_id.isnot(None),
        Gateway.agent_presence_expires_at <= moment,
    ).execution_options(include_all_tenants=True).all()
    ended = []
    for gateway in stale:
        if gateway.org_id is None:
            continue
        # TTL nochmals im UPDATE pruefen: paralleler Heartbeat darf nicht verloren gehen.
        changed = db.query(Gateway).filter(
            Gateway.org_id == gateway.org_id, Gateway.id == gateway.id,
            Gateway.agent_connection_id == gateway.agent_connection_id,
            Gateway.agent_presence_expires_at <= moment,
        ).update({Gateway.agent_connection_id: None, Gateway.agent_presence_expires_at: None,
                  Gateway.status: case((Gateway.device_token_hash.is_(None), "unpaired"), else_="offline")},
                 synchronize_session=False)
        if changed:
            ended.append((gateway.org_id, gateway.id))
            logger.warning("agent_heartbeat_overdue org_id=%s gateway_id=%s connection_id=%s at=%s",
                           gateway.org_id, gateway.id, gateway.agent_connection_id, moment.isoformat())
    db.commit()
    return ended
