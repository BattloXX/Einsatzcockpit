"""Taegliche, organisationsbezogene Aufbewahrung des SMS-Versandprotokolls."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.org_sms import OrgSmsConfig
from app.models.sms import SmsDispatchJob, SmsLog, SmsLogRecipient
from app.services.citizen_verification_service import purge_citizen_verifications
from app.services.gateway_routing import purge_old_messages

logger = logging.getLogger("einsatzleiter.sms_log_retention")

DEFAULT_RETENTION_DAYS = 90
DEFAULT_RETENTION_MAX_COUNT = 500
_CHUNK_SIZE = 500
_VIENNA_TZ = ZoneInfo("Europe/Vienna")


def _redact_jobs(db, org_id: int, log_ids: list[int]) -> None:
    ids = [
        r[0]
        for r in db.query(SmsLogRecipient.dispatch_job_id).filter(
            SmsLogRecipient.sms_log_id.in_(log_ids), SmsLogRecipient.dispatch_job_id.isnot(None)
        )
    ]
    db.query(SmsDispatchJob).filter(
        SmsDispatchJob.org_id == org_id,
        SmsDispatchJob.id.in_(ids),
        SmsDispatchJob.status.in_(("sent", "failed", "uncertain")),
    ).update({"phone": "", "text": ""}, synchronize_session=False)
    # Auch SQLite ohne aktivierte FK-Cascades darf keine verwaisten Empfaenger
    # hinterlassen, die spaeter an einer wiederverwendeten Log-ID erscheinen.
    db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id.in_(log_ids)).delete(synchronize_session=False)


def _no_active_dispatch(db, org_id: int):
    return (
        ~db.query(SmsLogRecipient.id)
        .join(SmsDispatchJob, SmsDispatchJob.id == SmsLogRecipient.dispatch_job_id)
        .filter(
            SmsLogRecipient.sms_log_id == SmsLog.id,
            SmsDispatchJob.org_id == org_id,
            SmsDispatchJob.status.notin_(("sent", "failed", "uncertain")),
        )
        .exists()
    )


def purge_sms_logs_for_org(
    org_id: int,
    retention_days: int | None = None,
    retention_max_count: int | None = None,
    chunk_size: int = _CHUNK_SIZE,
    now: datetime | None = None,
) -> int:
    """Loescht Logs einer Org nach Alter und Anzahl in kleinen Transaktionen.

    Nullwerte lesen die Org-Konfiguration beziehungsweise die Standardwerte. Der
    Wert 0 deaktiviert die jeweilige Grenze.
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    total = 0
    try:
        total += purge_citizen_verifications(db, org_id)
        db.commit()
        config = db.query(OrgSmsConfig).filter(OrgSmsConfig.org_id == org_id).first()
        days = (
            retention_days
            if retention_days is not None
            else (config.log_retention_days if config else DEFAULT_RETENTION_DAYS)
        )
        max_count = (
            retention_max_count
            if retention_max_count is not None
            else (config.log_retention_max_count if config else DEFAULT_RETENTION_MAX_COUNT)
        )
        if days <= 0 and max_count <= 0:
            return total

        if days > 0:
            reference = now or datetime.now(UTC).replace(tzinfo=None)
            if reference.tzinfo is not None:
                reference = reference.astimezone(UTC).replace(tzinfo=None)
            cutoff = reference - timedelta(days=days)
            # Auch SMS ohne SmsLog (z.B. PINs) beachten die Aufbewahrungsdauer.
            # Ein schlanker Tombstone verhindert weiterhin automatischen Neuversand.
            db.query(SmsDispatchJob).filter(
                SmsDispatchJob.org_id == org_id,
                SmsDispatchJob.status.in_(("sent", "failed", "uncertain")),
                SmsDispatchJob.finished_at < cutoff,
            ).update({"phone": "", "text": ""}, synchronize_session=False)
            db.commit()
            while True:
                ids = [
                    row[0]
                    for row in (
                        db.query(SmsLog.id)
                        .filter(SmsLog.org_id == org_id, SmsLog.sent_at < cutoff, _no_active_dispatch(db, org_id))
                        .limit(chunk_size)
                        .all()
                    )
                ]
                if not ids:
                    break
                _redact_jobs(db, org_id, ids)
                deleted = (
                    db.query(SmsLog)
                    .filter(SmsLog.org_id == org_id, SmsLog.id.in_(ids))
                    .delete(synchronize_session=False)
                )
                db.commit()
                total += deleted

        if max_count > 0:
            while True:
                ids = [
                    row[0]
                    for row in (
                        db.query(SmsLog.id)
                        .filter(SmsLog.org_id == org_id, _no_active_dispatch(db, org_id))
                        .order_by(SmsLog.sent_at.desc(), SmsLog.id.desc())
                        .offset(max_count)
                        .limit(chunk_size)
                        .all()
                    )
                ]
                if not ids:
                    break
                _redact_jobs(db, org_id, ids)
                deleted = (
                    db.query(SmsLog)
                    .filter(SmsLog.org_id == org_id, SmsLog.id.in_(ids))
                    .delete(synchronize_session=False)
                )
                db.commit()
                total += deleted
        return total
    except Exception:
        db.rollback()
        logger.exception("SMS-Protokoll-Retention fehlgeschlagen (org_id=%d)", org_id)
        raise
    finally:
        db.close()


def purge_all_sms_logs() -> int:
    """Wendet die jeweilige Aufbewahrung auf alle Organisationen an."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_ids = [row[0] for row in db.query(FireDept.id).all()]
    finally:
        db.close()
    return sum(purge_sms_logs_for_org(org_id) for org_id in org_ids)


def _seconds_until_next() -> float:
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=3, minute=40, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def sms_log_retention_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_next())
        try:
            await asyncio.to_thread(purge_old_messages)
        except Exception:
            logger.exception("Fehler bei der Gateway-Nachrichten-Retention")
        try:
            deleted = await asyncio.to_thread(purge_all_sms_logs)
            if deleted:
                logger.info("SMS-Protokoll-Retention: %d Eintraege geloescht", deleted)
        except Exception:
            logger.exception("Fehler im SMS-Protokoll-Retention-Loop")
