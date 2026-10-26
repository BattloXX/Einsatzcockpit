"""Ein dauerhafter Linux-Druckauftrag, ein atomarer Socket-Transport.

Erneute Zustellung nur mit DB-Beweis, dass der vorherige Transport niemals gesendet
wurde. Nach moeglicher CUPS-Uebergabe entscheidet ausschliesslich der Statusabgleich.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway, GatewayMessage, PrintJob
from app.services import gateway_presence as presence
from app.services import gateway_routing as routing
from app.services import ws_bus

logger = logging.getLogger("einsatzleiter.linux_print")
_task: asyncio.Task | None = None
TERMINAL = ("done", "failed", "canceled", "uncertain")


def database():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def snapshot(org, jid):
    with database() as db:
        job = db.query(PrintJob).filter(PrintJob.org_id == org, PrintJob.id == jid).first()
        if job is None:
            raise RuntimeError("Druckauftrag nicht gefunden")
        return {"job_id": jid, "status": job.status, "error": job.error}


def prepare(org, jid, payload=None):
    from app.services import print_agent_service
    from app.services.print_artifact_service import artifact_url, is_html_render

    with database() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            db.connection(execution_options={"isolation_level": "READ COMMITTED"})
        target = db.query(PrintJob.gateway_id).filter(PrintJob.org_id == org, PrintJob.id == jid).first()
        if target is None:
            return None
        gw = (
            db.query(Gateway)
            .filter(Gateway.org_id == org, Gateway.id == target[0], Gateway.kind == "linux")
            .with_for_update()
            .first()
        )
        job = db.query(PrintJob).filter(PrintJob.org_id == org, PrintJob.id == jid).with_for_update().one()
        if job.status != "queued" or job.delivery != "push" or gw is None:
            return None
        if print_agent_service.hat_agent_routen(db, job.printer_id):
            print_agent_service.enqueue_for_agents(db, job)
            return None
        if not presence.online(gw):
            return None
        assert gw.agent_connection_id and gw.agent_worker_id and gw.agent_presence_expires_at
        connection = presence.Connection(
            "linux", org, gw.id, gw.agent_connection_id, gw.agent_worker_id, gw.agent_presence_expires_at
        )
        if payload is None:
            payload = {
                "job_id": jid,
                "document_type": job.document_type,
                "printer_id": job.printer_id,
                "artifact_url": artifact_url(job),
                "options": job.options or {},
            }
            if is_html_render(job):
                payload["render_kind"] = "html"
        payload = dict(payload)
        if gw.print_protocol >= 2:
            job.lease_token = uuid4().hex
            payload.update(requires_intent=True, dispatch_token=job.lease_token, generation=job.lease_generation + 1)
        job.push_route_id = routing.enqueue_in(
            db, connection, {"type": "print_job", "id": str(jid), "payload": payload}, 30, str(jid)
        )
        job.status, job.phase = "sent", "prepared" if job.lease_token else "dispatched"
        job.attempts += 1
        job.lease_generation += 1
        job.lease_expires_at = presence.utc() + timedelta(minutes=10)
        db.commit()
        return job.push_route_id, connection


async def dispatch(org, jid, payload=None, *, wait_seconds=20):
    result = await asyncio.to_thread(prepare, org, jid, payload)
    if result:
        item, connection = result
        await routing.deliver(item)
        await ws_bus.publish(ws_bus.CH_GW, routing.envelope(connection, item))
        transport, _ = await asyncio.to_thread(routing._result, item, org)
        if transport == "failed":
            with database() as db:
                db.query(PrintJob).filter(
                    PrintJob.org_id == org,
                    PrintJob.id == jid,
                    PrintJob.push_route_id == item,
                    PrintJob.status.in_(("sent", "printing")),
                ).update(
                    {"status": "uncertain", "error": "Socket-Send mit unklarem Ausgang"}, synchronize_session=False
                )
                db.commit()
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        state = await asyncio.to_thread(snapshot, org, jid)
        if state["status"] != "sent" or asyncio.get_running_loop().time() >= deadline:
            if state["status"] == "failed":
                from app.services.print_dispatcher import dispatch_fallback_for_failed_job

                await dispatch_fallback_for_failed_job(jid)
            return state
        await asyncio.sleep(0.05)


def reconcile():
    now = presence.utc()
    with database() as db:
        jobs = (
            db.query(PrintJob.org_id, PrintJob.id)
            .join(Gateway, Gateway.id == PrintJob.gateway_id)
            .filter(
                Gateway.org_id == PrintJob.org_id,
                Gateway.kind == "linux",
                PrintJob.delivery == "push",
                PrintJob.status.in_(("queued", "sent", "printing")),
            )
            .execution_options(include_all_tenants=True)
            .all()
        )
    for org, jid in jobs:
        with database() as db:
            job = db.query(PrintJob).filter(PrintJob.org_id == org, PrintJob.id == jid).with_for_update().first()
            if job is None or job.delivery != "push" or job.status in TERMINAL:
                continue
            if job.status == "queued":
                if job.erstellt_am.replace(tzinfo=None) < now - timedelta(minutes=30):
                    job.status, job.error = "failed", "Kein Druckgateway innerhalb von 30 Minuten erreichbar"
            else:
                route = (
                    db.query(GatewayMessage)
                    .filter(GatewayMessage.org_id == org, GatewayMessage.id == job.push_route_id)
                    .first()
                    if job.push_route_id
                    else None
                )
                if route is not None and route.expires_at <= now and route.state in ("queued", "expired"):
                    changed = (
                        db.query(GatewayMessage)
                        .filter(
                            GatewayMessage.org_id == org,
                            GatewayMessage.id == route.id,
                            GatewayMessage.state.in_(("queued", "expired")),
                        )
                        .update({"state": "expired"}, synchronize_session=False)
                    )
                    if changed:
                        job.status = "queued" if job.attempts < 5 else "failed"
                        job.error = "Transport vor Socket-Send abgelaufen"
                        job.push_route_id, job.lease_expires_at = None, None
                elif job.lease_expires_at and job.lease_expires_at <= now:
                    job.status, job.error = "uncertain", "Druckergebnis nach moeglicher Uebergabe unbekannt"
            db.commit()


def pending():
    from app.models.gateway import PrinterAgentRoute
    from app.services.print_agent_service import enqueue_for_agents

    with database() as db:
        legacy = (
            db.query(PrintJob.org_id, PrintJob.id)
            .join(PrinterAgentRoute, PrinterAgentRoute.printer_id == PrintJob.printer_id)
            .filter(
                PrintJob.status == "queued",
                PrintJob.delivery == "push",
                PrinterAgentRoute.org_id == PrintJob.org_id,
                PrinterAgentRoute.aktiv.is_(True),
            )
            .execution_options(include_all_tenants=True)
            .distinct()
            .limit(100)
            .all()
        )
        for org, jid in legacy:
            job = db.query(PrintJob).filter(PrintJob.org_id == org, PrintJob.id == jid).first()
            if job is not None:
                enqueue_for_agents(db, job)
        # Offline-Ziele duerfen den Auswahlblock nicht vor erreichbaren Zielen fuellen.
        return (
            db.query(PrintJob.org_id, PrintJob.id)
            .join(Gateway, PrintJob.gateway_id == Gateway.id)
            .filter(
                PrintJob.status == "queued",
                PrintJob.delivery == "push",
                Gateway.kind == "linux",
                Gateway.org_id == PrintJob.org_id,
                Gateway.status == "online",
                Gateway.agent_connection_id.isnot(None),
                Gateway.agent_worker_id.isnot(None),
                Gateway.agent_presence_expires_at > presence.utc(),
                Gateway.device_token_hash.isnot(None),
                Gateway.agent_connection_token_hash == Gateway.device_token_hash,
            )
            .execution_options(include_all_tenants=True)
            .order_by(PrintJob.id)
            .limit(100)
            .all()
        )


async def run_once():
    await asyncio.to_thread(reconcile)
    jobs = await asyncio.to_thread(pending)
    for org, jid in jobs:
        try:
            await dispatch(org, jid, wait_seconds=0)
        except Exception:
            logger.exception("Linux-Auftragsvorbereitung fehlgeschlagen org=%s job=%s", org, jid)
            with database() as db:
                db.query(PrintJob).filter(
                    PrintJob.org_id == org,
                    PrintJob.id == jid,
                    PrintJob.status == "queued",
                    PrintJob.push_route_id.is_(None),
                ).update(
                    {"status": "failed", "error": "Druckauftragsvorbereitung fehlgeschlagen"}, synchronize_session=False
                )
                db.commit()


async def _loop():
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Linux-Druckqueue-Iteration fehlgeschlagen")
        await asyncio.sleep(2)


async def start():
    global _task
    if not settings.TESTING and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop())


async def stop():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


def receive_ack(connection, token_hash, job_id):
    if not isinstance(job_id, str) or not job_id.isascii() or not job_id.isdecimal():
        return
    with database() as db:
        if (
            presence.state(
                db, "linux", connection.org_id, connection.gateway_id, token_hash, connection.connection_id, lock=True
            )
            != "connected"
        ):
            return
        db.query(PrintJob).filter(
            PrintJob.org_id == connection.org_id,
            PrintJob.gateway_id == connection.gateway_id,
            PrintJob.id == int(job_id),
            PrintJob.delivery == "push",
            PrintJob.status == "sent",
            PrintJob.phase.in_(("dispatched", "prepared")),
        ).update({"phase": "received", "agent_contact_at": presence.utc()}, synchronize_session=False)
        db.commit()


def print_intent(connection, token_hash, job_id, dispatch_token, generation):
    if not isinstance(job_id, int) or not isinstance(dispatch_token, str) or not dispatch_token:
        return False
    with database() as db:
        if (
            presence.state(
                db, "linux", connection.org_id, connection.gateway_id, token_hash, connection.connection_id, lock=True
            )
            != "connected"
        ):
            return False
        job = (
            db.query(PrintJob)
            .filter(
                PrintJob.org_id == connection.org_id,
                PrintJob.gateway_id == connection.gateway_id,
                PrintJob.id == job_id,
                PrintJob.delivery == "push",
            )
            .with_for_update()
            .first()
        )
        if (
            job is None
            or job.status != "sent"
            or job.lease_token != dispatch_token
            or job.lease_generation != generation
        ):
            return False
        if job.phase == "submitting":
            return bool(job.lease_expires_at and job.lease_expires_at > presence.utc())
        if (
            job.phase not in ("prepared", "received")
            or not job.lease_expires_at
            or job.lease_expires_at <= presence.utc()
        ):
            return False
        job.phase, job.agent_contact_at = "submitting", presence.utc()
        job.lease_expires_at = presence.utc() + timedelta(minutes=10)
        db.commit()
        return True


def refresh_artifact(connection, token_hash, jid):
    from app.services.print_artifact_service import artifact_url

    with database() as db:
        if (
            presence.state(
                db, "linux", connection.org_id, connection.gateway_id, token_hash, connection.connection_id, lock=True
            )
            != "connected"
        ):
            return None
        job = (
            db.query(PrintJob)
            .filter(
                PrintJob.org_id == connection.org_id,
                PrintJob.gateway_id == connection.gateway_id,
                PrintJob.id == jid,
                PrintJob.status.in_(("queued", "sent")),
            )
            .first()
        )
        return artifact_url(job) if job is not None else None
