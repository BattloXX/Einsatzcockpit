"""Gemeinsame Testdaten mit echter Presence und lokalem Socket, ohne Routing-Mocks."""

import asyncio
import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway, PrintJob
from app.models.user import SmsGatewayToken
from app.services import gateway_routing as routing


class CaptureSocket:
    def __init__(self, *, reply=False, dead=False):
        self.sent = []
        self.reply = reply
        self.dead = dead
        self.connection = None
        self.token_hash = None
        self.closed = []

    async def send_text(self, raw):
        if self.dead:
            raise RuntimeError("socket closed")
        message = json.loads(raw)
        self.sent.append(message)
        if self.reply:
            job = str(message.get("id"))
            result = (
                {"id": job, "ok": True} if self.connection.kind == "sms" else {"job_id": int(job), "status": "done"}
            )
            await asyncio.to_thread(routing.record_result, self.connection, self.token_hash, job, result)

    async def close(self, code=1000):
        self.closed.append(code)


def bind(kind="sms", org_id=1, *, priority=100, socket=None, gateway_id=None):
    token_hash = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        if kind == "sms":
            row = SmsGatewayToken(org_id=org_id, token_hash=token_hash, label="Routing test", priority=priority)
        else:
            row = Gateway(org_id=org_id, name="Routing test", kind=kind, device_token_hash=token_hash)
        if gateway_id is not None:
            row.id = gateway_id
        db.add(row)
        db.commit()
        gid = row.id
    connection = routing.begin(kind, org_id, gid, token_hash)
    socket = socket or CaptureSocket()
    socket.connection, socket.token_hash = connection, token_hash
    routing.attach(connection, token_hash, socket)
    return connection, socket


def cleanup(connection):
    routing.detach(connection)
    routing.end(connection)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        model = SmsGatewayToken if connection.kind == "sms" else Gateway
        if connection.kind != "sms":
            db.query(PrintJob).filter(PrintJob.org_id == connection.org_id,
                                      PrintJob.gateway_id == connection.gateway_id).delete()
        row = db.query(model).filter(model.org_id == connection.org_id, model.id == connection.gateway_id).first()
        if row is not None:
            db.delete(row)
        db.commit()
