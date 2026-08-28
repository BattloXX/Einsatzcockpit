"""Überwachung gealterter Druckaufträge mit terminaler Eskalation und Fallback."""
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
ESCALATE_AFTER_MINUTES = 30


def _pruefe_haengende_jobs() -> list[int]:
    from app.routers.ws import gateway_online
    from app.services.print_dispatcher import escalate_stale_job

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=STALE_AFTER_MINUTES)
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
        eskaliert: list[int] = []
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
            # Laut Gateway-Architekturplan endet der Retry nach 5 Versuchen/10 Min.;
            # die reale Container-Konfiguration ist aus diesem Repo nicht verifizierbar.
            if age_minutes >= ESCALATE_AFTER_MINUTES:
                grund = f"Keine Statusmeldung seit {age_minutes} Minuten"
                if escalate_stale_job(db, job.id, grund):
                    eskaliert.append(job.id)
        return eskaliert
    finally:
        db.close()


def _eskalations_context(job_ids: list[int]) -> list[tuple[int, int, int]]:
    """Liest (job_id, org_id, gateway_id) fuer uebernommene Eskalationen."""
    if not job_ids:
        return []
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        jobs = (
            db.query(PrintJob)
            .filter(PrintJob.id.in_(job_ids))
            .execution_options(include_all_tenants=True)
            .all()
        )
        return [(job.id, job.org_id, job.gateway_id) for job in jobs]
    finally:
        db.close()


async def _watchdog_iteration() -> None:
    from app.services.broadcast import broadcast_org
    from app.services.print_dispatcher import dispatch_fallback_for_failed_job

    job_ids = await asyncio.to_thread(_pruefe_haengende_jobs)
    contexts = await asyncio.to_thread(_eskalations_context, job_ids)
    for job_id, org_id, gateway_id in contexts:
        await broadcast_org(org_id, {
            "type": "print_job_status",
            "job_id": job_id,
            "status": "failed",
            "gateway_id": gateway_id,
        })
        await dispatch_fallback_for_failed_job(job_id)


async def print_job_watchdog_loop() -> None:
    logger.info("print_job_watchdog_loop gestartet")
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            with iteration_watch(logger, "print_job_watchdog_loop", WATCHDOG_INTERVAL_S):
                await _watchdog_iteration()
        except asyncio.CancelledError:
            logger.info("print_job_watchdog_loop beendet")
            break
        except Exception:
            logger.exception("print_job_watchdog_loop: Iteration fehlgeschlagen")
