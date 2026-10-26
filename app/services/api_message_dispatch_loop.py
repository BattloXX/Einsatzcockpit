"""Persistenter, Multi-Worker-sicherer Versand der Nachrichten-API."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.api_message import ApiMessage, ApiMessageRecipient
from app.models.sms import SmsLog, SmsLogRecipient
from app.services import mail_service
from app.services.sms_dispatch_service import SmsSendResult, _send_bulk_with_progress
from app.services.sms_service import resolve_sms_config

logger = logging.getLogger("einsatzleiter.api_message")
INTERVAL_SECONDS = 5
BATCH_SIZE = 20


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _finish(db, message: ApiMessage) -> None:
    counts = dict(
        db.query(ApiMessageRecipient.status, func.count(ApiMessageRecipient.id))
        .filter(ApiMessageRecipient.message_id == message.id)
        .group_by(ApiMessageRecipient.status)
        .all()
    )
    message.success_count = counts.get("sent", 0)
    message.failed_count = counts.get("failed", 0) + counts.get("uncertain", 0)
    pending = counts.get("queued", 0) + counts.get("sending", 0)
    if pending:
        message.status = "queued"
        return
    message.completed_at = _now()
    if counts.get("uncertain", 0):
        message.status = "uncertain"
    elif message.failed_count == 0:
        message.status = "sent"
    elif message.success_count:
        message.status = "partial"
    else:
        message.status = "failed"
    if message.sms_log_id:
        log = db.query(SmsLog).filter(SmsLog.id == message.sms_log_id, SmsLog.org_id == message.org_id).first()
        if log:
            log.success_count = message.success_count
            log.completed_at = message.completed_at


def queue_sms(db, message: ApiMessage, recipients: list[ApiMessageRecipient]) -> None:
    """Persistiert SMS und API-Zuordnung vor jeder Freigabe an einen Worker."""
    assert message.org_id is not None
    from app.services import sms_outbox

    log = db.query(SmsLog).filter(SmsLog.org_id == message.org_id, SmsLog.id == message.sms_log_id).one()
    sms_outbox.queue_log(
        db, log, {r.ziel: (r.name, r.member_id) for r in recipients}, resolve_sms_config(message.org_id, db)
    )
    db.flush()
    rows = {r.phone_number: r for r in db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id == log.id)}
    for recipient in recipients:
        recipient.dispatch_job_id = rows[recipient.ziel].dispatch_job_id


async def _dispatch_sms(db, message: ApiMessage, recipients: list[ApiMessageRecipient]) -> None:
    assert message.org_id is not None
    org_id = message.org_id
    ctx = resolve_sms_config(org_id, db)
    by_phone = {recipient.ziel: recipient for recipient in recipients}
    from app.models.sms import SmsDispatchJob
    from app.services import sms_outbox

    jobs = []
    for recipient in recipients:
        job = (
            db.query(SmsDispatchJob)
            .filter(SmsDispatchJob.org_id == org_id, SmsDispatchJob.id == recipient.dispatch_job_id)
            .one()
        )
        row = (
            db.query(SmsLogRecipient)
            .filter(SmsLogRecipient.sms_log_id == message.sms_log_id, SmsLogRecipient.dispatch_job_id == job.id)
            .one()
        )
        jobs.append((recipient.ziel, job.text, job.job_key, row.id))

    async def record(result: SmsSendResult) -> None:
        if result.job_id:
            await asyncio.to_thread(sms_outbox.project, org_id, result.job_id)
            db.expire_all()
            return
        recipient = by_phone[result.phone_number]
        recipient.status = "sent" if result.success else "failed"
        recipient.provider = result.provider
        recipient.sent_at = result.sent_at if result.success else None
        recipient.error_message = None if result.success else "SMS-Versand fehlgeschlagen"
        row = (
            db.query(SmsLogRecipient)
            .filter(SmsLogRecipient.sms_log_id == message.sms_log_id, SmsLogRecipient.phone_number == recipient.ziel)
            .one()
        )
        row.success, row.sent_at, row.provider = result.success, result.sent_at, result.provider
        row.gateway_label = result.gateway_label
        db.commit()

    # Bewusst kein Retry: Bei einem Timeout ist ein Doppelversand nicht erkennbar.
    await _send_bulk_with_progress(
        org_id,
        jobs,
        ctx,
        record,
    )
    if message.sms_log_id:
        log = db.query(SmsLog).filter(SmsLog.id == message.sms_log_id).first()
        if log:
            log.provider = ",".join(sorted(ctx.providers_used)) or None


async def _dispatch_mail(db, message: ApiMessage, recipients: list[ApiMessageRecipient]) -> None:
    for recipient in recipients:
        recipient.status = "sending"
        recipient.attempt_count += 1
        db.commit()
        try:
            msg = mail_service._build_message(
                to=recipient.ziel,
                subject=message.betreff or "",
                body_txt=message.body_text or "",
                body_html=message.body_html,
            )
            await mail_service.deliver(db, message.org_id, msg)
            recipient.status = "sent"
            recipient.provider = "mail"
            recipient.sent_at = _now()
            recipient.error_message = None
        except Exception as exc:
            recipient.error_message = str(exc)[:2000]
            if recipient.attempt_count >= recipient.max_attempts:
                recipient.status = "failed"
            else:
                recipient.status = "queued"
                recipient.next_attempt_at = _now() + timedelta(minutes=min(2**recipient.attempt_count, 60))
        db.commit()


async def send_message_now(db, message: ApiMessage) -> None:
    """Sendet einen bereits angelegten SMS-Auftrag sofort und finalisiert ihn."""
    recipients = (
        db.query(ApiMessageRecipient)
        .filter(
            ApiMessageRecipient.message_id == message.id,
            ApiMessageRecipient.status == "queued",
        )
        .all()
    )
    message.status = "sending"
    message.started_at = message.started_at or _now()
    for recipient in recipients:
        recipient.status = "sending"
        recipient.attempt_count += 1
    queue_sms(db, message, recipients)
    db.commit()
    await _dispatch_sms(db, message, recipients)
    _finish(db, message)
    db.commit()


async def dispatch_once(db=None, *, batch_size: int = BATCH_SIZE) -> int:
    owns = db is None
    if owns:
        db = SessionLocal()
        set_tenant_context(db, None)
    processed = 0
    try:
        for _ in range(batch_size):
            now = _now()
            message = (
                db.query(ApiMessage)
                .filter(
                    ApiMessage.status == "queued",
                    ApiMessage.recipients.any(
                        (ApiMessageRecipient.status == "queued")
                        & or_(
                            ApiMessageRecipient.next_attempt_at.is_(None),
                            ApiMessageRecipient.next_attempt_at <= now,
                        )
                    ),
                )
                .execution_options(include_all_tenants=True)
                .order_by(ApiMessage.id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if message is None:
                break
            processed += 1
            message.status = "sending"
            message.started_at = message.started_at or now
            recipients = (
                db.query(ApiMessageRecipient)
                .filter(
                    ApiMessageRecipient.message_id == message.id,
                    ApiMessageRecipient.status == "queued",
                    or_(
                        ApiMessageRecipient.next_attempt_at.is_(None),
                        ApiMessageRecipient.next_attempt_at <= now,
                    ),
                )
                .execution_options(include_all_tenants=True)
                .all()
            )
            for recipient in recipients:
                recipient.status = "sending"
            if message.kanal == "sms":
                queue_sms(db, message, recipients)
            db.commit()
            if message.kanal == "sms":
                for recipient in recipients:
                    recipient.attempt_count += 1
                db.commit()
                await _dispatch_sms(db, message, recipients)
            else:
                await _dispatch_mail(db, message, recipients)
            _finish(db, message)
            db.commit()
        return processed
    finally:
        if owns:
            db.close()


async def api_message_dispatch_loop() -> None:
    while True:
        try:
            await dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Nachrichten-API-Dispatch fehlgeschlagen")
        await asyncio.sleep(INTERVAL_SECONDS)
