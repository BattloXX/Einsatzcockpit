"""Adaptive LIS scheduling, failure pauses and transport defaults."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.dienst_monitor import DienstStatus
from app.models.incident import Incident
from app.models.lis import OrgLisConfig
from app.models.master import OrgSettings
from app.services.lis import lis_health, lis_loop
from app.services.lis.lis_client import LisClient, LisClientError
from tests.conftest import TestingSession


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(lis_loop, "monotonic", lambda: now[0])
    monkeypatch.setattr(lis_loop, "jittered", lambda seconds, pct: seconds)
    return now


@pytest.fixture
def config_id(setup_db):
    with TestingSession() as db:
        set_tenant_context(db, None)
        config = OrgLisConfig(
            org_id=1, enabled=True, base_url="https://lis.invalid", site="LIS",
            username="user", password_enc="cipher", organization_id="guid", poll_interval_seconds=30,
        )
        db.add(config)
        db.commit()
        yield config.id
        db.delete(config)
        db.commit()


def test_not_due_skips_db_thread(monkeypatch, clock):
    io = AsyncMock()
    monkeypatch.setattr("app.services.loop_thread_io.im_db_thread", io)
    lis_loop._next_poll_at[1] = 101
    asyncio.run(lis_loop._sync_one_org(1, 1))
    io.assert_not_awaited()


def test_startup_idle_activity_window_and_reset(clock):
    lis_loop._schedule_success(1, False, 30, clock[0])
    assert lis_loop._next_poll_at[1] == 130
    clock[0] = 161
    lis_loop._backoffs[1].failure()
    lis_loop._schedule_success(1, False, 30, clock[0])
    assert lis_loop._next_poll_at[1] == 221
    assert lis_loop._backoffs[1].delay() == 0
    clock[0] = 221
    lis_loop._schedule_success(1, True, 30, clock[0])
    assert lis_loop._next_poll_at[1] == 251
    clock[0] += settings.LIS_ACTIVE_WINDOW_S - 1
    lis_loop._schedule_success(1, False, 30, clock[0])
    assert lis_loop._next_poll_at[1] == clock[0] + 30
    clock[0] += 1
    lis_loop._schedule_success(1, False, 90, clock[0])
    assert lis_loop._next_poll_at[1] == clock[0] + 90


@pytest.mark.parametrize("failure", [None, LisClientError("login failed")])
def test_failure_pauses_client_rebuild_and_success_resets(monkeypatch, clock, config_id, failure):
    sync = AsyncMock(side_effect=[failure, failure, False])
    client = AsyncMock()
    get_client = AsyncMock(return_value=client)
    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "secret")
    monkeypatch.setattr("app.services.lis.lis_sync.sync_organization", sync)
    # Keep the real client cache to verify closing and delayed reconstruction.
    monkeypatch.setattr("app.services.lis.lis_client.LisClient", lambda *args, **kwargs: client)
    original = lis_loop._get_client

    async def tracked_client(*args):
        await get_client(*args)
        return await original(*args)

    monkeypatch.setattr(lis_loop, "_get_client", tracked_client)
    with pytest.raises(LisClientError):
        asyncio.run(lis_loop._sync_one_org(1, config_id))
    assert lis_loop._next_poll_at[1] == 110
    assert 1 not in lis_loop._org_clients
    client.aclose.assert_awaited_once()
    clock[0] = 105
    asyncio.run(lis_loop._sync_one_org(1, config_id))
    assert get_client.await_count == 1
    clock[0] = 110
    with pytest.raises(LisClientError):
        asyncio.run(lis_loop._sync_one_org(1, config_id))
    assert lis_loop._next_poll_at[1] == 140
    clock[0] = 140
    asyncio.run(lis_loop._sync_one_org(1, config_id))
    assert lis_loop._backoffs[1].delay() == 0
    assert lis_loop._next_poll_at[1] == 170


def test_existing_active_incident_keeps_active_interval(monkeypatch, clock, config_id):
    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "secret")
    monkeypatch.setattr("app.services.lis.lis_sync.sync_organization", AsyncMock(return_value=False))
    monkeypatch.setattr(lis_loop, "_get_client", AsyncMock(return_value=object()))
    lis_loop._startup_active_until[1] = 0
    with TestingSession() as db:
        set_tenant_context(db, None)
        incident = Incident(primary_org_id=1, alarm_type_code="T1", status="active", lis_operation_id="op")
        db.add(incident)
        db.commit()
        try:
            asyncio.run(lis_loop._sync_one_org(1, config_id))
            assert lis_loop._next_poll_at[1] == 130
            assert 1 not in lis_loop._last_activity_at
        finally:
            db.delete(incident)
            db.commit()


def test_health_accepts_idle_jitter_and_rejects_stale(config_id, monkeypatch):
    monkeypatch.setattr(settings, "LIS_POLL_IDLE_INTERVAL_S", 100)
    monkeypatch.setattr(settings, "POLL_JITTER_PCT", 0.2)
    incident = Incident(lis_operation_id="op")
    with TestingSession() as db:
        set_tenant_context(db, None)
        lis_health._last_ok[1] = datetime.now(UTC) - timedelta(seconds=359)
        assert lis_health.lis_delivers_for(db, 1, incident)
        lis_health._last_ok[1] = datetime.now(UTC) - timedelta(seconds=361)
        assert not lis_health.lis_delivers_for(db, 1, incident)


@pytest.mark.parametrize("download", [False, True])
def test_user_agent_on_both_http_client_paths(monkeypatch, download):
    monkeypatch.setattr(settings, "LIS_USER_AGENT", "Einsatzcockpit/test-lis")
    real_client = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(500, content=b"<Envelope><Fault><faultstring>test</faultstring></Fault></Envelope>")

    def transport_client(**kwargs):
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", transport_client)

    async def run():
        client = LisClient("https://lis.invalid", "LIS", "user", "secret")
        client._logged_in = True
        try:
            with pytest.raises(LisClientError):
                if download:
                    await client.download_document("attachment")
                else:
                    await client._post("https://lis.invalid", "action", "<body/>")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert requests[0].headers["User-Agent"] == settings.LIS_USER_AGENT


def test_monitor_accepts_idle_jitter_and_detects_stale(config_id, monkeypatch):
    from app.services.dienst_monitor_service import pruefe_dienste

    monkeypatch.setattr(settings, "LIS_POLL_IDLE_INTERVAL_S", 100)
    monkeypatch.setattr(settings, "POLL_JITTER_PCT", 0.2)
    now = datetime.now(UTC).replace(tzinfo=None)
    with TestingSession() as db:
        set_tenant_context(db, None)
        org_settings = db.query(OrgSettings).filter_by(org_id=1).first()
        if org_settings is None:
            org_settings = OrgSettings(org_id=1)
            db.add(org_settings)
        org_settings.dienst_monitor_ausgeschaltet = ""
        status = DienstStatus(org_id=1, key="alarm_lis", last_probe_at=now - timedelta(seconds=359),
                              last_probe_ok=True)
        db.add(status)
        db.flush()
        try:
            assert next(c for c in pruefe_dienste(db, 1, now) if c.key == "alarm_lis").state == "ok"
            status.last_probe_at = now - timedelta(seconds=361)
            db.flush()
            assert next(c for c in pruefe_dienste(db, 1, now) if c.key == "alarm_lis").state == "down"
        finally:
            db.rollback()


def test_delivered_events_switch_idle_to_active(monkeypatch, clock, config_id):
    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "secret")
    monkeypatch.setattr("app.services.lis.lis_sync.sync_organization", AsyncMock(return_value=True))
    monkeypatch.setattr(lis_loop, "_get_client", AsyncMock(return_value=object()))
    lis_loop._startup_active_until[1] = 0
    asyncio.run(lis_loop._sync_one_org(1, config_id))
    assert lis_loop._next_poll_at[1] == 130
    assert lis_loop._last_activity_at[1] == clock[0]
