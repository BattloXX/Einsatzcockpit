"""Metadaten, Frische, Standorttrennung und konservative Szenario-Auswertung."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.routers.ui_weather import _station_current_weather
from app.services import weather_service as ws


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    ws._cache.clear()
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    yield
    ws._cache.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current", "forecast", "nowcast", "daily"])
async def test_metadata_fallback_and_cache_age(monkeypatch, kind):
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere", "open-meteo"]
                        if kind != "daily" else ["open-meteo-geosphere", "open-meteo"])
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", AsyncMock(return_value=None))
    monkeypatch.setattr(ws, "_fetch_geosphere_nowcast", AsyncMock(return_value=None))
    monkeypatch.setattr(ws, "_fetch_geosphere_nwp", AsyncMock(return_value=None))
    result = {"current": ws.CurrentWeather(temperature_c=20),
              "forecast": ws.ForecastResult([]), "nowcast": ws.NowcastResult([], 0, None, 0, "stable", "test"),
              "daily": ws.DailyForecast([])}[kind]
    fetch = AsyncMock(return_value=result)
    async def daily(*args, model=None):
        return None if model else result
    monkeypatch.setattr(ws, "_openmeteo_" + kind, daily if kind == "daily" else fetch)
    name = "get_daily_forecast" if kind == "daily" else "get_" + kind
    get = getattr(ws, name)
    first = await get(47.466, 9.745)
    assert first.meta.provider == "open-meteo"
    assert first.meta.is_fallback
    assert first.meta.lat == 47.466
    assert first.meta.lng == 9.745
    assert first.meta.freshness == "fresh"
    first.meta.fetched_at = datetime.now(UTC) - timedelta(seconds=80)
    fetched = first.meta.fetched_at
    monkeypatch.setattr(settings, "WEATHER_MAX_AGE_" + kind.upper() + "_S", 100)
    cached = await get(47.466, 9.745)
    assert cached.meta.fetched_at == fetched
    assert cached.meta.freshness == "aging"
    monkeypatch.setattr(settings, "WEATHER_MAX_AGE_" + kind.upper() + "_S", 50)
    assert (await get(47.466, 9.745)).meta.freshness == "stale"
    if kind != "daily":
        fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_nearby_locations_do_not_share_cache(monkeypatch):
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere"])
    fetch = AsyncMock(side_effect=[ws.CurrentWeather(temperature_c=1), ws.CurrentWeather(temperature_c=2)])
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", fetch)
    a = await ws.get_current(47.46601, 9.74501)
    b = await ws.get_current(47.46602, 9.74502)
    assert a.temperature_c == 1
    assert b.temperature_c == 2
    assert a.meta.lat != b.meta.lat
    assert fetch.await_count == 2


def test_station_direction_and_rain_rate_semantics():
    current = _station_current_weather([{"online": True, "wind_dir_deg": 225, "rain_rate": 12}])
    assert current.wind_direction_deg == 225
    assert current.precipitation_1h_mm is None
    assert current.precipitation_rate_mmh == 12
    assert current.meta.kind == "measured"


def test_incomplete_forecast_never_creates_hazard():
    forecast = ws.ForecastResult([ws.ForecastHorizon(
        6, precipitation_acc_mm=100, gust_speed_ms=50, complete=False,
    )])
    alerts = ws.analyze_weather(None, forecast)
    assert [a.key for a in alerts] == ["data_quality"]
    assert alerts[0].label_de == "Daten unvollständig"


def test_stale_current_never_creates_hazard():
    current = ws.CurrentWeather(gust_speed_ms=50)
    ws._set_meta(current, "current", 47, 9, "geosphere", ["geosphere"])
    current.meta.fetched_at -= timedelta(days=1)
    assert [a.key for a in ws.analyze_weather(current, None)] == ["data_quality"]


@pytest.mark.asyncio
async def test_current_cache_isolates_org_keys_and_records_primary_source(monkeypatch):
    from app.services import kachelmann_service

    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["kachelmann"])
    fetch = AsyncMock(side_effect=[ws.CurrentWeather(temperature_c=1), ws.CurrentWeather(temperature_c=2)])
    monkeypatch.setattr(kachelmann_service, "fetch_current", fetch)
    a = await ws.get_current(47.466, 9.745, org_id=101)
    b = await ws.get_current(47.466, 9.745, org_id=102)
    assert a.temperature_c == 1
    assert b.temperature_c == 2
    assert not a.meta.is_fallback
    assert not b.meta.is_fallback
    assert await ws.get_current(47.466, 9.745, org_id=101) is a
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_provider_parse_failure_uses_next_source(monkeypatch):
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["geosphere", "open-meteo"])
    monkeypatch.setattr(ws, "_fetch_current_from_nwp", AsyncMock(side_effect=ValueError("Ungültige Daten")))
    monkeypatch.setattr(ws, "_openmeteo_current", AsyncMock(return_value=ws.CurrentWeather(temperature_c=20)))
    result = await ws.get_current(47.466, 9.745)
    assert result.meta.is_fallback
    assert result.meta.provider == "open-meteo"


def test_stale_nowcast_and_forecast_never_create_hazards():
    nowcast = ws.NowcastResult([], 50, None, 100, "increasing", "geosphere")
    forecast = ws.ForecastResult([ws.ForecastHorizon(6, precipitation_acc_mm=100, gust_speed_ms=50)])
    for value, kind in [(nowcast, "nowcast"), (forecast, "forecast")]:
        ws._set_meta(value, kind, 47, 9, "geosphere", ["geosphere"])
        value.meta.fetched_at -= timedelta(days=1)
    assert [a.key for a in ws.analyze_weather(None, forecast, nowcast)] == ["data_quality"]



def test_station_without_measurement_time_never_creates_hazard():
    current = _station_current_weather([{"online": True, "gust": 50}])
    assert [a.key for a in ws.analyze_weather(current, None)] == ["data_quality"]
