"""Kennzahlen und Anzeigehilfen, ohne externe API-Aufrufe."""
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.templating import ms_to_kmh, templates
from app.core.timezones import to_org_tz
from app.services import weather_service as ws
from app.services import weather_thresholds as thresholds
from app.services.weather_alert_service import RULE_DEFAULTS
from app.services.weather_indicators import HOURS, ObservedPrecipitation, build_indicators

REF = datetime(2026, 3, 29, 0, tzinfo=UTC)


def inputs(value=0.0):
    current = ws.CurrentWeather(
        temperature_c=0, gust_speed_ms=value, snowfall_cm=0, precipitation_1h_mm=value,
    )
    forecast = ws.ForecastResult([
        ws.ForecastHorizon(
            h, precipitation_mm=value, gust_max_ms=value, gust_max_at=REF + timedelta(hours=2),
            snowfall_cm=0, temperature_min_c=0,
        ) for h in HOURS
    ], reference_time=REF, precipitation_hourly=[(REF + timedelta(hours=h), value) for h in range(1, 25)])
    observed = {h: (value, True) for h in HOURS}
    return current, forecast, observed, ws.WarningsResult("ok_no_warnings", [])


def test_zero_is_not_missing():
    result = build_indicators(*inputs())
    for field in fields(result):
        scenario = getattr(result, field.name)
        assert scenario.status == "vollständig"
        assert scenario.rating is None
    assert result.storm.current_gust_ms == 0
    assert result.heavy_rain.observed[0].mm == 0
    assert result.heavy_rain.peak_mmh == 0


def test_missing_is_not_zero():
    result = build_indicators(None, None, None, None)
    for field in fields(result):
        scenario = getattr(result, field.name)
        assert scenario.status == "unvollständig"
        assert scenario.rating is None
    assert result.storm.current_gust_ms is None
    assert result.heavy_rain.observed[0].mm is None
    assert result.heavy_rain.peak_mmh is None


@pytest.mark.parametrize("failure", ["complete", "stale_forecast", "stale_current", "missing"])
def test_incomplete_storm_has_no_rating(failure):
    current, forecast, observed, warnings = inputs(50)
    if failure == "complete":
        forecast.horizons[2].complete = False
    elif failure == "missing":
        forecast.horizons[2].gust_max_at = None
    else:
        meta = ws.DataMeta("test", None, None, None, None, REF, REF, "forecast", freshness="stale")
        if failure == "stale_current":
            current.meta = meta
        else:
            forecast.meta = meta
    storm = build_indicators(current, forecast, observed, warnings).storm
    assert storm.status == "unvollständig"
    assert storm.rating is None


@pytest.mark.parametrize("stale", [False, True])
def test_incomplete_observed_has_no_rain_rating(stale):
    current, forecast, observed, warnings = inputs(50)
    if not stale:
        observed[12] = (None, False)
    result = build_indicators(current, forecast, ObservedPrecipitation(
        observed, freshness="stale" if stale else "fresh",
    ), warnings)
    assert result.heavy_rain.status == "unvollständig"
    assert result.heavy_rain.rating is None


def test_gust_maximum_and_peak_keep_actual_time():
    current, forecast, observed, warnings = inputs()
    forecast.horizons[1].gust_max_ms = 30
    forecast.horizons[1].gust_speed_ms = 0
    forecast.precipitation_hourly[4] = (REF + timedelta(hours=5), 42)
    result = build_indicators(current, forecast, observed, warnings)
    assert result.storm.forecast[0].max_ms == 30
    assert result.storm.forecast[0].at == REF + timedelta(hours=2)
    assert result.storm.rating == "mögliche Gefahr"
    assert result.heavy_rain.peak_mmh == 42
    assert result.heavy_rain.peak_at == REF + timedelta(hours=5)


def test_thunder_requires_official_warning_and_flood_never_rates_rain():
    current, forecast, observed, warnings = inputs(50)
    current.weather_code = 95
    for h in forecast.horizons:
        h.weather_code = 99
    result = build_indicators(current, forecast, observed, warnings)
    assert result.thunder.rating is None
    assert result.flood.rating is None
    assert "Pegel" in result.flood.hint
    warnings.warnings = [ws.WeatherWarning(3, "Gewitter", "Test", REF, REF + timedelta(hours=3))]
    assert build_indicators(current, forecast, observed, warnings).thunder.rating == "mögliche Gefahr"
    warnings.status = "stale"
    assert build_indicators(current, forecast, observed, warnings).thunder.rating is None


def test_threshold_move_preserves_values_and_engine():
    expected = {
        "BF8": 17.2, "BF10": 24.5, "WILDFIRE_TEMP": 30, "WILDFIRE_HUM_WARN": 30,
        "WILDFIRE_HUM_DANGER": 20, "WILDFIRE_NO_RAIN": 0.5, "RAIN_NOWCAST_WARN": 15,
        "RAIN_NOWCAST_DANGER": 30, "RAIN_FC6_WARN": 25, "RAIN_FC6_DANGER": 40,
        "SNOW_TEMP_MAX": 1, "SNOW_NOWCAST_WARN": 3, "SNOW_NOWCAST_DANGER": 8,
        "SNOW_FC6_WARN": 8, "SNOW_FC6_DANGER": 15,
        "ICE_TEMP_LOW": -1, "ICE_TEMP_HIGH": 1, "ICE_MIN_RAIN": 0.2,
    }
    for name, value in expected.items():
        assert getattr(thresholds, name) == getattr(ws, "_" + name) == value
    assert RULE_DEFAULTS["sturm"]["vorwarn_gust_ms"] == 17
    assert RULE_DEFAULTS["sturm"]["akut_gust_ms"] == 25


