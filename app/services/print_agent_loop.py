"""Sweep-Loop für Lease-Jobs der Windows-Druckagents (läuft in jedem Worker).

Die eigentliche Logik (bedingte UPDATEs, ein Gewinner je Übergang) steckt in
print_agent_service.sweep; hier nur Takt, Broadcasts, Agent-Hinweise und Ersatzdruck.
"""
from __future__ import annotations

import asyncio
import logging

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import JOB_FAILED, JOB_QUEUED, JOB_UNCERTAIN, PrintJob
from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.print_agent_loop")


def _sweep_und_empfaenger() -> tuple[dict, dict[int, tuple[int, list[int]]]]:
    """Sweep ausführen; für neu eingereihte Jobs die zu weckenden Agents ermitteln."""
    from app.services import print_agent_service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.services import print_agent_presence
        ergebnis = print_agent_service.sweep(db)
        ergebnis["offline_agents"] = print_agent_presence.expire(db)
        empfaenger: dict[int, tuple[int, list[int]]] = {}
        for job_id, org_id in ergebnis["requeued"]:
            job = db.get(PrintJob, job_id)
            if job is not None:
                empfaenger[job_id] = (
                    org_id, print_agent_service.route_gateway_ids(db, org_id, job.printer_id),
                )
        return ergebnis, empfaenger
    finally:
        db.close()


async def print_agent_sweep_iteration() -> dict:
    from app.routers.ws import notify_agents_job_available
    from app.services.broadcast import broadcast_org
    from app.services.print_dispatcher import dispatch_fallback_for_failed_job

    ergebnis, empfaenger = await asyncio.to_thread(_sweep_und_empfaenger)
    for org_id, gateway_id in ergebnis.get("offline_agents", []):
        await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": False})
    for job_id, (org_id, gateway_ids) in empfaenger.items():
        await broadcast_org(org_id, {"type": "print_job_status", "job_id": job_id, "status": JOB_QUEUED})
        await notify_agents_job_available(org_id, gateway_ids, [job_id])
    for job_id, org_id in ergebnis["uncertain"]:
        await broadcast_org(org_id, {"type": "print_job_status", "job_id": job_id, "status": JOB_UNCERTAIN})
    for job_id, org_id in ergebnis["failed"]:
        await broadcast_org(org_id, {"type": "print_job_status", "job_id": job_id, "status": JOB_FAILED})
        await dispatch_fallback_for_failed_job(job_id)
    return ergebnis


async def print_agent_sweep_loop() -> None:
    from app.services.print_agent_service import SWEEP_INTERVAL_S

    logger.info("print_agent_sweep_loop gestartet")
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            with iteration_watch(logger, "print_agent_sweep_loop", SWEEP_INTERVAL_S):
                await print_agent_sweep_iteration()
        except asyncio.CancelledError:
            logger.info("print_agent_sweep_loop beendet")
            break
        except Exception:
            logger.exception("print_agent_sweep_loop: Iteration fehlgeschlagen")
