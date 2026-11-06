"""Adaptive DIBOS-Polls mit kontrollierter Uhr und ohne Netzverkehr."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.config import Settings, settings
from app.services.dibos import dibos_client, dibos_enrich, dibos_loop
from app.services.loop_utils import Backoff, jittered
from tests.test_dibos_loop import _make_config, _session
from tests.test_dienst_monitor_schalter import monitor_orgs as monitor_orgs


@pytest.fixture(autouse=True)
def poll_state(monkeypatch):
    for name in (
        "_org_clients", "_previous_event_numbers", "_last_public_events_at", "_last_enrich_fingerprint",
        "_next_poll_at", "_last_activity_at", "_startup_active_until", "_backoffs", "_last_units_at",
        "_last_events_fingerprint", "_cached_units",
    ):
        monkeypatch.setattr(dibos_loop, name, {})
    monkeypatch.setattr(settings, "POLL_JITTER_PCT", 0.0)
    now = [100.0]
    monkeypatch.setattr(dibos_loop, "monotonic", lambda: now[0])
    yield now
    asyncio.run(dibos_loop._close_all_clients())


@pytest.mark.parametrize("sample, expected", [(0.0, 8.0), (0.5, 10.0), (1.0, 12.0)])
def test_jitter_bounds(sample, expected):
    assert jittered(10, 0.2, random_source=lambda: sample) == expected
    assert jittered(0, 0.2, random_source=lambda: sample) == 0.5
    assert jittered(0.1, 0.2, random_source=lambda: sample) == 0.5


def test_backoff_sequence_and_reset():
    backoff = Backoff(300)
    assert backoff.delay() == 0
    for expected in (10, 30, 60, 120, 300, 300):
        backoff.failure()
        assert backoff.delay() == expected
    backoff.success()
    assert backoff.delay() == 0
    backoff.failure()
    assert backoff.delay() == 10
    capped = Backoff(20)
    capped.failure()
    capped.failure()
    assert capped.delay() == 20


@pytest.mark.parametrize("legacy, active, expected", [(5, 7, 7), (9, 7, 9), (5, 5, 5)])
def test_active_interval_legacy_alias(legacy, active, expected):
    config = Settings(DIBOS_POLL_INTERVAL_S=legacy, DIBOS_POLL_ACTIVE_INTERVAL_S=active)
    assert config.dibos_active_interval() == expected


@pytest.fixture
def client(monkeypatch):
    fake = AsyncMock()
    fake.get_current_events.return_value = []
    fake.get_current_units.return_value = [{"unid": "wache"}]
    fake.get_public_events.return_value = []
    monkeypatch.setattr(dibos_loop, "_get_client", AsyncMock(return_value=fake))
    monkeypatch.setattr(dibos_loop, "_lade_konfiguration_sync", lambda _: ((), False, True, True, None, 120))
    monkeypatch.setattr(dibos_loop, "_has_missing_active_incidents", AsyncMock(return_value=False))
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", AsyncMock(return_value=True))
    import app.services.dienst_monitor_service as monitor
    monkeypatch.setattr(monitor, "record_probe", Mock())
    return fake


def test_startup_idle_active_and_expiry(poll_state, client):
    asyncio.run(dibos_loop._check_org(1, 1))
    assert dibos_loop._next_poll_at[1] == 105
    poll_state[0] = 161
    asyncio.run(dibos_loop._check_org(1, 1))
    assert dibos_loop._next_poll_at[1] == 176
    poll_state[0] = 176
    client.get_current_events.return_value = [{"eventNumber": "new"}]
    asyncio.run(dibos_loop._check_org(1, 1))
    assert dibos_loop._next_poll_at[1] == 181
    assert dibos_loop._last_activity_at[1] == 176
    client.get_current_events.return_value = []
    poll_state[0] = 176 + settings.DIBOS_ACTIVE_WINDOW_S - 1
    asyncio.run(dibos_loop._check_org(1, 1))
    assert dibos_loop._next_poll_at[1] == poll_state[0] + 5
    poll_state[0] += 1
    asyncio.run(dibos_loop._check_org(1, 1))
    assert dibos_loop._next_poll_at[1] == poll_state[0] + 15


def test_probe_call_does_not_activate(poll_state, client):
    dibos_loop._startup_active_until[1] = 0
    client.get_current_events.return_value = [{
        "eventNumber": "probe", "tycod": "f30", "tycodDescription": "Probealarm F30",
    }]
    asyncio.run(dibos_loop._check_org(1, 1))
    assert 1 not in dibos_loop._last_activity_at
    assert dibos_loop._next_poll_at[1] == 115
    client.get_current_units.assert_not_awaited()


def test_org_not_due_is_skipped(poll_state, monkeypatch):
    with _session() as db:
        cfg_id = _make_config(db).id
    checked = AsyncMock()
    monkeypatch.setattr(dibos_loop, "_check_org", checked)
    dibos_loop._next_poll_at[1] = 101
    asyncio.run(dibos_loop._run_all_orgs())
    checked.assert_not_awaited()
    poll_state[0] = 101
    asyncio.run(dibos_loop._run_all_orgs())
    checked.assert_awaited_once_with(1, cfg_id)


@pytest.mark.parametrize("error_type", [dibos_client.DibosClientError, dibos_client.DibosAuthError])
def test_error_delays_next_poll_and_success_resets(poll_state, client, error_type):
    with _session() as db:
        _make_config(db)
    client.get_current_events.side_effect = error_type("401 or transport")
    asyncio.run(dibos_loop._run_all_orgs())
    assert dibos_loop._next_poll_at[1] == 110
    from app.services.dienst_monitor_service import record_probe
    record_probe.assert_called_with(1, "alarm_dibos", False, "401 or transport")
    poll_state[0] = 105
    asyncio.run(dibos_loop._run_all_orgs())
    assert client.get_current_events.await_count == 1
    poll_state[0] = 110
    asyncio.run(dibos_loop._run_all_orgs())
    assert dibos_loop._next_poll_at[1] == 140
    client.get_current_events.side_effect = None
    poll_state[0] = 140
    asyncio.run(dibos_loop._run_all_orgs())
    assert dibos_loop._backoffs[1].delay() == 0
    assert dibos_loop._next_poll_at[1] == 145


def test_units_throttle_and_immediate_changes(poll_state, client):
    client.get_current_events.return_value = [{"eventNumber": "one"}]
    asyncio.run(dibos_loop._check_org(1, 1))
    client.get_current_units.assert_awaited_once()
    # Anlage/Alarmierung geschieht vor der ersten Units-Abfrage.
    assert dibos_enrich.enrich_and_broadcast.await_args_list[0].kwargs["raw_units"] is None
    poll_state[0] = 105
    asyncio.run(dibos_loop._check_org(1, 1))
    assert client.get_current_units.await_count == 1
    poll_state[0] = 120
    asyncio.run(dibos_loop._check_org(1, 1))
    assert client.get_current_units.await_count == 2
    poll_state[0] = 125
    client.get_current_events.return_value = [{"eventNumber": "one", "rowVersion": 2}]
    asyncio.run(dibos_loop._check_org(1, 1))
    assert client.get_current_units.await_count == 3
    poll_state[0] = 130
    client.get_current_events.return_value = [{"eventNumber": "two"}]
    asyncio.run(dibos_loop._check_org(1, 1))
    assert client.get_current_units.await_count == 4


@pytest.mark.parametrize("override", [None, "Einsatzcockpit/test (+https://einsatzcockpit.com)"])
def test_user_agent_default_and_override(monkeypatch, override):
    assert settings.DIBOS_USER_AGENT.startswith("Einsatzcockpit/")
    assert "(+https://einsatzcockpit.com)" in settings.DIBOS_USER_AGENT
    if override is not None:
        monkeypatch.setattr(settings, "DIBOS_USER_AGENT", override)
    client = dibos_client.DibosClient("https://dibos.invalid", "g", "p", "s", "p")
    try:
        assert client._client.headers["User-Agent"] == settings.DIBOS_USER_AGENT
    finally:
        asyncio.run(client.aclose())


def test_user_agent_uses_configured_app_version():
    assert Settings(APP_VERSION="test-version").DIBOS_USER_AGENT == (
        "Einsatzcockpit/test-version (+https://einsatzcockpit.com)"
    )


@pytest.mark.parametrize("idle, age", [(15, 18), (60, 250)])
def test_idle_probe_ok_and_backoff_error_down(monitor_orgs, monkeypatch, idle, age):
    from app.models.dibos import OrgDibosConfig
    from app.models.dienst_monitor import DienstStatus
    from app.services.dienst_monitor_service import pruefe_dienste

    db, (org_id, _) = monitor_orgs
    monkeypatch.setattr(settings, "DIBOS_POLL_IDLE_INTERVAL_S", idle)
    monkeypatch.setattr(settings, "POLL_JITTER_PCT", 0.2)
    db.add(OrgDibosConfig(
        org_id=org_id, enabled=True, base_url="https://dibos.invalid", gateway_user="g",
        gateway_password_enc="cipher", service_user="s", service_password_enc="cipher", create_incidents=True,
        poll_interval_seconds=5,
    ))
    now = datetime.now(UTC).replace(tzinfo=None)
    probe = DienstStatus(org_id=org_id, key="alarm_dibos", last_probe_at=now - timedelta(seconds=age),
                         last_probe_ok=True)
    db.add(probe)
    db.commit()

    def state():
        return next(check.state for check in pruefe_dienste(db, org_id, now) if check.key == "alarm_dibos")

    assert state() == "ok"
    probe.last_probe_at = now - timedelta(seconds=max(180, 4 * idle * 1.2) + 1)
    db.commit()
    assert state() == "down"
    probe.last_probe_at = now
    probe.last_probe_ok = False
    probe.last_probe_error = "Login fehlgeschlagen"
    db.commit()
    assert state() == "down"


def test_loop_keeps_ticking_during_backoff(poll_state, monkeypatch):
    from app.services import background_runtime

    metrics = {}
    ticks = []
    dibos_loop._next_poll_at[1] = 400  # Org pausiert, Loop/Heartbeat laufen weiter.

    async def run_orgs():
        ticks.append(poll_state[0])

    async def sleep(delay):
        assert 0 < delay <= 5
        poll_state[0] += delay
        if poll_state[0] >= 400:
            raise asyncio.CancelledError

    monkeypatch.setattr(dibos_loop, "_run_all_orgs", run_orgs)
    monkeypatch.setattr(dibos_loop.asyncio, "sleep", sleep)
    token = background_runtime._loops.set(metrics)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(dibos_loop.dibos_poll_loop())
    finally:
        background_runtime._loops.reset(token)
    assert len(ticks) == 60
    assert metrics["dibos_poll_loop"]["state"] == "ok"
    assert metrics["dibos_poll_loop"]["interval_s"] == 5
    assert "last_success_at" in metrics["dibos_poll_loop"]