@pytest.mark.parametrize("forecast_gust", [None, 0])
def test_analyze_weather_zero_gust_is_not_replaced_by_wind(forecast_gust):
    current = ws.CurrentWeather(gust_speed_ms=0, wind_speed_ms=40)
    forecast = ws.ForecastResult([ws.ForecastHorizon(6, gust_speed_ms=0, gust_max_ms=forecast_gust,
                                                   wind_speed_ms=40)])
    assert "storm" not in [a.key for a in ws.analyze_weather(current, forecast)]


def test_analyze_weather_prefers_window_totals_and_maximum():
    forecast = ws.ForecastResult([ws.ForecastHorizon(
        6, precipitation_mm=50, precipitation_acc_mm=0, gust_max_ms=30, gust_speed_ms=0,
    )])
    assert {"rain", "storm"} <= {a.key for a in ws.analyze_weather(None, forecast)}


@pytest.mark.parametrize(("value", "expected"), [(None, None), (0, 0), (10, 36)])
def test_ms_to_kmh(value, expected):
    assert ms_to_kmh(value) == expected
    assert templates.env.filters["ms_to_kmh"](value) == expected


@pytest.mark.parametrize(("at", "expected", "offset"), [
    (datetime(2026, 3, 29, 0, 30, tzinfo=UTC), "29.03.2026 01:30", 1),
    (datetime(2026, 3, 29, 1, 30, tzinfo=UTC), "29.03.2026 03:30", 2),
    (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), "25.10.2026 02:30", 2),
    (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), "25.10.2026 02:30", 1),
])
def test_org_timezone_helper_and_template_dst(at, expected, offset):
    org = SimpleNamespace(timezone="Europe/Vienna")
    assert to_org_tz(at, org).utcoffset() == timedelta(hours=offset)
    template = templates.env.from_string("{{ at|local_datetime }}")
    assert template.render(at=at, org=org) == expected
    assert template.render(at=at, org_settings=org) == expected
    assert template.render(at=at, user=SimpleNamespace(org=org)) == expected


def test_timezone_fallback_and_explicit_org(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Europe/Vienna")
    assert to_org_tz(REF).hour == 1
    assert to_org_tz(REF, SimpleNamespace(timezone="invalid")).hour == 1
    template = templates.env.from_string("{{ at|local_datetime }}")
    assert template.render(
        at=REF, org_settings=SimpleNamespace(), org=SimpleNamespace(timezone="UTC"),
    ) == "29.03.2026 00:00"


@pytest.mark.asyncio
async def test_default_forecast_horizons_and_daily_org_cache(monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    ws._cache.clear()
    monkeypatch.setattr(ws, "_provider_order", lambda *args: ["open-meteo"])
    fetch = AsyncMock(return_value=ws.ForecastResult([]))
    daily = AsyncMock(return_value=ws.DailyForecast([]))
    monkeypatch.setattr(ws, "_openmeteo_forecast", fetch)
    monkeypatch.setattr(ws, "_openmeteo_daily", daily)
    try:
        await ws.get_forecast(47, 9)
        fetch.assert_awaited_once_with(47, 9, (1, 3, 6, 12, 24))
        await ws.get_daily_forecast(47, 9, org_id=1)
        await ws.get_daily_forecast(47, 9, org_id=2)
        daily.assert_awaited_once()
    finally:
        ws._cache.clear()


@pytest.mark.parametrize("failure", ["stale", "incomplete", "missing"])
def test_snow_and_ice_incomplete_forecast_never_rates(failure):
    current, forecast, observed, warnings = inputs(50)
    current.snowfall_cm = 30
    if failure == "stale":
        forecast.meta = ws.DataMeta("test", None, None, None, None, REF, REF, "forecast", freshness="stale")
    elif failure == "incomplete":
        forecast.horizons[0].complete = False
    else:
        forecast.horizons[0].temperature_min_c = None
    result = build_indicators(current, forecast, observed, warnings)
    assert result.snow.status == result.ice.status == "unvollständig"
    assert result.snow.rating is result.ice.rating is None


def test_snow_and_ice_possible_danger():
    current, forecast, observed, warnings = inputs(50)
    current.snowfall_cm = 30
    result = build_indicators(current, forecast, observed, warnings)
    assert result.snow.rating == result.ice.rating == "mögliche Gefahr"


def test_peak_is_never_inferred_from_accumulated_rain():
    current, forecast, observed, warnings = inputs(50)
    forecast.precipitation_hourly = []
    rain = build_indicators(current, forecast, observed, warnings).heavy_rain
    assert rain.peak_mmh is rain.peak_at is None
    assert rain.status == "unvollständig"
    assert rain.rating is None
