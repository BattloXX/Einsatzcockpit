"""Daily retention for persistent AI request logs."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import AIRequestLog

logger = logging.getLogger("einsatzleiter.ai")
_CHUNK_SIZE = 5000
_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def purge_old_ai_requests(
    retention_days: int = settings.AI_LOG_RETENTION_DAYS,
    chunk_size: int = _CHUNK_SIZE,
) -> int:
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    total = 0
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        while True:
            ids = [row[0] for row in db.query(AIRequestLog.id).filter(
                AIRequestLog.created_at < cutoff
            ).limit(chunk_size).all()]
            if not ids:
                break
            db.query(AIRequestLog).filter(AIRequestLog.id.in_(ids)).delete(
                synchronize_session=False
            )
            db.commit()
            total += len(ids)
            if len(ids) < chunk_size:
                break
    except Exception:
        db.rollback()
        logger.exception("KI-Log-Retention fehlgeschlagen")
        raise
    finally:
        db.close()
    return total


def _seconds_until_next() -> float:
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=3, minute=30, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def ai_log_retention_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_next())
        try:
            await asyncio.to_thread(purge_old_ai_requests)
        except Exception:
            logger.exception("Fehler im KI-Log-Retention-Loop")
