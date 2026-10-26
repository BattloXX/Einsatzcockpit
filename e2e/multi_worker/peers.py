"""Protokoll-Peers ohne Modem/Spooler; die Zaehler sind keine physische Abnahme."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from e2e.multi_worker.lab import eventually


class Ledger:
    def __init__(self, lab):
        self.path = lab.directory / ("peer-ledger-" + uuid4().hex + ".json")
        self.submissions = Counter()

    def submit_once(self, ident):
        if self.submissions[str(ident)]:
            return False
        self.submissions[str(ident)] += 1
        self.path.write_text(json.dumps(dict(self.submissions)))
        return True


class Peer:
    def __init__(self, lab, gateway, *, owner=None, ledger=None):
        self.lab, self.gateway, self.target_owner = lab, gateway, owner
        self.ledger = ledger or Ledger(lab)
        self.tasks = set()
        self.pending = {}
        self.errors = []
        self.hints = 0
        self.received = Counter()
        self.hold_before_intent = False
        self.hold_received = False
        self.hold_after_intent = False
        self.drop_after_attempt = False
        self.held = {}
        self.wires = {}
        self.auto = True
        self.poll_lock = asyncio.Lock()
        self.socket = None

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, kind, value, traceback):
        await self.close()
        if kind is None:
            assert not self.errors, "Gateway-Protokollfehler: " + ",".join(self.errors)

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception() and not isinstance(done.exception(), ConnectionClosed):
                self.errors.append(type(done.exception()).__name__)

        task.add_done_callback(completed)
        return task

    async def open(self):
        kind = self.gateway["kind"]
        path = {"sms": "sms-gateway", "linux": "gateway", "windows_agent": "print-agent"}[kind]
        for _ in range(60):
            self.socket = await connect(
                self.lab.base.replace("http://", "ws://") + "/ws/" + path,
                additional_headers={"Authorization": "Bearer " + self.gateway["token"]},
            )
            await asyncio.wait_for(self.socket.recv(), timeout=15)  # Initiale config/config_sync.
            model = "SmsGatewayToken" if kind == "sms" else "Gateway"
            row = self.lab.row(model, self.gateway["id"])
            self.owner = row.worker_id if kind == "sms" else row.agent_worker_id
            self.connection = row.connection_id if kind == "sms" else row.agent_connection_id
            if self.target_owner is None or self.target_owner == self.owner:
                break
            await self.socket.close()
        else:
            raise AssertionError("Gateway-Zielworker nicht erreicht")
        self.spawn(self.reader())
        if kind == "sms":
            await self.send({"type": "hello", "capabilities": ["sms-transaction-v2"], "open_jobs": []})
            await eventually(lambda: self.lab.row(model, self.gateway["id"]).sms_protocol == 2)
        elif kind == "linux":
            await self.send(
                {"type": "hello", "payload": {"version": "phase6-peer", "capabilities": ["print-status-v2"]}}
            )
            await eventually(lambda: self.lab.row(model, self.gateway["id"]).print_protocol == 2)
        else:
            await self.send(
                {
                    "type": "hello",
                    "payload": {"version": "phase6-peer", "open_jobs": [], "host_info": {"host": "Phase 6"}},
                }
            )
            await self.readiness()
            await eventually(lambda: self.lab.row("PrinterAgentRoute", self.gateway["route_id"]).ready)
            self.spawn(self.polling())
        self.spawn(self.heartbeat())

    async def send(self, message):
        await self.socket.send(json.dumps(message))

    async def rpc(self, kind, payload):
        rid = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.send({"type": kind, "rid": rid, "payload": payload})
            return await asyncio.wait_for(future, timeout=20)
        finally:
            self.pending.pop(rid, None)

    async def heartbeat(self):
        while True:
            await self.send({"type": "ping" if self.gateway["kind"] == "sms" else "heartbeat"})
            if self.gateway["kind"] == "windows_agent":
                await self.readiness()
            await asyncio.sleep(5)

    async def readiness(self):
        await self.send(
            {
                "type": "readiness",
                "payload": {"routes": [{"route_id": self.gateway["route_id"], "ready": True, "state": "ready"}]},
            }
        )

    async def reader(self):
        async for raw in self.socket:
            message = json.loads(raw)
            rid = message.get("rid")
            if rid in self.pending:
                self.pending[rid].set_result(message)
                continue
            kind = message.get("type")
            if kind == "ping":
                await self.send({"type": "pong"})
            elif kind in ("sms.prepare", "sms.commit"):
                await self.sms(message)
            elif kind == "print_job":
                self.spawn(self.linux(message))
            elif kind == "job_available":
                self.hints += 1
                if self.auto:
                    self.spawn(self.poll())

    async def sms(self, message):
        jid = message["job_id"]
        wire = {key: message[key] for key in ("id", "job_id", "generation")}
        self.wires[jid] = wire
        self.received[(message["type"], jid)] += 1
        if message["type"] == "sms.prepare":
            if not self.hold_received:
                await self.send({"type": "sms.ack", **wire})
        else:
            self.ledger.submit_once(jid)
            await self.send({"type": "sms.attempt", **wire})
            if self.drop_after_attempt:
                await eventually(lambda: self.lab.row("SmsDispatchJob", jid).attempted_at is not None)
                await self.socket.close()
                return
            result = {
                "type": "sms.result",
                **wire,
                "ok": True,
                "provider_accepted": True,
                "outcome": "sent",
                "attempted": True,
                "delivery_verified": False,
            }
            await self.send(result)
            await self.send(result)  # Idempotenz des Abschlusses ebenfalls pruefen.

    async def artifact(self, url):
        absolute = urljoin(self.lab.base, url)
        assert absolute.startswith(self.lab.base + "/")
        self.lab.secrets.extend(value for values in parse_qs(urlsplit(absolute).query).values() for value in values)
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(absolute)
        assert response.status_code == 200, "Druckartefakt nicht abrufbar"
        assert response.content.startswith(b"%PDF"), "Druckartefakt ist kein PDF"

    async def linux(self, message):
        jid = int(message["id"])
        payload = message["payload"]
        self.received[("print_job", str(jid))] += 1
        await self.send({"type": "ack", "id": str(jid)})
        await self.artifact(payload["artifact_url"])
        result = await self.rpc(
            "print_intent",
            {"job_id": jid, "dispatch_token": payload["dispatch_token"], "generation": payload["generation"]},
        )
        assert result["payload"]["ok"]
        self.ledger.submit_once(jid)
        for state in ("printing", "done"):
            await self.send({"type": "job_status", "payload": {"job_id": jid, "status": state}})

    async def polling(self):
        while True:
            if self.auto:
                await self.poll()
            await asyncio.sleep(2)

    async def poll(self):
        async with self.poll_lock:
            response = await self.rpc("lease_request", {"max": 10})
            grants = response["payload"]["jobs"]
            for grant in grants:
                self.held[grant["job_id"]] = grant
                if not self.hold_before_intent:
                    await self.windows(grant)
            return grants

    async def windows(self, grant):
        jid = grant["job_id"]
        await self.artifact(grant["artifact_url"])
        result = await self.rpc("print_intent", {"job_id": jid, "lease_token": grant["lease_token"]})
        assert result["payload"]["ok"]
        self.ledger.submit_once(jid)
        if self.hold_after_intent:
            return
        await self.status(grant, "printing")
        await self.status(grant, "done")

    async def status(self, grant, state):
        await self.send(
            {
                "type": "job_status",
                "id": uuid4().hex,
                "payload": {
                    "job_id": grant["job_id"],
                    "lease_token": grant["lease_token"],
                    "status": state,
                    "phase": "spooled" if state != "uncertain" else "submitting",
                    "spool_ref": "win:phase6",
                },
            }
        )

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.socket is not None:
            await self.socket.close()
