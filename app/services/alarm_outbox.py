"""Crash-sichere, persistente Alarmierung fuer Einsaetze."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog

logger = logging.getLogger("einsatzleiter.alarm_outbox")
TERMINAL = {"sent", "suppressed", "failed"}
CHANNELS = ("sms", "push", "teams")
_LEASE = timedelta(minutes=5)
_running_incident_tasks: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _address(incident: Incident) -> str:
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


def enqueue_incident_alarm(
    db: Session, incident: Incident, *, org_id: int | None, source: str | None,
    triggered_by_user_id: int | None = None, base_url: str | None = None, push_url: str | None = None,
) -> None:
    """Fuegt die drei Versandauftraege hinzu; der Aufrufer bestimmt den Commit."""
    from app.services.incident_notify import NIE_ALARMIEREN_STICHWORTE

    existing = {
        channel for (channel,) in db.query(IncidentAlarmJob.channel).filter(
            IncidentAlarmJob.incident_id == incident.id
        ).all()
    }
    suppressed = (incident.alarm_type_code or "").upper() in NIE_ALARMIEREN_STICHWORTE
    now = _now()
    context = {
        "source": source,
        "triggered_by_user_id": triggered_by_user_id,
        "base_url": base_url or settings.effective_public_base_url,
        "push_url": push_url or f"/einsatz/{incident.id}",
    }
    for channel in CHANNELS:
        if channel not in existing:
            db.add(IncidentAlarmJob(
                org_id=org_id, incident_id=incident.id, channel=channel,
                status="suppressed" if suppressed else "pending", next_attempt_at=now,
                finished_at=now if suppressed else None, context=context,
            ))
    if suppressed and not existing:
        db.add(IncidentLog(
            incident_id=incident.id, author_name="System", level="info",
            text=f"{incident.alarm_type_code} – keine Alarmierung (SMS/Push/Teams)",
        ))


def _claim(db: Session, job: IncidentAlarmJob, now: datetime) -> bool:
    due = and_(IncidentAlarmJob.status.in_(("pending", "retry")), IncidentAlarmJob.next_attempt_at <= now)
    stale = and_(IncidentAlarmJob.status == "sending", IncidentAlarmJob.lease_until < now)
    result = db.execute(
        update(IncidentAlarmJob)
        .where(IncidentAlarmJob.id == job.id, IncidentAlarmJob.org_id == job.org_id, or_(due, stale))
        .values(status="sending", lease_until=now + _LEASE,
                attempt_count=IncidentAlarmJob.attempt_count + 1)
    )
    db.commit()
    return bool(getattr(result, "rowcount", 0) == 1)


async def _send_sms(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.sms_dispatch_service import dispatch_einsatzinfo
    org_id = job.org_id
    if not org_id:
        return "suppressed", "keine Organisation zugeordnet"
    if not darf_extern("sms", is_exercise=incident.is_exercise, org_id=org_id, db=db):
        logger.info("Einsatzinfo-SMS uebersprungen: Uebung unterdrueckt (Einsatz %s)", incident.id)
        return "suppressed", "Uebung unterdrueckt"
    address = _address(incident)
    info_link = (
        f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
        if incident.alarm_token else ""
    )
    result = await dispatch_einsatzinfo(
        org_id, incident.alarm_type_code, address, incident.address_city, incident.report_text,
        incident.reason, incident.is_exercise, job.context.get("triggered_by_user_id"), info_link,
        incident.lis_operation_number, incident.id,
    )
    if result is None:
        return "sent", None
    if result.sent or result.handed_off:
        if result.reason:
            return "sent", result.reason
        return "sent", f"gesendet an {result.recipient_count}"
    return "suppressed", result.reason


async def _send_push(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.incident_notify import _send_incident_push, _send_incident_wake_only
    org_id = job.org_id
    address = _address(incident)
    title = f"{'[ÜBUNG] ' if incident.is_exercise else ''}🚒 Einsatz: {incident.alarm_type_code}"
    body = address or incident.report_text or "Kein Ort angegeben"
    url = job.context.get("push_url") or f"/einsatz/{incident.id}"
    user_id = job.context.get("triggered_by_user_id")
    if not darf_extern("push", is_exercise=incident.is_exercise, org_id=org_id, db=db):
        if incident.is_exercise:
            await _send_incident_wake_only(incident.id, org_id, title, body, url, user_id)
        return "suppressed", "Uebung unterdrueckt"
    live_extra = None
    try:
        from app.services.einsatz_live_service import build_incident_live_payload
        from app.services.incident_live_notify import _live_extra
        live_extra = _live_extra(build_incident_live_payload(db, incident), alert=True, kind="einsatz_live")
    except Exception:
        logger.exception("Live-Push-Payload fehlgeschlagen (Einsatz %s)", incident.id)
    await _send_incident_push(incident.id, org_id, title, body, url, live_extra, user_id)
    return "sent", None


async def _send_teams(db: Session, incident: Incident, job: IncidentAlarmJob) -> tuple[str, str | None]:
    from app.services.exercise_guard import darf_extern
    from app.services.teams_alarm_service import post_incident_card
    if not darf_extern("teams", is_exercise=incident.is_exercise, org_id=job.org_id, db=db):
        logger.info("Teams-Alarmierung uebersprungen: Uebung unterdrueckt (Einsatz %s)", incident.id)
        return "suppressed", "Uebung unterdrueckt"
    base_url = job.context.get("base_url")
    if not base_url:
        return "suppressed", "keine Base-URL verfuegbar"
    result = await post_incident_card(db, incident, base_url=base_url)
    if result is None or result.sent:
        return "sent", None
    if result.retryable:
        raise RuntimeError(result.reason)
    if result.reason and result.reason.startswith("HTTP "):
        return "failed", result.reason
    return "suppressed", result.reason


def _retry_at(channel: str, attempt: int, now: datetime) -> datetime | None:
    delays = {"teams": (5, 20, 60, 300, 900), "push": (10, 60), "sms": (10, 60)}[channel]
    return now + timedelta(seconds=delays[attempt - 1]) if attempt <= len(delays) else None


async def _run_claimed(job_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.get(IncidentAlarmJob, job_id)
        if job is None:
            return
        incident = db.get(Incident, job.incident_id)
        if incident is None:
            return
        try:
            sender = {"sms": _send_sms, "push": _send_push, "teams": _send_teams}[job.channel]
            outcome, reason = await sender(db, incident, job)
        except Exception as exc:
            logger.exception("Alarmkanal fehlgeschlagen (job=%s, Einsatz %s)", job.id, incident.id)
            now = _now()
            retry_at = _retry_at(job.channel, job.attempt_count, now)
            job.status = "retry" if retry_at else "failed"
            job.next_attempt_at = retry_at or now
            job.finished_at = now if retry_at is None else None
            job.last_error = str(exc)[:2000]
        else:
            job.status = outcome
            job.last_error = reason
            job.finished_at = _now()
        job.lease_until = None
        if job.channel == "sms":
            sms_text = "Alarmierung SMS: gesendet"
            if job.status == "sent" and job.last_error:
                sms_text += f" ({job.last_error})"
            db.add(IncidentLog(
                incident_id=incident.id, author_name="System",
                level="info" if job.status == "sent" else "warning",
                text=sms_text if job.status == "sent"
                else f"Alarmierung SMS uebersprungen: {job.last_error}",
            ))
        if job.channel == "teams":
            db.add(IncidentLog(
                incident_id=incident.id, author_name="System",
                level="info" if job.status == "sent" else "warning",
                text="Alarmierung Teams: gesendet" if job.status == "sent"
                else f"Alarmierung Teams uebersprungen: {job.last_error}",
            ))
        db.commit()
        _finish_if_done(db, incident, job.org_id, job.context.get("triggered_by_user_id"))
    except Exception:
        db.rollback()
        logger.exception("Alarm-Outbox fehlgeschlagen (job=%s)", job_id)
    finally:
        db.close()


def _finish_if_done(db: Session, incident: Incident, org_id: int | None, user_id: int | None) -> None:
    jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident.id).all()
    if len(jobs) != 3 or any(job.status not in TERMINAL for job in jobs):
        return
    from app.core.audit import write_audit
    from app.models.user import AuditLog
    finished = db.query(AuditLog.id).filter(
        AuditLog.action == "incident.alarm_finished", AuditLog.incident_id == incident.id,
    ).first()
    if finished:
        return
    payload = {job.channel: {"status": job.status, "reason": job.last_error} for job in jobs}
    write_audit(db, "incident.alarm_finished", org_id=org_id, user_id=user_id, incident_id=incident.id, payload=payload)
    db.add(IncidentLog(incident_id=incident.id, author_name="System", level="info", text="Alarmierung abgeschlossen"))
    db.commit()
    logger.info("Alarmierung abgeschlossen (Einsatz %s): %s", incident.id, payload)


async def process_incident_alarm(incident_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = _now()
        jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident_id).all()
        claimed = [job.id for job in jobs if _claim(db, job, now)]
    finally:
        db.close()
    await asyncio.gather(*(_run_claimed(job_id) for job_id in claimed))


async def run_alarm_outbox_once() -> list[asyncio.Task]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = _now()
        ids = [row[0] for row in db.query(IncidentAlarmJob.incident_id).filter(or_(
            and_(IncidentAlarmJob.status.in_(("pending", "retry")), IncidentAlarmJob.next_attempt_at <= now),
            and_(IncidentAlarmJob.status == "sending", IncidentAlarmJob.lease_until < now),
        )).distinct().all()]
    finally:
        db.close()
    running_ids = {task.get_name() for task in _running_incident_tasks}
    started = []
    for incident_id in ids:
        if str(incident_id) in running_ids:
            continue
        task = asyncio.create_task(process_incident_alarm(incident_id), name=str(incident_id))
        _running_incident_tasks.add(task)
        task.add_done_callback(_running_incident_tasks.discard)
        started.append(task)
    return started


async def alarm_outbox_loop() -> None:
    while True:
        try:
            await run_alarm_outbox_once()
        except Exception:
            logger.exception("Alarm-Outbox-Iteration fehlgeschlagen")
        await asyncio.sleep(5)
