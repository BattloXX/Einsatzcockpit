"""Ressourcenbegrenzte Leader-Jobs, ausschliesslich Diagnosepersistenz schreiben."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from datetime import timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.admin_mcp.masking import mask_text
from app.db import SessionLocal
from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.services.diagnose.helpers import table
from app.services.diagnose.registry import CHECKS, MAX_IDS, CheckResult, Finding, execute_isolated
from app.services.loop_utils import iteration_watch

logger = logging.getLogger(__name__)
RUN_TIMEOUT = 120.0
_next_retention = 0.0


def recover_stale(db: Session) -> int:
    result = db.execute(update(DiagRun).where(DiagRun.status == "running",
        DiagRun.fortschritt_am < utcnow() - timedelta(minutes=10)).values(
        status="failed", active_slot=None, finished_at=utcnow(),
        fehlertext="Fortschritt seit mehr als 10 Minuten ausgeblieben"))
    db.commit()
    return cast(CursorResult, result).rowcount


def claim_run(db: Session) -> int | None:
    ident = db.scalar(select(DiagRun.id).where(DiagRun.status == "queued").order_by(DiagRun.id).limit(1))
    if ident is None:
        return None
    result = db.execute(update(DiagRun).where(DiagRun.id == ident, DiagRun.status == "queued").values(
        status="running", started_at=utcnow(), fortschritt_am=utcnow()))
    db.commit()
    return ident if cast(CursorResult, result).rowcount == 1 else None


def einsatz_aktiv(db: Session) -> bool:
    for name in ("incident", "major_incident"):
        t = table(name)
        if db.scalar(select(t.c.id).where(t.c.status == "active").limit(1)
                     .execution_options(include_all_tenants=True)) is not None:
            return True
    # Lagefuehrung haengt an major_incident, besitzt keinen eigenen Aktivstatus.
    return False


def _numbers(value):
    """Evidenz erhaelt nur Zaehler/Flags; selbst maskierter Freitext kann Namen enthalten."""
    if isinstance(value, dict):
        return {mask_text(str(k))[:80]: _numbers(v) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_numbers(v) for v in value[:MAX_IDS]]
    return value if type(value) in (int, float, bool) or value is None else "[MASKIERT]"


def persist_result(db: Session, run_id: int, check_id: str, result: CheckResult, *, success: bool):
    run = db.get(DiagRun, run_id)
    if run is None or run.status != "running":
        return
    check = CHECKS[check_id]
    keys = set()
    for finding in result.findings[:200]:
        key = finding.key(check_id)
        if key in keys:
            continue
        keys.add(key)
        row = db.scalar(select(DiagFinding).where(DiagFinding.finding_key == key))
        if row is None:
            row = DiagFinding(run_id=run_id, finding_key=key, check_id=check_id, modul=check.modul,
                              occurrences=0, status="offen", first_seen_at=utcnow())
            db.add(row)
        row.run_id = run_id
        row.last_seen_at = utcnow()
        row.occurrences += 1
        if row.status == "behoben":
            row.status = "offen"
            row.behoben_am = None
        row.schweregrad = finding.schweregrad
        row.titel = mask_text(finding.titel)[:200]
        row.evidenz = _numbers(finding.evidenz)
        row.betroffene_ids = {k: v[:MAX_IDS] for k, v in list(finding.betroffene_ids.items())[:10]}
        row.ursache_hypothese = mask_text(finding.ursache_hypothese)[:500]
        row.empfehlung = mask_text(finding.empfehlung)[:500]
    db.flush()
    complete = success and result.vollstaendig and len(result.findings) <= 200
    if complete:
        for previous in db.scalars(select(DiagFinding).where(DiagFinding.check_id == check_id,
            DiagFinding.status == "offen", DiagFinding.finding_key.not_in(keys))):
            from app.models.diag_schedule import DiagNotification

            notification = db.get(DiagNotification, previous.finding_key)
            if notification:
                notification.last_sent_at = None
            previous.status = "behoben"
            previous.behoben_am = utcnow()
    progress = dict(run.fortschritt)
    checks = dict(progress.get("checks", {}))
    checks[check_id] = {"status": "done" if complete else "unvollstaendig" if success else "check_fehlgeschlagen",
        "befunde": len(keys), "hinweis": result.hinweis}
    counts: Counter[str] = Counter(progress.get("schweregrade", {}))
    counts.update(f.schweregrad for f in result.findings[:200])
    progress.update(checks=checks, schweregrade=dict(counts), gesamt=len(run.checks),
                    erledigt=len(checks) + len(progress.get("uebersprungen", {})))
    run.fortschritt = progress
    run.fortschritt_am = utcnow()
    db.commit()


def retention(db: Session):
    cutoff = utcnow() - timedelta(days=30)
    # Begrenzte Batches; offene/unterdrueckte Befunde und deren letzter Lauf bleiben erhalten.
    ids = list(db.scalars(select(DiagFinding.id).where(DiagFinding.status == "behoben",
        DiagFinding.behoben_am < cutoff).limit(500)))
    if ids:
        db.query(DiagFinding).filter(DiagFinding.id.in_(ids)).delete(synchronize_session=False)
    ids = list(db.scalars(select(DiagRun.id).where(DiagRun.created_at < cutoff,
        DiagRun.status.in_(("done", "failed", "cancelled")), ~select(DiagFinding.id).where(
            DiagFinding.run_id == DiagRun.id).exists()).limit(500)))
    if ids:
        db.query(DiagRun).filter(DiagRun.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _prepare(factory):
    global _next_retention
    with factory() as db:
        recover_stale(db)
        if time.monotonic() >= _next_retention:
            retention(db)
            _next_retention = time.monotonic() + 3600
        ident = claim_run(db)
        if ident is None:
            return None
        row = db.get(DiagRun, ident)
        return ident, list(row.checks)


def _skip(factory, ident, check_id, reason):
    with factory() as db:
        row = db.get(DiagRun, ident)
        if row is not None and row.status == "running":
            progress = dict(row.fortschritt)
            skipped = dict(progress.get("uebersprungen", {}))
            skipped[check_id] = reason
            row.fortschritt = {**progress, "uebersprungen": skipped, "gesamt": len(row.checks),
                               "erledigt": len(progress.get("checks", {})) + len(skipped)}
            row.fortschritt_am = utcnow()
            db.commit()


def _finish(factory, ident, failure: str | None = None):
    with factory() as db:
        row = db.get(DiagRun, ident)
        if row is not None and row.status == "running":
            row.status = "failed" if failure else "done"
            row.fehlertext = failure
            row.finished_at = utcnow()
            row.active_slot = None
            db.commit()
            from app.services.diag_schedule import notify_run

            try:
                notify_run(db, ident)
            except Exception:
                db.rollback()
                logger.warning("Systemdiagnose: Benachrichtigung fehlgeschlagen (Lauf %s)", ident)


async def run_once(factory=SessionLocal):
    prepared = await asyncio.to_thread(_prepare, factory)
    if prepared is None:
        return
    ident, selected = prepared
    deadline = time.monotonic() + RUN_TIMEOUT
    failure = None
    try:
        for check_id in selected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "Gesamtzeitlimit erreicht"
                await asyncio.to_thread(_skip, factory, ident, check_id, "gesamtzeitlimit")
                continue
            check = CHECKS[check_id]

            def execute(check=check, check_id=check_id, remaining=remaining):
                with factory() as db:
                    active = einsatz_aktiv(db)
                    bind = db.get_bind()
                if active and check.kosten != "leicht":
                    _skip(factory, ident, check_id, "uebersprungen_wegen_einsatz")
                    return
                success = True
                try:
                    result = execute_isolated(bind, check, min(10.0, remaining))
                except Exception:
                    success = False
                    result = CheckResult([Finding("check_fehlgeschlagen", "check_fehlgeschlagen", "warn")], False)
                with factory() as db:
                    persist_result(db, ident, check_id, result, success=success)
            # Nach Abbruch keinen unbeobachteten Schreibthread mit dem naechsten Lauf ueberlappen.
            task = asyncio.create_task(asyncio.to_thread(execute))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            await asyncio.sleep(0.05)
        if time.monotonic() >= deadline:
            failure = "Gesamtzeitlimit erreicht"
    except BaseException:
        failure = "Diagnoselauf unterbrochen"
        raise
    finally:
        await asyncio.to_thread(_finish, factory, ident, failure)


async def diag_runner_loop():
    while True:
        with iteration_watch(logger, "diag_runner_loop", 130):
            await run_once()
        await asyncio.sleep(5)
