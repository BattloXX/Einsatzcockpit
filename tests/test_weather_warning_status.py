"""Warnstatus: Ausfall ist keine Entwarnung, Last-Known-Good hat eine Altersgrenze."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.services import weather_service as ws

LAT, LNG = 47.466, 9.745


@pytest.fixture(autouse=True)
def warning_cache(monkeypatch):
    ws._cache.clear()
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(settings, "WEATHER_CACHE_TTL_WARN", 300)
    monkeypatch.setattr(settings, "WEATHER_WARN_STALE_S", 1800)
    monkeypatch.setattr(settings, "WEATHER_WARN_ERROR_TTL_S", 60)
    yield
    ws._cache.clear()


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 10, 22, 10, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(ws, "datetime", Clock)
    return Clock


@pytest.mark.asyncio
async def test_successful_empty_response_differs_from_error(monkeypatch):
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(ws, "_fetch_geosphere_warnings", fetch)
    result = await ws.get_warnings_result(LAT, LNG)
    assert result.status == "ok_no_warnings"
    assert result.fetched_at == result.last_success_at
    assert result.error is None
    ws._cache.clear()
    fetch.side_effect = httpx.ReadTimeout("Timeout")
    result = await ws.get_warnings_result(LAT, LNG)
    assert result.status == "unavailable"
    assert result.warnings == []
    assert result.last_success_at is None
    assert result.error == "Timeout"
    assert await ws.get_warnings(LAT, LNG) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("with_warning", [False, True])
async def test_stale_age_expiry_and_short_negative_cache(monkeypatch, clock, with_warning):
    old = [ws.WeatherWarning(3, "Gewitter", "Test", clock.instant, clock.instant + timedelta(hours=4))]
    fetch = AsyncMock(return_value=old if with_warning else [])
    monkeypatch.setattr(ws, "_fetch_geosphere_warnings", fetch)
    first = await ws.get_warnings_result(LAT, LNG)
    assert first.status == ("ok_with_warnings" if with_warning else "ok_no_warnings")
    clock.instant += timedelta(seconds=301)
    fetch.side_effect = httpx.ReadTimeout("Timeout")
    stale = await ws.get_warnings_result(LAT, LNG)
    assert stale.status == "stale"
    assert stale.warnings == first.warnings
    assert stale.last_success_at == first.fetched_at
    assert (stale.fetched_at - stale.last_success_at).total_seconds() == 301
    assert await ws.get_warnings(LAT, LNG) == first.warnings
    assert fetch.await_count == 2
    clock.instant += timedelta(seconds=59)
    assert (await ws.get_warnings_result(LAT, LNG)).status == "stale"
    assert fetch.await_count == 2
    clock.instant += timedelta(seconds=2)
    await ws.get_warnings_result(LAT, LNG)
    assert fetch.await_count == 3
    # Auch innerhalb eines negativen Cache-Treffers wird die Stale-Grenze neu geprüft.
    clock.instant = first.last_success_at + timedelta(seconds=1800)
    assert (await ws.get_warnings_result(LAT, LNG)).status == "stale"
    clock.instant += timedelta(seconds=1)
    expired = await ws.get_warnings_result(LAT, LNG)
    assert expired.status == "unavailable"
    assert expired.warnings == []
    assert expired.last_success_at == first.last_success_at
    assert await ws.get_warnings(LAT, LNG) == []
    fetch.side_effect = None
    clock.instant += timedelta(seconds=61)
    recovered = await ws.get_warnings_result(LAT, LNG)
    assert recovered.status == first.status
    assert recovered.error is None
    assert recovered.last_success_at == clock.instant


@pytest.mark.asyncio
async def test_disabled_does_not_fetch(monkeypatch):
    fetch = AsyncMock()
    monkeypatch.setattr(ws, "_fetch_geosphere_warnings", fetch)
    monkeypatch.setattr(settings, "WEATHER_ENABLED", False)
    assert (await ws.get_warnings_result(LAT, LNG)).status == "disabled"
    assert await ws.get_warnings(LAT, LNG) == []
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], {"properties": {}}, {"properties": {"warnings": "bad"}},
                                      {"properties": {"warnings": [{"warnstufeid": "bad"}]}}])
async def test_malformed_payload_unavailable(monkeypatch, payload):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://test.invalid"))

    monkeypatch.setattr(ws.httpx, "AsyncClient", lambda **kwargs: Client())
    assert (await ws.get_warnings_result(LAT, LNG)).status == "unavailable"


@pytest.mark.asyncio
async def test_concurrent_warning_requests_fetch_once(monkeypatch):
    async def fetch(*args):
        await asyncio.sleep(0)
        return []

    mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(ws, "_fetch_geosphere_warnings", mock)
    results = await asyncio.gather(*(ws.get_warnings_result(LAT, LNG) for _ in range(8)))
    assert all(r.status == "ok_no_warnings" for r in results)
    mock.assert_awaited_once()
