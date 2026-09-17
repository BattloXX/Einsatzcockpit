"""Reminder für fällige Meldungen – WS-Broadcast + Web-Push-Fallback.

Läuft alle 30 s und sucht Meldungen, deren due_at erreicht wurde und die noch
nicht als popup_shown markiert sind. Fällige Meldungen werden per WebSocket an
alle Board-Clients des Einsatzes gesendet; zusätzlich erhält der Einsatzleiter
eine Web-Push-Benachrichtigung als Fallback für den Fall, dass er gerade nicht
auf dem Board ist.
"""
import asyncio
import logging
from datetime import UTC, date, datetime

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Message, Task
from app.services.broadcast import manager

logger = logging.getLogger("einsatzleiter.task_reminder")

# Datum des letzten Objekt-Revisions-Checks (einmal taeglich, im 30s-Loop)
_letzter_revision_check: date | None = None
_letzter_pflegeauftrag_check: date | None = None


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
        # DB + pywebpush (synchrones HTTP!) in den Threadpool (Audit B2)
        def _push() -> None:
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

        try:
            await asyncio.to_thread(_push)
        except Exception:
            logger.exception("task_reminder: Push-Fallback fehlgeschlagen")


async def _check_objekt_revisionen() -> None:
    """Einmal taeglich: faellige Objekt-Revisionen erinnern (WS an Org-Kanal)."""
    global _letzter_revision_check
    heute = datetime.now(UTC).date()
    if _letzter_revision_check == heute:
        return
    _letzter_revision_check = heute

    from app.services.broadcast import broadcast_org
    from app.services.objekt_service import pruefe_revision_erinnerungen

    def _pruefe() -> list[dict]:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            faellig = pruefe_revision_erinnerungen(db)
            if faellig:
                db.commit()
            return faellig
        finally:
            db.close()

    faellig = await asyncio.to_thread(_pruefe)

    for item in faellig:
        if not item.get("org_id"):
            continue
        try:
            await broadcast_org(item["org_id"], {
                "type": "objekt_revision_faellig",
                "objekt_id": item["objekt_id"],
                "nummer": item["nummer"],
                "name": item["name"],
                "revision_datum": item["revision_datum"],
            })
        except Exception:
            logger.exception("task_reminder: Objekt-Revisions-Broadcast fehlgeschlagen")


