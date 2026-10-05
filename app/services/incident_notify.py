"""Persistent incident notification entry point and push helpers."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.incident import Incident

logger = logging.getLogger("einsatzleiter.incident_notify")
NIE_ALARMIEREN_STICHWORTE = frozenset({"F30"})


def _combined_address(incident: Incident) -> str:
    return (
        f"{incident.address_street or ''} {incident.address_no or ''}, "
        f"{incident.address_city or ''}"
    ).strip(", ").strip()


def incident_alarm_started(db: Session, incident_id: int) -> bool:
    from app.models.incident import IncidentAlarmJob
    from app.models.user import AuditLog

    return (
        db.query(IncidentAlarmJob.id).filter(IncidentAlarmJob.incident_id == incident_id).first() is not None
        or db.query(AuditLog.id).filter(
            AuditLog.action == "incident.alarm_started", AuditLog.incident_id == incident_id,
        ).first() is not None
    )


def incident_needs_alarm_backfill(db: Session, incident: Incident) -> bool:
    if (incident.alarm_type_code or "").upper() in NIE_ALARMIEREN_STICHWORTE:
        return False
    started_at = incident.started_at
    if started_at is None or incident.status != "active":
        return False
    if started_at.tzinfo is not None:
        started_at = started_at.astimezone(UTC).replace(tzinfo=None)
    is_recent = datetime.now(UTC).replace(tzinfo=None) - started_at <= timedelta(minutes=15)
    return is_recent and not incident_alarm_started(db, incident.id)


async def _send_incident_push(
    incident_id: int, org_id: int | None, title: str, body: str, url: str,
    extra: dict | None, triggered_by_user_id: int | None,
) -> None:
    from app.core.audit import write_audit
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import FcmToken, PushSubscription, User
    from app.services.push_service import notify_all, notify_org

    def _run() -> None:
        push_db = SessionLocal()
        set_tenant_context(push_db, None)
        try:
            if org_id is not None:
                user_ids = push_db.query(User.id).filter(User.org_id == org_id)
                recipient_count = (
                    push_db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).count()
                    + push_db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).count()
                )
                sent_count = notify_org(
                    push_db, org_id, title, body, url, source="einsatz_alarm",
                    channel_id="einsatz_alarm", extra=extra,
                )
            else:
                recipient_count = push_db.query(PushSubscription).count() + push_db.query(FcmToken).count()
                sent_count = notify_all(
                    push_db, title, body, url, source="einsatz_alarm",
                    channel_id="einsatz_alarm", extra=extra,
                )
            write_audit(
                push_db, "push.einsatz_alarm_sent", org_id=org_id, user_id=triggered_by_user_id,
                incident_id=incident_id,
                payload={"sent_count": sent_count, "recipient_count": recipient_count, "channel_id": "einsatz_alarm"},
            )
            push_db.commit()
        except Exception:
            push_db.rollback()
            logger.exception("Push-Benachrichtigung fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            push_db.close()

    await asyncio.to_thread(_run)


async def _send_incident_wake_only(
    incident_id: int, org_id: int | None, title: str, body: str, url: str,
    triggered_by_user_id: int | None,
) -> None:
    from app.core.audit import write_audit
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import FcmToken, User
    from app.services.push_service import notify_org_fcm_wake_only

    if org_id is None:
        return

    def _run() -> None:
        push_db = SessionLocal()
        set_tenant_context(push_db, None)
        try:
            user_ids = push_db.query(User.id).filter(User.org_id == org_id)
            recipient_count = push_db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).count()
            sent_count = notify_org_fcm_wake_only(
                push_db, org_id, title, body, url, source="einsatz_wake", channel_id="einsatz_alarm",
            )
            write_audit(
                push_db, "push.einsatz_wake_sent", org_id=org_id, user_id=triggered_by_user_id,
                incident_id=incident_id,
                payload={"sent_count": sent_count, "recipient_count": recipient_count, "channel_id": "einsatz_alarm"},
            )
            push_db.commit()
        except Exception:
            push_db.rollback()
            logger.exception("Stiller FCM-Wake fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            push_db.close()

    await asyncio.to_thread(_run)


async def notify_incident_created(
    db: Session, incident: Incident, *, org_id: int | None, triggered_by_user_id: int | None = None,
    push_url: str | None = None, base_url: str | None = None, background_tasks=None,
    source: str | None = None,
) -> None:
    """Persist jobs first, then run the immediate best-effort processing pass."""
    from app.core.audit import write_audit
    from app.services.alarm_outbox import enqueue_incident_alarm, process_incident_alarm

    enqueue_incident_alarm(
        db, incident, org_id=org_id, source=source, triggered_by_user_id=triggered_by_user_id,
        base_url=base_url, push_url=push_url,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Alarm-Outbox konnte nicht gespeichert werden (Einsatz %s)", incident.id)
        raise
    try:
        write_audit(
            db, "incident.alarm_started", org_id=org_id, user_id=triggered_by_user_id,
            incident_id=incident.id, payload={"source": source},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Alarmierungsstart konnte nicht protokolliert werden (Einsatz %s)", incident.id)
    try:
        from app.services.einsatz_live_service import build_incident_live_payload

        live_payload = build_incident_live_payload(db, incident)
        incident.live_push_phase = live_payload["phase_index"]
        incident.live_push_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Initialer Live-Status fehlgeschlagen (Einsatz %s)", incident.id)
    if background_tasks is not None:
        background_tasks.add_task(process_incident_alarm, incident.id)
    else:
        await process_incident_alarm(incident.id)
