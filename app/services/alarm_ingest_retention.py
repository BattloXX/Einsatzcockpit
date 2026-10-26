"""Rohbytes taeglich um 03:45 Wien entfernen; Text und Journal bleiben."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest
from app.models.master import FireDept
from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.alarm")
_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def purge_old_alarm_raw_bytes(retention_days: int | None = None, chunk_size: int = 1000) -> int:
    days = settings.ALARM_RAW_BYTES_RETENTION_DAYS if retention_days is None else retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    total = 0
    with SessionLocal() as db:
        set_tenant_context(db, None)
        try:
            org_ids = [row[0] for row in db.query(FireDept.id).all()]
            for org_id in org_ids:
                while True:
                    ids = [row[0] for row in db.query(AlarmIngest.id).filter(
                        AlarmIngest.org_id == org_id, AlarmIngest.received_at < cutoff,
                        AlarmIngest.raw_bytes.is_not(None),
                    ).order_by(AlarmIngest.id).limit(chunk_size).all()]
                    if not ids:
                        break
                    rows = db.query(AlarmIngest).filter(
                        AlarmIngest.org_id == org_id, AlarmIngest.id.in_(ids),
                        AlarmIngest.received_at < cutoff, AlarmIngest.raw_bytes.is_not(None),
                    ).all()
                    for row in rows:
                        row.raw_bytes = None
                    db.commit()
                    total += len(rows)
        except Exception:
            db.rollback()
            logger.exception("Alarm-Rohbytes-Retention fehlgeschlagen")
            raise
    return total


def _seconds_until_next() -> float:
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=3, minute=45, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def alarm_ingest_retention_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_next())
        try:
            with iteration_watch(logger, "alarm_ingest_retention_loop", 86400):
                await asyncio.to_thread(purge_old_alarm_raw_bytes)
        except Exception:
            logger.exception("Fehler im Alarm-Rohbytes-Retention-Loop")
