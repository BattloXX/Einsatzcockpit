"""SMS-Auswahl und Failover ueber echte gemeinsame Presence statt Socketlisten."""

import asyncio

import pytest

from app.routers import ws
from tests.gateway_helpers import CaptureSocket, bind, cleanup

pytestmark = pytest.mark.usefixtures("sms_outbox_state")


@pytest.fixture
def gateways():
    connections = []
    ws._sms_last_gateway.clear()

    def create(org_id=1, *, priority=100, reply=True, dead=False):
        connection, socket = bind(org_id=org_id, priority=priority, socket=CaptureSocket(reply=reply, dead=dead))
        connections.append(connection)
        return connection, socket

    yield create
    for connection in connections:
        cleanup(connection)
    ws._sms_last_gateway.clear()


async def test_dispatch_no_gateway_raises():
    with pytest.raises(RuntimeError, match="Kein SMS-Gateway"):
        await ws.dispatch_sms(123456, "job-x", "+43660000", "hi")


async def test_legacy_dead_socket_is_not_blindly_retried_on_live_gateway(gateways):
    live, socket = gateways()
    dead, _ = gateways(dead=True)
    with pytest.raises(RuntimeError, match="Timeout oder unklarer"):
        await ws.dispatch_sms(1, "job-prune", "+43660111", "hallo", timeout=0.1)
    assert not socket.sent  # Ein fehlgeschlagener Socket-Send beweist keinen Nichtversand.
    assert dead.gateway_id not in ws.connected_gateway_token_ids(1)
    assert live.gateway_id in ws.connected_gateway_token_ids(1)


async def test_dispatch_dead_socket_never_reports_success(gateways):
    gateways(dead=True)
    gateways(dead=True)
    with pytest.raises(RuntimeError, match="Timeout oder unklarer"):
        await ws.dispatch_sms(1, "job-alldead", "+43660222", "x", timeout=0.1)


async def test_dispatch_timeout_is_not_retried(gateways):
    a, sa = gateways(reply=False)
    b, sb = gateways(reply=False)
    with pytest.raises(RuntimeError, match="Timeout"):
        await ws.dispatch_sms(1, "job-timeout", "+43660333", "x", timeout=0.15)
    assert len(sa.sent) + len(sb.sent) == 1
    assert ws.connected_gateway_token_ids(1) == {a.gateway_id, b.gateway_id}


async def test_dispatch_respects_configured_priority(gateways):
    a, sa = gateways(priority=10)
    _b, sb = gateways(priority=200)
    result = await ws.dispatch_sms(1, "job-priority", "+43660444", "x", timeout=2)
    assert result["gateway_token_id"] == a.gateway_id
    assert sa.sent and not sb.sent


async def test_direct_sms_rotate_over_all_gateways(gateways):
    entries = [gateways(priority=i) for i in range(5)]
    selected = [
        (await ws.dispatch_sms(1, f"job-{i}", "+436601234567", "Code", timeout=2))["gateway_token_id"]
        for i in range(10)
    ]
    assert selected == [c.gateway_id for c, _ in entries] * 2
    assert all(len(socket.sent) == 2 for _, socket in entries)


async def test_explicit_gateway_selection_stays_in_effect(gateways):
    a, _ = gateways(priority=10)
    b, _ = gateways(priority=20)
    selected = []
    for i, preferred in enumerate([b.gateway_id, b.gateway_id, None, None]):
        result = await ws.dispatch_sms(
            1, f"job-{i}", "+436601234567", "Code", timeout=2, preferred_gateway_token_id=preferred
        )
        selected.append(result["gateway_token_id"])
    assert selected == [b.gateway_id, b.gateway_id, a.gateway_id, b.gateway_id]


async def test_reconnected_gateway_has_only_one_rotation_slot(gateways):
    from app.services import gateway_routing as routing

    a, old = gateways(priority=10)
    b, _ = gateways(priority=20)
    newer = routing.begin("sms", 1, a.gateway_id, old.token_hash)
    socket = CaptureSocket(reply=True)
    socket.connection, socket.token_hash = newer, old.token_hash
    routing.attach(newer, old.token_hash, socket)
    try:
        results = [await ws.dispatch_sms(1, f"job-{i}", "+436601234567", "x", timeout=2) for i in range(4)]
        assert [r["gateway_token_id"] for r in results] == [a.gateway_id, b.gateway_id] * 2
        assert not old.sent and len(socket.sent) == 2
    finally:
        routing.detach(newer)


async def test_parallel_sms_rotate_independently_per_org(gateways):
    a, _ = gateways(1, priority=10)
    b, _ = gateways(1, priority=20)
    c, _ = gateways(2)
    result = await asyncio.gather(
        *(ws.dispatch_sms(org, f"job-{i}", "+43660000", "x", timeout=2) for i, org in enumerate([1, 1, 2, 2]))
    )
    assert sorted(r["gateway_token_id"] for r in result[:2]) == sorted([a.gateway_id, b.gateway_id])
    assert [r["gateway_token_id"] for r in result[2:]] == [c.gateway_id, c.gateway_id]
