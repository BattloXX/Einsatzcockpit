"""Kompatible Windows-API auf der gemeinsamen Gateway-Presence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.gateway import GATEWAY_KIND_WINDOWS_AGENT, Gateway
from app.services import gateway_presence as presence
from app.services.gateway_presence import TTL_S, online, utc  # noqa: F401


def begin(
    db: Session, org_id: int, gateway_id: int, token_hash: str, connection_id: str, now: datetime | None = None
) -> bool:
    return presence.begin(db, GATEWAY_KIND_WINDOWS_AGENT, org_id, gateway_id, token_hash, connection_id, now=now)


def state(
    db: Session, org_id: int, gateway_id: int, token_hash: str, connection_id: str, now: datetime | None = None
) -> str:
    return presence.state(db, GATEWAY_KIND_WINDOWS_AGENT, org_id, gateway_id, token_hash, connection_id, now)


def touch(
    db: Session,
    gateway: Gateway,
    connection_id: str,
    token_hash: str,
    *,
    heartbeat: bool = False,
    now: datetime | None = None,
) -> bool:
    if gateway.org_id is None:
        return False
    return presence.touch(
        db, GATEWAY_KIND_WINDOWS_AGENT, gateway.org_id, gateway.id, token_hash, connection_id, now, heartbeat=heartbeat
    )


def end(db: Session, org_id: int, gateway_id: int, connection_id: str) -> bool:
    return presence.end(db, GATEWAY_KIND_WINDOWS_AGENT, org_id, gateway_id, connection_id)


def expire(db: Session, now: datetime | None = None) -> list[tuple[int, int]]:
    return presence.expire(db, now)