async def _check_pflegeauftrag_erinnerungen() -> None:
    """Einmal taeglich: offene Pflegeauftraege per Mail erinnern."""
    global _letzter_pflegeauftrag_check
    heute = datetime.now(UTC).date()
    if _letzter_pflegeauftrag_check == heute:
        return
    _letzter_pflegeauftrag_check = heute

    from app.models.objekt import (
        PFLEGEAUFTRAG_STATUS_EINGELADEN,
        PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG,
        PFLEGEAUFTRAG_STATUS_NACHARBEIT,
        ObjektPflegeauftrag,
    )
    from app.services.objekt_pflege_service import (
        _TERMINALE_STATUS,
        _ereignis,
        rotiere_pflegeauftrag_token,
    )

    def _pruefe() -> list[dict]:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            now = datetime.now(UTC)
            erinnerungen: list[dict] = []
            auftraege = (
                db.query(ObjektPflegeauftrag)
                .execution_options(include_all_tenants=True)
                .filter(ObjektPflegeauftrag.status.notin_(_TERMINALE_STATUS))
                .all()
            )
            for auftrag in auftraege:
                if auftrag.status not in {
                    PFLEGEAUFTRAG_STATUS_EINGELADEN,
                    PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG,
                    PFLEGEAUFTRAG_STATUS_NACHARBEIT,
                }:
                    continue
                gueltig_bis = auftrag.gueltig_bis
                if gueltig_bis.tzinfo is None:
                    gueltig_bis = gueltig_bis.replace(tzinfo=UTC)
                erstellt_am = auftrag.erstellt_am
                if erstellt_am.tzinfo is None:
                    erstellt_am = erstellt_am.replace(tzinfo=UTC)
                erinnerung_text = None
                if auftrag.erinnerung_1_am is None and (now - erstellt_am).days >= 30:
                    auftrag.erinnerung_1_am = now
                    erinnerung_text = "Erinnerung 1 (Tag 30)"
                elif auftrag.erinnerung_2_am is None and (gueltig_bis - now).days <= 7:
                    auftrag.erinnerung_2_am = now
                    erinnerung_text = "Erinnerung 2 (7 Tage vor Ablauf)"
                if erinnerung_text is None:
                    continue
                _ereignis(db, auftrag, "erinnerung", text=erinnerung_text)
                raw_token = rotiere_pflegeauftrag_token(db, auftrag)
                kontakt = auftrag.kontakt
                objekt = auftrag.objekt
                erinnerungen.append({
                    "auftrag_id": auftrag.id,
                    "org_id": auftrag.org_id,
                    "to": kontakt.email,
                    "kontakt_name": kontakt.anzeigename,
                    "objekt_name": objekt.name,
                    "raw_token": raw_token,
                    "gueltig_bis": auftrag.gueltig_bis,
                    "auftrag_text": auftrag.auftrag_text,
                })
            if erinnerungen:
                db.commit()
            return erinnerungen
        finally:
            db.close()

    erinnerungen = await asyncio.to_thread(_pruefe)
    for item in erinnerungen:
        if not item["to"]:
            continue
        try:
            from app.config import settings
            from app.core.timezones import format_local_datetime
            from app.models.master import FireDept
            from app.services.mail_service import send_pflegeauftrag_einladung

            # Eigene Session bewusst offen ueber den deliver()-Aufruf hinweg (statt wie
            # bei der reinen FireDept-Lookup vorher zu schliessen), da send_pflegeauftrag_
            # einladung() db= braucht, um org-eigene SMTP/O365/Resend-Konfiguration statt
            # nur des globalen Fallbacks zu beruecksichtigen (Muster: pflegeauftrag_einladen
            # in ui_objekt.py, das ebenfalls db=db an send_pflegeauftrag_einladung gibt).
            reminder_db = SessionLocal()
            set_tenant_context(reminder_db, None)
            try:
                org = await asyncio.to_thread(reminder_db.get, FireDept, item["org_id"])
                link = f"{settings.effective_public_base_url.rstrip('/')}/objektpflege/{item['raw_token']}"
                await send_pflegeauftrag_einladung(
                    to=item["to"],
                    kontakt_name=item["kontakt_name"],
                    objekt_name=item["objekt_name"],
                    link=link,
                    gueltig_bis_text=format_local_datetime(item["gueltig_bis"], org),
                    auftrag_text=item["auftrag_text"],
                    db=reminder_db,
                    org_id=item["org_id"],
                )
            finally:
                reminder_db.close()
        except Exception:
            logger.exception("task_reminder: Pflegeauftrag-Erinnerung fehlgeschlagen")


def _check_due_in_new_session() -> list[dict]:
    """DB-Arbeit für den Threadpool (Audit B2): Session lebt komplett im Worker-Thread."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return _check_due_messages_sync(db)
    finally:
        db.close()


async def task_reminder_loop() -> None:
    from app.services.loop_utils import iteration_watch
    logger.info("task_reminder_loop gestartet")
    while True:
        try:
            await asyncio.sleep(30)
            with iteration_watch(logger, "task_reminder_loop", 30):
                due = await asyncio.to_thread(_check_due_in_new_session)
                for item in due:
                    await _notify_due(item)
                await _check_objekt_revisionen()
                await _check_pflegeauftrag_erinnerungen()
        except asyncio.CancelledError:
            logger.info("task_reminder_loop beendet")
            break
        except Exception:
            logger.exception("task_reminder_loop: Iteration fehlgeschlagen")
