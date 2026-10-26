"""Linux-Push-Claim, Retry-Nachweise und physische Doppel-Ausgabegrenzen."""

import asyncio
from datetime import timedelta

import pytest

from app.models.gateway import Gateway, GatewayMessage, PrintJob
from app.routers import ws
from app.services import gateway_routing as routing
from app.services import linux_print_service as service
from app.services.gateway_presence import utc
from tests.gateway_helpers import CaptureSocket, bind, cleanup
from tests.test_gateway_dispatch import make_job


@pytest.fixture
def gateway():
    connection, socket = bind("linux")
    yield connection, socket
    cleanup(connection)


async def test_competing_dispatchers_and_redis_echo_have_one_transport(gateway, monkeypatch):
    c, socket = gateway
    jid = make_job(c)

    async def echo(channel, message):
        await routing.deliver(message["message_id"])
        await routing.deliver(message["message_id"])

    monkeypatch.setattr(routing.ws_bus, "publish", echo)
    await asyncio.gather(*(service.dispatch(1, jid, {"job_id": jid}, wait_seconds=0) for _ in range(5)))
    assert len(socket.sent) == 1
    with service.database() as db:
        job = db.get(PrintJob, jid)
        assert job.attempts == job.lease_generation == 1
        assert db.query(GatewayMessage).filter_by(org_id=1, job_key=str(jid), gateway_kind="linux").count() == 1
    service.receive_ack(c, socket.token_hash, str(jid))
    with service.database() as db:
        assert db.get(PrintJob, jid).phase == "received"
    assert service.snapshot(1, jid)["status"] == "sent"


async def test_queued_route_can_recover_but_delivered_route_cannot_retry(gateway):
    c, socket = gateway
    jid = make_job(c)
    route, _ = service.prepare(1, jid, {"job_id": jid})
    with service.database() as db:
        db.query(GatewayMessage).filter_by(org_id=1, id=route).update({"expires_at": utc() - timedelta(seconds=1)})
        db.commit()
    service.reconcile()
    assert service.snapshot(1, jid)["status"] == "queued"
    await service.dispatch(1, jid, {"job_id": jid}, wait_seconds=0)
    assert len(socket.sent) == 1
    with service.database() as db:
        job = db.get(PrintJob, jid)
        job.lease_expires_at = utc() - timedelta(seconds=1)
        db.commit()
    service.reconcile()
    assert service.snapshot(1, jid)["status"] == "uncertain"
    await service.dispatch(1, jid, {"job_id": jid}, wait_seconds=0)
    assert len(socket.sent) == 1
    assert (
        ws._apply_job_status(
            jid, "done", None, org_id=1, gateway_id=c.gateway_id, connection=c, token_hash=socket.token_hash
        )
        is None
    )


@pytest.mark.parametrize(
    "spooled,proof,expected", [(False, True, "failed"), (False, False, "uncertain"), (True, True, "uncertain")]
)
def test_negative_status_needs_pre_spooler_proof(gateway, spooled, proof, expected):
    c, socket = gateway
    jid = make_job(c)
    service.prepare(1, jid, {"job_id": jid})
    with service.database() as db:
        db.query(Gateway).filter_by(org_id=1, id=c.gateway_id).update({"print_protocol": 2})
        if spooled:
            db.query(PrintJob).filter_by(org_id=1, id=jid).update({"phase": "spooled", "status": "printing"})
        db.commit()
    ws._apply_job_status(
        jid,
        "failed",
        "offline",
        org_id=1,
        gateway_id=c.gateway_id,
        connection=c,
        token_hash=socket.token_hash,
        not_submitted=proof,
    )
    assert service.snapshot(1, jid)["status"] == expected


async def test_offline_gateway_keeps_persistent_queue_until_reconnect(gateway):
    c, socket = gateway
    jid = make_job(c)
    routing.end(c)
    state = await service.dispatch(1, jid, {"job_id": jid}, wait_seconds=0)
    assert state["status"] == "queued" and not socket.sent
    current = routing.begin("linux", 1, c.gateway_id, socket.token_hash)
    peer = CaptureSocket(reply=True)
    peer.connection, peer.token_hash = current, socket.token_hash
    routing.attach(current, socket.token_hash, peer)
    try:
        assert not routing.end(c)
        state = await service.dispatch(1, jid, {"job_id": jid}, wait_seconds=0)
        assert state["status"] == "done" and len(peer.sent) == 1
    finally:
        routing.detach(current)


def test_linux_intent_is_fenced_after_timeout_and_by_token(gateway):
    c, socket = gateway
    with service.database() as db:
        db.query(Gateway).filter_by(org_id=1, id=c.gateway_id).update({"print_protocol": 2})
        db.commit()
    jid = make_job(c)
    route, _ = service.prepare(1, jid, {"job_id": jid})
    with service.database() as db:
        payload = db.get(GatewayMessage, route).message["payload"]
    assert payload["requires_intent"]
    assert not service.print_intent(c, socket.token_hash, jid, "wrong", payload["generation"])
    assert service.print_intent(c, socket.token_hash, jid, payload["dispatch_token"], payload["generation"])
    assert service.print_intent(c, socket.token_hash, jid, payload["dispatch_token"], payload["generation"])
    with service.database() as db:
        db.query(PrintJob).filter_by(org_id=1, id=jid).update({"status": "uncertain"})
        db.commit()
    assert not service.print_intent(c, socket.token_hash, jid, payload["dispatch_token"], payload["generation"])


async def test_manual_resolution_creates_exactly_one_durable_reprint(gateway, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from starlette.requests import Request

    from app.routers.ui_gateway import resolve_uncertain_job

    c, _ = gateway
    jid = make_job(c)
    with service.database() as db:
        db.query(PrintJob).filter_by(org_id=1, id=jid).update({"status": "uncertain"})
        db.commit()
    monkeypatch.setattr("app.services.print_dispatcher.dispatch_job", AsyncMock())
    user = SimpleNamespace(org_id=1, id=1, display_name="Operator", username="operator")
    for _ in range(2):
        with service.database() as db:
            await resolve_uncertain_job(
                jid, Request({"type": "http", "headers": []}), action="reprint", db=db, user=user
            )
    with service.database() as db:
        assert db.get(PrintJob, jid).status == "canceled"
        assert db.query(PrintJob).filter_by(org_id=1, fallback_of_job_id=jid).count() == 1
        assert db.query(PrintJob).filter_by(org_id=1, fallback_of_job_id=jid).one().status == "queued"
