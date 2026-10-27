"""Hostuebergreifende Tagesplanung und maximal eine Sammelmail pro Lauf."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.admin_mcp.code_tools import safe_title
from app.config import settings
from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.admin_security import utcnow
from app.models.diag_schedule import DiagNotification, DiagScheduleSlot
from app.models.diagnose import DiagFinding, DiagRun
from app.models.master import SystemSettings
from app.services.diagnose.registry import CHECKS
from app.services.diagnose.runner import einsatz_aktiv
from app.services.loop_utils import iteration_watch
from app.services.mail_service import _build_message, deliver, get_smtp_cfg, normalize_email_list

logger = logging.getLogger(__name__)
DEFAULTS = {"diag_schedule_enabled": "false", "diag_schedule_time": "03:00",
    "diag_schedule_scope": "schnell", "diag_schedule_mail": "",
    "diag_schedule_severity": "fail", "diag_schedule_repeat_hours": "24"}
RANK = {"info": 0, "warn": 1, "fail": 2, "kritisch": 3}


def configuration(db: Session) -> dict[str, str]:
    return {key: (row.value or default) if (row := db.get(SystemSettings, key)) else default
            for key, default in DEFAULTS.items()}


def validate(values: dict[str, str]) -> dict[str, str]:
    if values["diag_schedule_enabled"] not in ("true", "false"):
        raise ValueError("Ungueltiger Schalter")
    import re

    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", values["diag_schedule_time"]):
        raise ValueError("Uhrzeit als HH:MM angeben")
    if values["diag_schedule_scope"] not in ("schnell", "voll") or values["diag_schedule_severity"] not in (
            "warn", "fail", "kritisch"):
        raise ValueError("Ungueltiger Umfang oder Schweregrad")
    if not 1 <= int(values["diag_schedule_repeat_hours"]) <= 720:
        raise ValueError("Wiederholung: 1 bis 720 Stunden")
    mail = normalize_email_list(values["diag_schedule_mail"])
    if values["diag_schedule_mail"].strip() and not mail:
        raise ValueError("Ungueltige Mailadresse")
    values["diag_schedule_mail"] = mail
    return values


def schedule_once(db: Session, now: datetime | None = None) -> int | None:
    now = now or utcnow()
    cfg = configuration(db)
    if cfg["diag_schedule_enabled"] != "true":
        return None
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DEFAULT_TIMEZONE))
    hour, minute = map(int, cfg["diag_schedule_time"].split(":"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elapsed = (local.astimezone(UTC) - due.astimezone(UTC)).total_seconds()
    if elapsed < 0 or elapsed > 6 * 3600 + 300:
        return None
    slot = local.date().isoformat()
    if db.get(DiagScheduleSlot, slot) or db.scalar(select(DiagRun.id).where(DiagRun.active_slot == 1)):
        return None
    active = einsatz_aktiv(db)
    if active and elapsed < 6 * 3600:
        return None
    scope = cfg["diag_schedule_scope"]
    checks = [c.id for c in CHECKS.values() if not active and scope == "voll" or c.kosten == "leicht"]
    try:
        # UNIQUE Tages-Slot und active_slot werden in derselben Transaktion beansprucht.
        row = DiagRun(quelle="schedule", checks=checks, umfang={"typ": scope, "einsatzgedrosselt": active},
            active_slot=1, korrelations_id=str(uuid4()), fortschritt={"gesamt": len(checks), "erledigt": 0})
        db.add(row)
        db.flush()
        db.add(DiagScheduleSlot(slot=slot, run_id=row.id))
        write_audit(db, "diag.schedule.started", entity_id=row.id)
        db.commit()
        return row.id
    except (IntegrityError, OperationalError):
        db.rollback()
        return None


def notify_run(db: Session, run_id: int, now: datetime | None = None) -> None:
    """Claims werden vor Versand committed; auch Versandfehler erzeugen keinen Sturm."""
    now = now or utcnow()
    cfg = configuration(db)
    if cfg["diag_schedule_enabled"] != "true" or not cfg["diag_schedule_mail"]:
        return
    batch_key = f"run:{run_id}"
    if db.get(DiagNotification, batch_key):
        return
    rows = list(db.scalars(select(DiagFinding).where(DiagFinding.run_id == run_id,
        DiagFinding.status == "offen")))
    selected = []
    try:
        db.add(DiagNotification(finding_key=batch_key, last_sent_at=now, count=1))
        db.flush()
        for row in rows:
            if RANK[row.schweregrad] < RANK[cfg["diag_schedule_severity"]]:
                continue
            claim = db.get(DiagNotification, row.finding_key, with_for_update=True)
            if claim and claim.last_sent_at and now - claim.last_sent_at < timedelta(
                    hours=int(cfg["diag_schedule_repeat_hours"])):
                continue
            if claim is None:
                claim = DiagNotification(finding_key=row.finding_key, count=0)
                db.add(claim)
            claim.last_sent_at, claim.count = now, claim.count + 1
            # Nur Registry-Modul, fester Titelwortschatz und Zaehler, keine freien DB-Texte.
            check = CHECKS.get(row.check_id)
            selected.append(f"{check.modul if check else 'system'} | {row.schweregrad} | "
                            f"{safe_title(row.titel)} | Anzahl: {row.occurrences}")
        db.commit()
    except (IntegrityError, OperationalError):
        db.rollback()
        return
    if not selected:
        return
    text = "Systemdiagnose\n" + "\n".join(selected) + "\n" + (
        settings.effective_public_base_url.rstrip("/") + "/admin/systemdiagnose")
    try:
        smtp = get_smtp_cfg(db)
        message = _build_message(to=cfg["diag_schedule_mail"], subject="Systemdiagnose: kritische Befunde",
            body_txt=text, body_html="<pre>" + escape(text) + "</pre>", smtp_cfg=smtp)
        run_async(deliver, db, None, message, smtp)
        write_audit(db, "diag.notification.sent", entity_id=run_id, payload={"anzahl": len(selected)})
    except Exception:
        db.rollback()
        logger.warning("Systemdiagnose: Sammelmail fehlgeschlagen (Lauf %s)", run_id)
        write_audit(db, "diag.notification.failed", entity_id=run_id)
    db.commit()


def tick():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        schedule_once(db)


async def diag_schedule_loop():
    while True:
        try:
            with iteration_watch(logger, "diag_schedule_loop", 60):
                await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Systemdiagnose: Tagesplanung fehlgeschlagen")
        await asyncio.sleep(60)
