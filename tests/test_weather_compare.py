"""Vollständigkeit je Variable, identische Zeitfenster und Org-getrennte Ergebnisse."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import kachelmann_service
from app.services import weather_compare as wc
from app.services import weather_service as ws

START = datetime(2026, 10, 22, 10, tzinfo=UTC)


def forecast(rain=1., gust=10.):
    times = [START + timedelta(hours=i) for i in range(1, 7)]
    return ws.ForecastResult([], precipitation_hourly=[(t, rain) for t in times],
                             gust_hourly=[(t, gust) for t in times])


def test_complete_per_variable_differences_and_reasons(monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_COMPARE_WARN_PRECIP_MM", 5)
    monkeypatch.setattr(settings, "WEATHER_COMPARE_WARN_GUST_MS", 5)
    incomplete = forecast()
    incomplete.precipitation_hourly.pop(2)
    incomplete.gust_hourly[0] = (START + timedelta(hours=1), None)
    rain_only = forecast(2)
    rain_only.gust_hourly = []
    result = wc.evaluate({"a": forecast(), "b": forecast(3, 16), "gap": incomplete,
                          "rain": rain_only, "off": "Nicht konfiguriert"}, START, 6)
    assert [p["provider"] for p in result["providers"]] == ["a", "b", "rain"]
    assert result["differences"] == {"precipitation_mm": 12, "gust_max_ms": 6}
    assert "Niederschlag" in result["deviation_hint"] and "Böenmaximum" in result["deviation_hint"]
    assert result["window_start"] == START
    assert result["window_end"] == START + timedelta(hours=6)
    assert len(result["excluded"]) == 4
    assert "unvollständig" in str(result["excluded"])
    assert "probability" not in str(result) and "accuracy" not in str(result)


def test_threshold_strict_and_no_difference_for_single_provider(monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_COMPARE_WARN_PRECIP_MM", 6)
    monkeypatch.setattr(settings, "WEATHER_COMPARE_WARN_GUST_MS", 5)
    result = wc.evaluate({"a": forecast(0, 0), "b": forecast(1, 5)}, START, 6)
    assert result["deviation_hint"] is None
    assert result["providers"][0]["gust_max_ms"] == 0
    single = wc.evaluate({"a": forecast()}, START, 6)
    assert all(v is None for v in single["differences"].values())


def test_shifted_or_duplicate_valid_at_not_comparable():
    shifted = forecast()
    shifted.precipitation_hourly = [(t + timedelta(hours=1), v) for t, v in shifted.precipitation_hourly]
    shifted.gust_hourly.append(shifted.gust_hourly[0])
    assert wc.evaluate({"shifted": shifted}, START, 6)["providers"] == []


@pytest.mark.asyncio
async def test_cache_single_flight_and_org_isolation(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(kachelmann_service, "is_configured", lambda org_id: org_id == 123)
    fetch = AsyncMock(return_value={"test": forecast()})
    monkeypatch.setattr(wc, "_fetch", fetch)
    ws._cache.clear()
    results = await asyncio.gather(*(wc.compare(47.4, 9.7, 6, 123) for _ in range(3)))
    assert results[0] == results[1]
    assert fetch.await_count == 1
    await wc.compare(47.4, 9.7, 6, 456)
    assert fetch.await_count == 2
    assert fetch.await_args.args[-1] == 456


@pytest.mark.asyncio
async def test_fail_open_and_kachelmann_only_configured(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(kachelmann_service, "is_configured", lambda org_id: False)
    kachelmann = AsyncMock()
    monkeypatch.setattr(kachelmann_service, "fetch_forecast", kachelmann)
    monkeypatch.setattr(ws, "_fetch_geosphere_nwp", AsyncMock(side_effect=RuntimeError("secret")))
    om = AsyncMock(return_value=None)
    monkeypatch.setattr(ws, "_openmeteo_forecast", om)
    result = await wc._fetch(47.4, 9.7, 6, 123)
    assert result["geosphere"] is None
    assert "nicht konfiguriert" in result["kachelmann"]
    kachelmann.assert_not_called()
    assert {c.kwargs["model"] for c in om.await_args_list} == {"geosphere_seamless", "best_match"}
    ws._cache.clear()
    monkeypatch.setattr(wc, "_fetch", AsyncMock(side_effect=RuntimeError("secret")))
    result = await wc.compare(47.4, 9.7, 6, 123)
    assert "secret" not in str(result)
    assert result["excluded"]


@pytest.mark.asyncio
async def test_warning_parser_preserves_only_supplied_areas(monkeypatch):
    import httpx

    now = datetime.now(UTC)
    polygon = {"type": "MultiPolygon", "coordinates": [[[[9, 47], [10, 47], [10, 48], [9, 47]]]]}
    payload = {"properties": {"warnings": [{"geometry": polygon, "properties": {
        "rawinfo": {"start": now.timestamp(), "end": (now + timedelta(hours=1)).timestamp()}}}]}}
    original = httpx.AsyncClient
    monkeypatch.setattr(ws.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), **kwargs))
    result = await ws._fetch_geosphere_warnings(47.4, 9.7)
    assert result[0].geometry == polygon
    del payload['properties']['warnings'][0]['geometry']
    result = await ws._fetch_geosphere_warnings(47.4, 9.7)
    assert result[0].geometry is None


def test_partial_hours_on_different_grid_are_excluded():
    shifted = forecast()
    for name in ('precipitation_hourly', 'gust_hourly'):
        series = getattr(shifted, name)
        setattr(shifted, name, [(t - timedelta(minutes=30), v) for t, v in series]
                + [(START + timedelta(hours=6, minutes=30), 1)])
    assert wc.evaluate({'shifted': shifted}, START, 6)['providers'] == []
