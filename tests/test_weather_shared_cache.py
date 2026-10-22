"""Redis, Codec und Deduplizierung mit getrennten lokalen Worker-Caches."""
import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.config import settings
from app.services import shared_cache as sc
from app.services import weather_health as health
from app.services import weather_service as ws


@pytest.fixture
def redis(monkeypatch):
    connection = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(sc, "client", lambda: connection)
    ws._cache.clear()
    yield connection
    ws._cache.clear()


def samples():
    now = datetime.now(UTC)
    meta = ws.DataMeta("geosphere", None, 47, 9, now, now, now, "forecast")
    return [
        ws.CurrentWeather(temperature_c=4, meta=meta),
        ws.ForecastResult([ws.ForecastHorizon(3, gust_max_at=now)], meta=meta,
                          precipitation_hourly=[(now, None), (now, 2.5)]),
        ws.NowcastResult([ws.NowcastStep(now, 1)], 1, now, 1, "stable", "geosphere", meta),
        ws.DailyForecast([ws.DailyForecastDay("Mo", 5)], meta=meta),
        ws.WarningsResult("ok_with_warnings", [ws.WeatherWarning(2, "rain", "Regen", now, now)], now, now),
    ]


@pytest.mark.parametrize("value", samples())
def test_codec_roundtrip(value):
    restored = sc.loads(sc.dumps(value))
    assert restored == value
    assert type(restored) is type(value)


def test_codec_rejects_unsafe_types_and_naive_time():
    with pytest.raises(KeyError):
        sc.loads('{"@class":"Popen","fields":{}}')
    with pytest.raises(ValueError):
        sc.loads('{"@datetime":"2026-01-01T00:00:00"}')
    with pytest.raises(ValueError):
        sc.dumps(datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None))


async def test_hit_between_workers_and_freshness(redis, monkeypatch):
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere"])
    fetch = AsyncMock(return_value=ws.CurrentWeather(temperature_c=7))
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", fetch)
    first = await ws.get_current(47, 9)
    ws._cache.clear()  # Zweiter Worker hat keinen lokalen Cache.
    second = await ws.get_current(47, 9)
    assert second == first
    assert second is not first
    assert second.meta.fetched_at == first.meta.fetched_at
    assert fetch.await_count == 1


async def test_distributed_lock_deduplicates(redis):
    calls = 0

    async def worker():
        nonlocal calls
        async with sc.lock("same"):
            result = await sc.get("same")
            if result is None:
                calls += 1
                await asyncio.sleep(0.08)
                result = ws.CurrentWeather(temperature_c=4)
                await sc.set("same", result, 30)
            return result

    results = await asyncio.gather(worker(), worker())
    assert results[0] == results[1]
    assert calls == 1
    assert not await redis.exists(sc.key_for("same", "lock"))


async def test_lock_release_does_not_delete_new_owner(redis):
    async with sc.lock("owner"):
        await redis.set(sc.key_for("owner", "lock"), "new-owner")
    assert await redis.get(sc.key_for("owner", "lock")) == "new-owner"


async def test_redis_down_falls_back(monkeypatch, caplog):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://secret:password@invalid")
    monkeypatch.setattr(sc, "client", lambda: (_ for _ in ()).throw(OSError("secret")))
    monkeypatch.setattr(sc, "_last_error", -float("inf"))
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere"])
    fetch = AsyncMock(return_value=ws.CurrentWeather(temperature_c=8))
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", fetch)
    ws._cache.clear()
    with caplog.at_level(logging.WARNING):
        assert (await ws.get_current(48, 10)).temperature_c == 8
        assert (await ws.get_current(48, 10)).temperature_c == 8
    assert fetch.await_count == 1
    assert sum("Wetter-Redis" in r.message for r in caplog.records) == 1
    assert "secret" not in caplog.text
    ws._cache.clear()


async def test_ttl_expiry(redis):
    await sc.set("expire", ws.CurrentWeather(), 1)
    assert await sc.get("expire") is not None
    await asyncio.sleep(1.05)
    assert await sc.get("expire") is None


async def test_kachelmann_org_isolation(redis, monkeypatch):
    from app.services import kachelmann_service as ks
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["kachelmann"])
    fetch = AsyncMock(side_effect=[ws.CurrentWeather(temperature_c=1), ws.CurrentWeather(temperature_c=2)])
    monkeypatch.setattr(ks, "fetch_current", fetch)
    assert (await ws.get_current(47, 9, org_id=1)).temperature_c == 1
    assert (await ws.get_current(47, 9, org_id=2)).temperature_c == 2
    ws._cache.clear()
    assert (await ws.get_current(47, 9, org_id=1)).temperature_c == 1
    assert fetch.await_count == 2
    assert all(key.startswith("wx:") for key in await redis.keys("*"))
    assert "password" not in repr(await redis.keys("*"))


async def test_health_shared_atomic_counters(redis, monkeypatch):
    monkeypatch.setattr(health, "_health", {})
    await asyncio.gather(*(health.record("geosphere", True, 12) for _ in range(4)))
    await health.record("geosphere", False, 15, "https://secret?api_key=password")
    health._health.clear()  # Diagnose auch aus einem anderen Worker abrufbar.
    row = (await health.provider_health())["geosphere"]
    assert row["success_count"] == 4
    assert row["error_count"] == 1
    assert row["last_success_at"] and row["last_error_at"]
    assert row["last_error"] == "provider_error"
    assert row["last_latency_ms"] == 15
    assert await redis.ttl("wx:health:v1:geosphere") > 0


