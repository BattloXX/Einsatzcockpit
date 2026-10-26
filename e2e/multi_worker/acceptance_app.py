"""Echte ECP-Lifespan mit streng begrenzten, nur im Test aktivierbaren Ausloesern.

Keine Modem-/Spooler-Nachbildung im Server: diese sitzt an den echten WS-Endpunkten.
Die Ausloeser waehlen lediglich deterministisch einen Gunicorn-Worker und rufen
die produktiven Dienste auf. Der normale Import app.main:app aktiviert sie nicht.
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit

if os.environ.get("ECP_ACCEPTANCE_TEST") != "1" or not os.environ.get("ECP_ACCEPTANCE_KEY"):
    raise RuntimeError("Acceptance-App ist ausschliesslich fuer die isolierte Testumgebung")
if not urlsplit(os.environ.get("DATABASE_URL", "")).path.startswith("/ecp_phase6_"):
    raise RuntimeError("Acceptance-App benoetigt eine eigens angelegte Phase-6-Testdatenbank")

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.main import app
from app.models.gateway import PrintJob
from app.models.incident import IncidentAlarmJob
from app.services import alarm_outbox, linux_print_service, sms_outbox, ws_bus
from app.services.gateway_presence import utc, worker_id
from app.services.sms_service import SmsContext

_publish = ws_bus.publish


async def record_publish(channel, message):
    if channel == ws_bus.CH_GW and message.get("kind") == "route":
        # Metadatenbeweis der Workergrenze; niemals Gateway-Payloads aufzeichnen.
        record = {
            "publisher": worker_id(),
            "message_id": message["message_id"],
            "owner": message["worker_id"],
            "gateway_id": message["gateway_id"],
        }
        path = os.path.join(os.environ["ECP_ACCEPTANCE_ARTIFACTS"], f"routes-{os.getpid()}.jsonl")
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(record) + "\n").encode())
        finally:
            os.close(fd)
    return await _publish(channel, message)


ws_bus.publish = record_publish


class ConnectionRecorder:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def recorded(message):
            if message["type"] == "websocket.accept":
                path = os.path.join(os.environ["ECP_ACCEPTANCE_ARTIFACTS"], f"connections-{os.getpid()}.jsonl")
                record = {"path": scope["path"], "worker": worker_id(), "at": time.time_ns()}
                await asyncio.to_thread(append_connection, path, record)
            await send(message)

        await self.app(scope, receive, recorded)


app.add_middleware(ConnectionRecorder)


def append_connection(path, record):
    with open(path, "a") as stream:
        stream.write(json.dumps(record) + "\n")


def authorized(key: str) -> None:
    if not hmac.compare_digest(key, os.environ["ECP_ACCEPTANCE_KEY"]):
        raise HTTPException(403)


@app.middleware("http")
async def worker_tag(request: Request, call_next):
    target = request.headers.get("x-ecp-target-worker")
    if target and target != worker_id():
        return JSONResponse({"retry_worker": True}, status_code=409, headers={"Connection": "close"})
    response = await call_next(request)
    response.headers["X-ECP-Acceptance-Worker"] = worker_id()
    return response


@app.get("/api/v1/__acceptance/runtime")
def runtime(x_ecp_acceptance_key: str = Header("")):
    authorized(x_ecp_acceptance_key)
    # Ausschliesslich nicht geheime Konfiguration fuer den Gleichheitsnachweis.
    safe = {
        name: os.environ.get(name, "")
        for name in ("ECP_GUNICORN_WORKERS", "ECP_GUNICORN_BIND", "LEADER_LOCK_PATH", "TESTING")
    }
    return {
        "worker": worker_id(),
        "configuration": hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest(),
    }


@app.post("/api/v1/__acceptance/trigger")
async def trigger(request: Request, x_ecp_acceptance_key: str = Header("")):
    authorized(x_ecp_acceptance_key)
    data = await request.json()
    org = int(data["org_id"])
    if data["kind"] == "alarm":
        with sms_outbox.database() as db:
            job = IncidentAlarmJob(
                org_id=org,
                incident_id=int(data["incident_id"]),
                channel="sms",
                status="pending",
                next_attempt_at=utc(),
                context={},
            )
            db.add(job)
            db.flush()
            assert alarm_outbox._claim(db, job, utc())
            job_id, generation = job.id, job.attempt_count
        await alarm_outbox._run_claimed(job_id, generation)
        return {"alarm_job": job_id, "worker": worker_id()}
    if data["kind"] == "sms":
        ids = [
            sms_outbox.ensure(org, entry["key"], entry["phone"], entry["text"], ["gateway"]) for entry in data["jobs"]
        ]
        await asyncio.gather(*(sms_outbox.drive(org, jid, SmsContext(org, ["gateway"], None)) for jid in ids))
        return {"job_ids": ids, "worker": worker_id()}
    if data["kind"] == "linux":
        return await linux_print_service.dispatch(org, int(data["job_id"]), wait_seconds=1)
    if data["kind"] == "windows":
        from app.routers.ws import notify_agents_job_available
        from app.services import print_agent_service

        with sms_outbox.database() as db:
            job = db.query(PrintJob).filter(PrintJob.id == int(data["job_id"]), PrintJob.org_id == org).one()
            gateways = print_agent_service.enqueue_for_agents(db, job)
        await notify_agents_job_available(org, gateways, [job.id])
        return {"job_id": job.id, "worker": worker_id()}
    raise HTTPException(400, "Unbekannter Testausloeser")
