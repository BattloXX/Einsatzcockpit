"""Reminder für fällige Meldungen – WS-Broadcast + Web-Push-Fallback.

Läuft alle 30 s und sucht Meldungen, deren due_at erreicht wurde und die noch
nicht als popup_shown markiert sind. Fällige Meldungen werden per WebSocket an
alle Board-Clients des Einsatzes gesendet; zusätzlich erhält der Einsatzleiter
eine Web-Push-Benachrichtigung als Fallback für den Fall, dass er gerade nicht
auf dem Board ist.
"""
import asyncio
import logging
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Message, Task
from app.services.broadcast import manager

logger = logging.getLogger("einsatzleiter.task_reminder")


def _check_due_messages_sync(db) -> list[dict]:
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    candidates = (
        db.query(Message)
        .filter(
            Message.due_at.isnot(None),
            Message.popup_shown == False,  # noqa: E712
            Message.is_done == False,  # noqa: E712
            Message.is_cancelled == False,  # noqa: E712
        )
        .all()
    )
    due: list[dict] = []
    for msg in candidates:
        due_at = msg.due_at
        if due_at is None:
            continue
        due_naive = due_at.replace(tzinfo=None) if due_at.tzinfo else due_at
        if due_naive > now_naive:
            continue
        msg.popup_shown = True
        incident = db.get(Incident, msg.incident_id)
        if incident and incident.status == "active":
            due.append({
                "incident_id": msg.incident_id,
                "message_id": msg.id,
                "title": msg.title,
                "leader_user_id": incident.incident_leader_user_id,
            })
    # Check tasks with due_at
    task_candidates = (
        db.query(Task)
        .filter(
            Task.due_at.isnot(None),
            Task.popup_shown == False,  # noqa: E712
            Task.is_done == False,  # noqa: E712
            Task.is_cancelled == False,  # noqa: E712
        )
        .all()
    )
    for task in task_candidates:
        due_at = task.due_at
        if due_at is None:
            continue
        due_naive = due_at.replace(tzinfo=None) if due_at.tzinfo else due_at
        if due_naive > now_naive:
            continue
        task.popup_shown = True
        incident = db.get(Incident, task.incident_id)
        if incident and incident.status == "active":
            due.append({
                "incident_id": task.incident_id,
                "message_id": task.id,
                "title": f"Auftrag: {task.title}",
                "leader_user_id": incident.incident_leader_user_id,
                "kind": "task",
            })

    if due:
        db.commit()
    return due


async def _notify_due(item: dict) -> None:
    incident_id = item["incident_id"]
    message_id = item["message_id"]
    title = item["title"]
    leader_user_id = item.get("leader_user_id")

    try:
        await manager.broadcast(incident_id, {
            "type": "message_due",
            "message_id": message_id,
            "title": title,
        })
    except Exception:
        logger.exception("task_reminder: WS-Broadcast für Einsatz %s fehlgeschlagen", incident_id)

    if leader_user_id:
        try:
            from app.services.push_service import notify_user
            db2 = SessionLocal()
            set_tenant_context(db2, None)
            try:
                notify_user(
                    db2, leader_user_id,
                    "Meldung fällig",
                    title,
                    url=f"/einsatz/{incident_id}",
                    source="task_reminder",
                )
            finally:
                db2.close()
        except Exception:
            logger.exception("task_reminder: Push-Fallback fehlgeschlagen")


async def task_reminder_loop() -> None:
    logger.info("task_reminder_loop gestartet")
    while True:
        try:
            await asyncio.sleep(30)
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                due = _check_due_messages_sync(db)
            finally:
                db.close()
            for item in due:
                await _notify_due(item)
        except asyncio.CancelledError:
            logger.info("task_reminder_loop beendet")
            break
        except Exception:
            logger.exception("task_reminder_loop: Iteration fehlgeschlagen")
