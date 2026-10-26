"""Persistente SMS-Grenzen, Generations-Fencing und beweisbar sicherer Fallback."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.models.sms import SmsDispatchAttempt as Attempt
from app.models.sms import SmsDispatchCursor as Cursor
from app.models.sms import SmsDispatchJob as Job
from app.services import gateway_presence as presence
from app.services import sms_outbox as outbox
from app.services.sms_service import SmsContext, send_sms
from tests.gateway_helpers import CaptureSocket, bind, cleanup

pytestmark = pytest.mark.usefixtures("sms_outbox_state")


async def test_run_once_limits_parallel_drives(monkeypatch):
    ids = {outbox.ensure(1, f"parallel-{i}", "+43660000", "Alarm", ["gateway"])
           for i in range(outbox.MAX_PARALLEL_DRIVES * 3)}
    running, peak = 0, 0
    driven = []

    async def drive(org_id, job_id):
        nonlocal running, peak
        assert org_id == 1
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0)
            driven.append(job_id)
        finally:
            running -= 1

    monkeypatch.setattr(outbox, "drive", drive)
    await outbox.run_once()
    assert peak == outbox.MAX_PARALLEL_DRIVES
    assert len(driven) == len(ids) and set(driven) == ids


async def test_run_once_skips_future_queued_job(monkeypatch):
    due = outbox.ensure(1, "due", "+43660000", "Alarm", ["gateway"])
    future = outbox.ensure(1, "future", "+43660000", "Alarm", ["gateway"])
    with outbox.database() as db:
        db.query(Job).filter(Job.org_id == 1, Job.id == future).update(
            {"next_attempt_at": presence.utc() + timedelta(hours=1)}, synchronize_session=False
        )
        db.commit()
    driven = []

    async def drive(org_id, job_id):
        driven.append((org_id, job_id))

    monkeypatch.setattr(outbox, "drive", drive)
    await outbox.run_once()
    assert driven == [(1, due)]
    assert outbox.snapshot(1, future)["status"] == "queued"


@pytest.fixture
def sms_state():
    created = []

    def gateway(*, protocol=2, reply=False, org_id=1, priority=100):
        connection, socket = bind("sms", org_id, priority=priority, socket=CaptureSocket(reply=reply))
        outbox.hello(connection, socket.token_hash, {"capabilities": ["sms-transaction-v2"] if protocol == 2 else []})
        created.append(connection)
        return connection, socket

    yield gateway
    for connection in created:
        cleanup(connection)
    with outbox.database() as db:
        for org in (1, 2):
            db.query(Attempt).filter(Attempt.org_id == org).delete()
            db.query(Job).filter(Job.org_id == org).delete()
            db.query(Cursor).filter(Cursor.org_id == org).delete()
        db.commit()


@pytest.mark.parametrize("target", ["sms_log", "api_message"])
@pytest.mark.parametrize("status", outbox.TERMINAL)
@pytest.mark.parametrize("age_s", [0, outbox.PROJECTION_WINDOW_S, outbox.PROJECTION_WINDOW_S + 1])
async def test_run_once_terminal_projection_window(monkeypatch, target, status, age_s):
    from app.models.api_message import ApiMessage, ApiMessageRecipient
    from app.models.sms import SmsLog, SmsLogRecipient

    now = presence.utc()
    monkeypatch.setattr(presence, "utc", lambda: now)
    jid = outbox.ensure(1, "projection-window", "+43660000", "Alarm", ["gateway"])
    with outbox.database() as db:
        job = db.get(Job, jid)
        job.status, job.finished_at = status, now - timedelta(seconds=age_s)
        if target == "sms_log":
            parent = SmsLog(org_id=1, source="manual", text="Alarm", sent_at=now, recipient_count=1)
            db.add(parent)
            db.flush()
            recipient = SmsLogRecipient(
                sms_log_id=parent.id, phone_number=job.phone, sent_at=now,
                dispatch_job_id=jid, dispatch_status="queued", provider="ausstehend", success=False,
            )
        else:
            parent = ApiMessage(org_id=1, api_key_id=1, external_key=uuid4().hex, kanal="sms", recipient_count=1)
            db.add(parent)
            db.flush()
            recipient = ApiMessageRecipient(
                org_id=1, message_id=parent.id, ziel=job.phone, dispatch_job_id=jid, status="sending",
            )
        db.add(recipient)
        db.commit()
        recipient_id, model = recipient.id, type(recipient)
    projected = []
    original_project = outbox.project

    def project(org_id, job_id):
        projected.append((org_id, job_id))
        original_project(org_id, job_id)

    monkeypatch.setattr(outbox, "project", project)
    await outbox.run_once()
    expected = age_s <= outbox.PROJECTION_WINDOW_S
    assert projected == ([(1, jid)] if expected else [])
    with outbox.database() as db:
        recipient = db.get(model, recipient_id)
        actual = recipient.dispatch_status if target == "sms_log" else recipient.status
        assert actual == (status if expected else "queued" if target == "sms_log" else "sending")


async def test_run_once_projects_old_nonterminal_job():
    from app.models.sms import SmsLog, SmsLogRecipient

    now = presence.utc()
    jid = outbox.ensure(1, "old-projection", "+43660000", "Alarm", ["gateway"])
    with outbox.database() as db:
        job = db.get(Job, jid)
        job.created_at = now - timedelta(days=90)
        job.next_attempt_at = now + timedelta(minutes=5)
        log = SmsLog(org_id=1, source="manual", text="Alarm", sent_at=job.created_at, recipient_count=1)
        db.add(log)
        db.flush()
        recipient = SmsLogRecipient(
            sms_log_id=log.id, phone_number=job.phone, sent_at=log.sent_at,
            dispatch_job_id=jid, dispatch_status="claimed", provider="sendet", success=False,
        )
        db.add(recipient)
        db.commit()
        recipient_id = recipient.id
    await outbox.run_once()
    with outbox.database() as db:
        assert db.get(SmsLogRecipient, recipient_id).dispatch_status == "queued"


def expire(jid, org=1):
    with outbox.database() as db:
        db.query(Job).filter(Job.org_id == org, Job.id == jid).update(
            {"lease_until": presence.utc() - timedelta(seconds=1)}, synchronize_session=False
        )
        db.commit()


async def prepare(gateway, *, key=None, providers=None):
    c, socket = gateway
    jid = outbox.ensure(
        c.org_id, key or uuid4().hex, "+43660000", "Alarm", providers or ["gateway"], preferred=c.gateway_id
    )
    await outbox.drive(c.org_id, jid, SmsContext(c.org_id, providers or ["gateway"], None))
    message = socket.sent[-1]
    return jid, {"id": message["id"], "job_id": jid, "generation": message["generation"]}


@pytest.mark.parametrize("second_gateway", [False, True])
async def test_receipt_timeout_retries_another_gateway_or_only_target(sms_state, monkeypatch, second_gateway):
    first, socket = sms_state(priority=1)
    other, other_socket = sms_state(priority=20) if second_gateway else (first, socket)
    jid, wire = await prepare((first, socket))
    before = presence.utc()
    original_utc = presence.utc
    monkeypatch.setattr(presence, "utc", lambda now=None: original_utc(now) if now is not None else before)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "dispatched"
    later = before + timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
    monkeypatch.setattr(presence, "utc", lambda now=None: original_utc(now) if now is not None else later)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "queued"
    with outbox.database() as db:
        assert db.get(Attempt, (jid, 1)).outcome == "receipt_timeout"
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    state = outbox.snapshot(1, jid)
    assert state["generation"] == 2 and state["gateway_token_id"] == other.gateway_id
    assert other_socket.sent[-1]["generation"] == 2
    assert not outbox.event(first, socket.token_hash, {**wire, "type": "sms.ack"})[0]


async def test_receipt_timeout_filters_all_failed_gateways_before_fallback(sms_state, monkeypatch):
    first, socket = sms_state(priority=1)
    second, _ = sms_state(priority=20)
    third, _ = sms_state(priority=30)
    jid, _ = await prepare((first, socket))
    later = presence.utc()
    original_utc = presence.utc
    monkeypatch.setattr(presence, "utc", lambda now=None: original_utc(now) if now is not None else later)
    for target in (second, third):
        later += timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
        outbox.sweep()
        await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
        assert outbox.snapshot(1, jid)["gateway_token_id"] == target.gateway_id
    later += timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
    outbox.sweep()
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    assert outbox.snapshot(1, jid)["status"] == "dispatched"


@pytest.mark.parametrize("guard", ["received", "ack", "may_have_sent", "delivered_grant"])
async def test_receipt_timeout_preserves_possible_send(sms_state, monkeypatch, guard):
    from app.models.gateway import GatewayMessage

    c, socket = sms_state()
    jid, wire = await prepare((c, socket))
    if guard == "ack":
        _, controls, _ = outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})
        await outbox.notify(controls)
    else:
        with outbox.database() as db:
            job = db.get(Job, jid)
            if guard == "received":
                job.received_at = presence.utc()
            elif guard == "may_have_sent":
                job.may_have_sent = True
            else:
                attempt = db.get(Attempt, (jid, 1))
                grant_id = outbox.route(db, c, {"type": "sms.commit"}, job_key=wire["id"])
                db.get(GatewayMessage, grant_id).state = "delivered"
                attempt.grant_route_id = grant_id
            db.commit()
    before = outbox.snapshot(1, jid)
    later = presence.utc() + timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
    monkeypatch.setattr(presence, "utc", lambda: later)
    outbox.sweep()
    assert outbox.snapshot(1, jid) == before
    with outbox.database() as db:
        assert db.get(Attempt, (jid, 1)).outcome != "receipt_timeout"
    expire(jid)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == ("queued" if guard == "received" else "uncertain")


async def test_receipt_timeout_uses_dispatch_time_and_cancels_queued_grants(sms_state, monkeypatch):
    from app.models.gateway import GatewayMessage

    c, socket = sms_state()
    jid = outbox.ensure(1, "delayed-prepare", "+43660000", "Alarm", ["gateway"])
    token = outbox.claim(1, jid)
    later = presence.utc() + timedelta(seconds=20)
    original_utc = presence.utc
    monkeypatch.setattr(presence, "utc", lambda now=None: original_utc(now) if now is not None else later)
    provider, routes = outbox._prepare(1, jid, token, False, 15)
    assert provider == "gateway"
    await outbox.notify(routes)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "dispatched"
    with outbox.database() as db:
        attempt = db.get(Attempt, (jid, 1))
        assert attempt.created_at == later
        grant_ids = [outbox.route(db, c, {"type": "sms.commit"}, job_key=f"{jid}:1") for _ in range(2)]
        db.commit()
    later += timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "queued"
    with outbox.database() as db:
        assert all(db.get(GatewayMessage, gid).state == "expired" for gid in grant_ids)


@pytest.mark.parametrize("in_chain", [False, True])
async def test_receipt_timeout_respects_provider_fallback_chain(sms_state, monkeypatch, in_chain):
    c, socket = sms_state()
    providers = ["gateway", "eus"] if in_chain else ["gateway"]
    jid, _ = await prepare((c, socket), providers=providers)
    later = presence.utc() + timedelta(seconds=outbox.RECEIPT_TIMEOUT_S + 1)
    original_utc = presence.utc
    monkeypatch.setattr(presence, "utc", lambda now=None: original_utc(now) if now is not None else later)
    outbox.sweep()
    token = outbox.claim(1, jid)
    provider, _ = outbox._prepare(1, jid, token, True, 15)
    assert provider == ("eus" if in_chain else "gateway")


async def test_lease_not_dispatched_excludes_previous_gateway(sms_state):
    c, socket = sms_state(priority=1)
    other, _ = sms_state(priority=20)
    jid, _ = await prepare((c, socket))
    expire(jid)
    outbox.sweep()
    with outbox.database() as db:
        assert db.get(Attempt, (jid, 1)).outcome == "not_dispatched"
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    assert outbox.snapshot(1, jid)["gateway_token_id"] == other.gateway_id


async def test_ack_is_not_send_and_duplicates_do_not_create_second_commit(sms_state):
    c, socket = sms_state()
    jid, wire = await prepare((c, socket))
    assert outbox.snapshot(1, jid)["status"] == "dispatched"
    changed, routes, status = outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})
    assert changed and status == "acknowledged" and len(routes) == 1
    await outbox.notify(routes)
    assert outbox.snapshot(1, jid)["accepted_at"] is None
    assert outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})[1] == []
    assert [m["type"] for m in socket.sent] == ["sms.prepare", "sms.commit"]
    outbox.event(c, socket.token_hash, {**wire, "type": "sms.attempt"})
    assert outbox.snapshot(1, jid)["attempted_at"] is not None
    accepted = {**wire, "type": "sms.result", "ok": True, "provider_accepted": True, "outcome": "sent"}
    assert outbox.event(c, socket.token_hash, accepted)[0]
    final = outbox.snapshot(1, jid)
    assert final["status"] == "sent" and final["delivery_status"] == "unverified"
    assert not outbox.event(c, socket.token_hash, {**accepted, "ok": False})[0]
    assert not outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})[0]
    assert outbox.snapshot(1, jid) == final


async def test_missing_ack_safe_only_before_send_permission(sms_state):
    c, socket = sms_state()
    jid, wire = await prepare((c, socket))
    expire(jid)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "queued"
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    assert outbox.snapshot(1, jid)["generation"] == 2
    assert not outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})[0]
    assert outbox.snapshot(1, jid)["status"] == "dispatched"


@pytest.mark.parametrize("protocol", [1, 2])
async def test_disconnect_after_possible_send_is_uncertain_and_never_reclaimed(sms_state, protocol):
    c, socket = sms_state(protocol=protocol)
    jid, wire = await prepare((c, socket))
    if protocol == 2:
        _, controls, _ = outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})
        await outbox.notify(controls)
    expire(jid)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "uncertain"
    assert outbox.claim(1, jid) is None
    late = {**wire, "type": "sms.result", "ok": True, "provider_accepted": True}
    assert not outbox.event(c, socket.token_hash, late)[0]
    assert outbox.snapshot(1, jid)["status"] == "uncertain"


async def test_stable_job_is_sent_once_under_simultaneous_callers(sms_state):
    sms_state(reply=True)
    ctx = SmsContext(1, ["gateway"], None)
    results = await asyncio.gather(
        *(send_sms(1, "+43660000", "Alarm", ctx=ctx, job_id="stable", timeout=2) for _ in range(6))
    )
    assert all(results)
    assert len({r.job_id for r in results}) == 1
    with outbox.database() as db:
        assert db.query(Attempt).filter(Attempt.org_id == 1).count() == 1


async def test_wrong_gateway_org_and_generation_cannot_ack(sms_state):
    c, socket = sms_state()
    foreign, fs = sms_state(org_id=2)
    other, os = sms_state()
    jid, wire = await prepare((c, socket))
    for connection, token, msg in [
        (foreign, fs.token_hash, wire),
        (other, os.token_hash, wire),
        (c, socket.token_hash, {**wire, "generation": 9}),
    ]:
        assert not outbox.event(connection, token, {**msg, "type": "sms.ack"})[0]
    assert outbox.snapshot(1, jid)["status"] == "dispatched"


async def test_legacy_negative_response_never_falls_back(sms_state):
    c, socket = sms_state(protocol=1)
    jid, wire = await prepare((c, socket), providers=["gateway", "eus"])
    outbox.event(c, socket.token_hash, {**wire, "type": "sms.result", "ok": False})
    assert outbox.snapshot(1, jid)["status"] == "uncertain"
    assert outbox.snapshot(1, jid)["provider"] == "gateway"


async def test_missing_gateway_does_not_report_success(sms_state):
    result = await send_sms(
        1, "+43660000", "Alarm", ctx=SmsContext(1, ["gateway"], None), job_id="wait-gateway", timeout=0.05
    )
    assert not result and result.status == "queued"
    assert outbox.snapshot(1, result.job_id)["status"] == "queued"


async def test_legacy_queued_transport_can_be_cancelled_before_socket_send(sms_state, monkeypatch):
    c, socket = sms_state(protocol=1)

    async def suppress(_routes):
        pass

    monkeypatch.setattr(outbox, "notify", suppress)
    jid = outbox.ensure(1, "not-sent", "+43660000", "Alarm", ["gateway"])
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    assert not socket.sent
    expire(jid)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "queued"


async def test_preferred_offline_gateway_uses_next_priority(sms_state):
    from app.services import gateway_routing as routing

    preferred, _ = sms_state(priority=1)
    active, socket = sms_state(priority=20, reply=True)
    routing.end(preferred)
    result = await send_sms(
        1, "+43660000", "Alarm", ctx=SmsContext(1, ["gateway"], None), preferred_gateway_token_id=preferred.gateway_id
    )
    assert result and result.gateway_token_id == active.gateway_id
    assert socket.sent[0]["type"] == "sms.prepare"


async def test_global_inflight_limit_for_parallel_distinct_jobs(sms_state):
    c, socket = sms_state()
    ids = [outbox.ensure(1, f"burst-{i}", f"+436600{i}", "Alarm", ["gateway"]) for i in range(20)]
    await asyncio.gather(*(outbox.drive(1, jid, SmsContext(1, ["gateway"], None)) for jid in ids))
    states = [outbox.snapshot(1, jid)["status"] for jid in ids]
    assert states.count("dispatched") == 7 and states.count("queued") == 13
    assert len(socket.sent) == 7


async def test_restart_after_claim_without_external_effect_reclaims(sms_state):
    sms_state(reply=True)
    jid = outbox.ensure(1, "worker-died", "+43660000", "Alarm", ["gateway"])
    first = outbox.claim(1, jid)
    expire(jid)
    outbox.sweep()
    assert outbox._prepare(1, jid, first, False, 15) == (None, [])
    await outbox.drive(1, jid, SmsContext(1, ["gateway"], None))
    state = outbox.snapshot(1, jid)
    assert state["status"] == "sent" and state["generation"] == 2


async def test_reconnect_resumes_same_generation_and_old_disconnect_is_fenced(sms_state):
    from app.services import gateway_routing as routing
    from tests.gateway_helpers import CaptureSocket

    old, socket = sms_state()
    jid, wire = await prepare((old, socket))
    changed, controls, _ = outbox.event(old, socket.token_hash, {**wire, "type": "sms.ack"})
    await outbox.notify(controls)
    new = routing.begin("sms", 1, old.gateway_id, socket.token_hash)
    peer = CaptureSocket(reply=True)
    peer.connection, peer.token_hash = new, socket.token_hash
    routing.attach(new, socket.token_hash, peer)
    try:
        assert not routing.end(old)
        assert not outbox.event(old, socket.token_hash, {**wire, "type": "sms.result", "ok": True})[0]
        controls, abandoned = outbox.hello(
            new,
            socket.token_hash,
            {"capabilities": ["sms-transaction-v2"], "open_jobs": [{**wire, "state": "received"}]},
        )
        assert not abandoned and len(controls) == 1
        await outbox.notify(controls)
        assert peer.sent[0]["type"] == "sms.commit"
        state = outbox.snapshot(1, jid)
        assert state["status"] == "sent" and state["generation"] == 1
    finally:
        routing.detach(new)


async def test_new_queued_grant_does_not_erase_old_delivered_grant(sms_state):
    from app.services import gateway_routing as routing

    old, socket = sms_state()
    jid, wire = await prepare((old, socket))
    _, controls, _ = outbox.event(old, socket.token_hash, {**wire, "type": "sms.ack"})
    await outbox.notify(controls)  # Darf bereits einen Versand ausgeloest haben.
    new = routing.begin("sms", 1, old.gateway_id, socket.token_hash)
    controls, _ = outbox.hello(
        new, socket.token_hash, {"capabilities": ["sms-transaction-v2"], "open_jobs": [{**wire, "state": "received"}]}
    )
    assert len(controls) == 1  # neue Freigabe bleibt absichtlich in der DB-Queue
    expire(jid)
    outbox.sweep()
    assert outbox.snapshot(1, jid)["status"] == "uncertain"


async def test_revoked_token_cannot_ack_or_complete_job(sms_state):
    from app.models.user import SmsGatewayToken

    c, socket = sms_state()
    jid, wire = await prepare((c, socket))
    with outbox.database() as db:
        db.query(SmsGatewayToken).filter(SmsGatewayToken.org_id == 1, SmsGatewayToken.id == c.gateway_id).update(
            {"revoked_at": presence.utc()}, synchronize_session=False
        )
        db.commit()
    for typ in ("sms.ack", "sms.result"):
        assert not outbox.event(c, socket.token_hash, {**wire, "type": typ, "ok": True, "provider_accepted": True})[0]
    assert outbox.snapshot(1, jid)["status"] == "dispatched"


@pytest.mark.parametrize("safe", [False, True])
async def test_provider_fallback_requires_proven_non_send(sms_state, monkeypatch, safe):
    from unittest.mock import AsyncMock

    from app.services.eus_sms_service import EusConfig, EusSmsError

    c, socket = sms_state(reply=True)
    sender = AsyncMock(side_effect=EusSmsError("provider_error", safe_not_sent=safe))
    monkeypatch.setattr("app.services.sms_service.send_via_eus", sender)
    ctx = SmsContext(1, ["eus", "gateway"], EusConfig(1, "https://test.invalid", "basic", "id", "secret", 1))
    result = await send_sms(1, "+43660000", "Alarm", ctx=ctx, timeout=2)
    assert bool(result) == safe
    assert result.status == ("sent" if safe else "uncertain")
    assert bool(socket.sent) == safe
    assert sender.await_count == 1


async def test_gateway_preflight_rejection_can_fall_back_to_provider(sms_state, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.eus_sms_service import EusConfig

    c, socket = sms_state()
    jid, wire = await prepare((c, socket), providers=["gateway", "eus"])
    _, controls, _ = outbox.event(c, socket.token_hash, {**wire, "type": "sms.ack"})
    await outbox.notify(controls)
    outbox.event(
        c,
        socket.token_hash,
        {**wire, "type": "sms.result", "ok": False, "outcome": "failed", "not_sent": True, "attempted": False},
    )
    sender = AsyncMock()
    monkeypatch.setattr("app.services.sms_service.send_via_eus", sender)
    ctx = SmsContext(1, ["gateway", "eus"], EusConfig(1, "https://test.invalid", "basic", "id", "secret", 1))
    await outbox.drive(1, jid, ctx)
    assert outbox.snapshot(1, jid)["status"] == "sent"
    assert outbox.snapshot(1, jid)["generation"] == 2
    sender.assert_awaited_once()


async def test_retention_removes_payload_but_preserves_nonrepeatable_tombstone(sms_state):
    from app.services.sms_log_retention import purge_sms_logs_for_org

    sms_state(reply=True)
    result = await send_sms(1, "+43660000", "Secret Alarm", ctx=SmsContext(1, ["gateway"], None), job_id="retain-once")
    assert result
    purge_sms_logs_for_org(1, retention_days=1, retention_max_count=0, now=presence.utc() + timedelta(days=2))
    with outbox.database() as db:
        row = db.query(Job).filter(Job.org_id == 1, Job.id == result.job_id).one()
        assert row.phone == row.text == ""
        assert len(row.payload_hash) == 64
    repeated = await send_sms(
        1, "+43660000", "Secret Alarm", ctx=SmsContext(1, ["gateway"], None), job_id="retain-once"
    )
    assert repeated and repeated.job_id == result.job_id
    with outbox.database() as db:
        assert db.query(Attempt).filter(Attempt.org_id == 1, Attempt.job_id == result.job_id).count() == 1