async def test_health_local_and_bounded(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(health, "_health", {})
    await health.record("kachelmann", True, 5)
    await health.record("kachelmann", False, 6)
    for index in range(100):
        await health.record(str(index), False, 1)
    rows = await health.provider_health()
    assert len(rows) == 1
    assert rows["kachelmann"]["success_count"] == rows["kachelmann"]["error_count"] == 1


async def test_service_lock_between_independent_workers(redis, monkeypatch):
    from contextvars import ContextVar

    local_cache = ContextVar("worker_cache")

    class WorkerLocks:
        def setdefault(self, *args):
            return {}  # Jeder Aufruf simuliert einen anderen Worker mit eigenen Locks.

    monkeypatch.setattr(ws, "_flights", WorkerLocks())
    monkeypatch.setattr(ws, "_cache_get", lambda key: local_cache.get().get(key))
    monkeypatch.setattr(ws, "_cache_set", lambda key, value, ttl: local_cache.get().__setitem__(key, value))
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere"])

    async def upstream(*args):
        await asyncio.sleep(0.08)
        return ws.CurrentWeather(temperature_c=12)

    fetch = AsyncMock(side_effect=upstream)
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", fetch)

    async def worker(org_id):
        token = local_cache.set({})
        try:
            return await ws.get_current(47.0, 9.0, org_id=org_id)
        finally:
            local_cache.reset(token)

    # Ohne Kachelmann teilen auch verschiedene Organisationen dieselben Rohdaten.
    first, second = await asyncio.gather(worker(1), worker(2))
    assert first == second
    assert fetch.await_count == 1


async def test_lock_wait_is_bounded(redis, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_LOCK_TTL_S", 0.05)
    await redis.set(sc.key_for("busy", "lock"), "other", ex=30)
    with pytest.raises(sc.LockTimeout):
        async with sc.lock("busy"):
            pytest.fail("Belegte Sperre darf keinen parallelen Fetch starten")
    assert await redis.get(sc.key_for("busy", "lock")) == "other"


async def test_native_redis_timeout_is_fail_open(monkeypatch):
    monkeypatch.setattr(sc, "client", lambda: (_ for _ in ()).throw(TimeoutError("password")))
    async with sc.lock("timeout"):
        assert await sc.get("timeout") is None


async def test_lock_cancellation_releases_owner(redis):
    entered = asyncio.Event()

    async def worker():
        async with sc.lock("cancel"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await redis.exists(sc.key_for("cancel", "lock"))


async def test_disabled_redis_does_not_construct_client(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    construct = AsyncMock(side_effect=AssertionError("Redis darf nicht erzeugt werden"))
    monkeypatch.setattr(sc.Redis, "from_url", construct)
    assert await sc.get("disabled") is None
    await sc.set("disabled", ws.CurrentWeather(), 30)
    async with sc.lock("disabled"):
        pass
    construct.assert_not_called()


async def test_redis_hit_preserves_remaining_ttl_and_sync_reader(redis, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere"])
    monkeypatch.setattr(settings, "WEATHER_MAX_AGE_NOWCAST_S", 100)
    now = datetime.now(UTC)
    meta = ws.DataMeta("geosphere", None, 47.0, 9.0, None, now, now - timedelta(seconds=80), "nowcast")
    value = ws.NowcastResult([], 0, None, 0, "stable", "geosphere", meta)
    key = ws._cache_key(ws._provider_resource("nowcast", "nowcast", ["geosphere"]), 47.0, 9.0)
    await sc.set(key, value, 30)
    await redis.pexpire(sc.key_for(key), 5000)
    fetch = AsyncMock(side_effect=AssertionError("Cache-Treffer darf keinen Provider aufrufen"))
    monkeypatch.setattr(ws, "_fetch_geosphere_nowcast", fetch)
    cached = await ws.get_nowcast(47.0, 9.0)
    assert cached.meta.fetched_at == meta.fetched_at
    assert cached.meta.freshness == "aging"
    assert ws.get_cached_nowcast(47.0, 9.0) is cached
    remaining = ws._cache[key][1] - datetime.now(UTC).timestamp()
    assert 0 < remaining <= 5
    fetch.assert_not_called()


async def test_warning_failure_does_not_cache_exception_secrets(redis, monkeypatch, caplog):
    import httpx

    monkeypatch.setattr(ws, "_warnings_enabled", lambda _: True)
    monkeypatch.setattr(ws, "_fetch_geosphere_warnings", AsyncMock(side_effect=httpx.ReadTimeout(
        "https://weather.test?api_key=private-key")))
    with caplog.at_level(logging.WARNING):
        result = await ws.get_warnings_result(47.0, 9.0)
    assert result.status == "unavailable"
    assert result.error == "ReadTimeout"
    assert "private-key" not in caplog.text
    for key in await redis.keys("wx:cache:*"):
        assert "private-key" not in await redis.get(key)


async def test_forecast_kachelmann_org_isolation(redis, monkeypatch):
    from app.services import kachelmann_service as ks

    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["kachelmann"])
    fetch = AsyncMock(side_effect=[ws.ForecastResult([ws.ForecastHorizon(3, temperature_c=1)]),
                                  ws.ForecastResult([ws.ForecastHorizon(3, temperature_c=2)])])
    monkeypatch.setattr(ks, "fetch_forecast", fetch)
    assert (await ws.get_forecast(47.0, 9.0, org_id=1)).horizons[0].temperature_c == 1
    assert (await ws.get_forecast(47.0, 9.0, org_id=2)).horizons[0].temperature_c == 2
    ws._cache.clear()
    assert (await ws.get_forecast(47.0, 9.0, org_id=1)).horizons[0].temperature_c == 1
    assert fetch.await_count == 2


async def test_corrupt_redis_payload_fails_open(redis):
    await redis.setex(sc.key_for("corrupt"), 30, '{"@class":"unknown","fields":{}}')
    assert await sc.get("corrupt") is None
