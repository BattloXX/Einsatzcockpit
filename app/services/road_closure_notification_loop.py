"""Persistent Teams sender for road closure notifications."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, update

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import decrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.road_closure import RoadClosure, RoadClosureNotification, RoadClosureTeamsConfig
from app.services import road_closure_notify_service
from app.services.road_closure_public_service import fingerprint, public_closure_dict
from app.services.road_closure_token_service import get_or_create_detail_token, public_url

logger = logging.getLogger("einsatzleiter.road_closure_notification_loop")
_LEASE = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _claim(db, row: RoadClosureNotification, now: datetime) -> bool:
    due = and_(RoadClosureNotification.status.in_(("pending", "retry")), RoadClosureNotification.next_attempt_at <= now)
    stale = and_(RoadClosureNotification.status == "sending", RoadClosureNotification.lease_until < now)
    result = db.execute(update(RoadClosureNotification).where(
        RoadClosureNotification.id == row.id, RoadClosureNotification.org_id == row.org_id, or_(due, stale)
    ).values(status="sending", lease_until=now + _LEASE,
             attempt_count=RoadClosureNotification.attempt_count + 1))
    db.commit()
    return bool(result.rowcount == 1)


async def _process(row_id: int, now: datetime) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(RoadClosureNotification).execution_options(include_all_tenants=True).filter(
            RoadClosureNotification.id == row_id).first()
        if not row or row.status != "sending":
            return
        closure = db.query(RoadClosure).execution_options(include_all_tenants=True).filter(
            RoadClosure.id == row.road_closure_id, RoadClosure.org_id == row.org_id).first()
        config = db.query(RoadClosureTeamsConfig).execution_options(include_all_tenants=True).filter(
            RoadClosureTeamsConfig.org_id == row.org_id).first()
        org = db.get(FireDept, row.org_id)
        if not closure:
            row.status, row.last_error, row.lease_until = "suppressed", "Sperre nicht gefunden", None
            db.commit()
            return
        if not config or not config.enabled or not config.webhook_url_enc or not org:
            row.status, row.last_error, row.lease_until = "suppressed", "Teams deaktiviert", None
            db.commit()
            return
        try:
            webhook = decrypt_secret(config.webhook_url_enc)
        except Exception:
            row.status, row.last_error, row.lease_until = "failed", "Webhook nicht lesbar", None
            db.commit()
            return
        data = public_closure_dict(closure, org)
        detail_url = map_url = None
        if closure.cancelled_at is None:
            # Aufgehobene/ersetzte Sperren sind öffentlich nicht mehr sichtbar – dann ohne Link und Bild melden.
            _token, raw = get_or_create_detail_token(db, closure)
            db.commit()
            detail_url = public_url(raw, "detail")
            map_url = detail_url + "/karte.png" if config.include_map and data.get("geometry") else None
        payload = road_closure_notify_service.build_road_closure_card(
            data, ereignis=row.ereignis, detail_url=detail_url,
            intern_url=settings.effective_public_base_url.rstrip("/") + f"/strassensperren/{closure.id}",
            map_url=map_url,
        )
        sent, retryable, reason = await road_closure_notify_service.send_payload(webhook, payload)
        row = db.get(RoadClosureNotification, row_id)
        if row is None:
            return
        row.lease_until = None
        if sent:
            row.status, row.sent_at, row.last_error = "sent", now, None
            row.payload_fingerprint = fingerprint(data)
            write_audit(db, "road_closure.teams_sent", org_id=row.org_id, entity_type="road_closure",
                        entity_id=closure.id, payload={"ereignis": row.ereignis, "notification_id": row.id})
        elif retryable and row.attempt_count < 5:
            delays = [30, 120, 600, 1800, 3600]
            row.status, row.last_error = "retry", reason
            row.next_attempt_at = now + timedelta(seconds=delays[min(row.attempt_count - 1, 4)])
        else:
            row.status, row.last_error = "failed", reason
            write_audit(
                db, "road_closure.teams_failed", org_id=row.org_id, entity_type="road_closure",
                entity_id=closure.id,
                payload={"ereignis": row.ereignis, "notification_id": row.id, "reason": reason},
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Road closure Teams notification failed", exc_info=True)
    finally:
        db.close()


async def process_due(now: datetime | None = None) -> int:
    now = now or _now()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(RoadClosureNotification).execution_options(include_all_tenants=True).filter(or_(
            and_(RoadClosureNotification.status.in_(("pending", "retry")),
                 RoadClosureNotification.next_attempt_at <= now),
            and_(RoadClosureNotification.status == "sending", RoadClosureNotification.lease_until < now),
        )).limit(20).all()
        ids = [row.id for row in rows if _claim(db, row, now)]
    finally:
        db.close()
    for row_id in ids:
        await _process(row_id, now)
    return len(ids)


async def road_closure_notification_loop() -> None:
    while True:
        try:
            await process_due()
        except Exception:
            logger.warning("Road closure notification loop iteration failed", exc_info=True)
        await asyncio.sleep(10)
