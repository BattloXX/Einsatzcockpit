"""Passive Überwachung gealterter Druckaufträge ohne Retry oder Reprint."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import JOB_PRINTING, JOB_SENT, PrintJob
from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.print_watchdog")

WATCHDOG_INTERVAL_S = 300
STALE_AFTER_MINUTES = 10


def _warn_stale_print_jobs() -> None:
    from app.routers.ws import gateway_online

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cutoff = datetime.now(UTC) - timedelta(minutes=STALE_AFTER_MINUTES)
        jobs = (
            db.query(PrintJob)
            .filter(
                PrintJob.status.in_((JOB_SENT, JOB_PRINTING)),
                PrintJob.aktualisiert_am < cutoff,
            )
            .execution_options(include_all_tenants=True)
            .all()
        )
        now = datetime.now(UTC)
        for job in jobs:
            updated = job.aktualisiert_am
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age_minutes = int((now - updated).total_seconds() // 60)
            logger.warning(
                "Druckauftrag %s seit %d Min. in Status %s "
                "(org_id=%s, gateway_id=%s, incident_id=%s, gateway_online=%s)",
                job.id, age_minutes, job.status, job.org_id, job.gateway_id,
                job.incident_id, gateway_online(job.org_id),
            )
    finally:
        db.close()


async def print_job_watchdog_loop() -> None:
    logger.info("print_job_watchdog_loop gestartet")
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            with iteration_watch(logger, "print_job_watchdog_loop", WATCHDOG_INTERVAL_S):
                await asyncio.to_thread(_warn_stale_print_jobs)
        except asyncio.CancelledError:
            logger.info("print_job_watchdog_loop beendet")
            break
        except Exception:
            logger.exception("print_job_watchdog_loop: Iteration fehlgeschlagen")
