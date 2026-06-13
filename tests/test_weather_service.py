"""Tests für weather_service.py — GeoSphere-Antworten gemockt."""
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import weather_service
from app.services.weather_service import (
    CurrentWeather,
    ForecastHorizon,
    ForecastResult,
    NowcastResult,
    NowcastStep,
    ScenarioAlert,
    WeatherWarning,
    _cache,
    _cache_key,
    _cache_set,
    _compute_trend,
    _safe_float,
    _wind_dir_from_uv,
    _wind_speed_from_uv,
    analyze_weather,
    get_cached_nowcast,
    get_current,
    get_forecast,
    get_nowcast,
    get_warnings,
    has_cached,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

LAT, LNG = 47.466, 9.745

_BASE = datetime(2024, 6, 13, 10, 0, 0, tzinfo=UTC)
NOWCAST_TIMESTAMPS = [
    (_BASE + timedelta(minutes=15 * i)).isoformat() for i in range(13)
]

NOWCAST_RR = [0.0, 0.1, 0.5, 1.2, 0.8, 0.3, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
NOWCAST_T2M = [15.2] * 13
NOWCAST_FF = [3.0] * 13
NOWCAST_FX = [5.0] * 13

NWP_TIMESTAMPS = [f"2024-06-13T{h:02d}:00:00+00:00" for h in range(24)]

NWP_RR_ACC = [0.0, 0.0, 0.1, 0.3, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0,
              3.5, 3.8, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0,
              4.0, 4.0, 4.0, 4.0]
NWP_U10M = [1.0] * 24
NWP_V10M = [-2.0] * 24
NWP_UGUST = [2.0] * 24
NWP_VGUST = [-4.0] * 24
NWP_T2M = [14.0] * 24


def _make_geosphere_response(params_data: dict, timestamps: list[str]) -> dict:
    """Builds a minimal GeoSphere GeoJSON response."""
    params = {
        name: {"data": data, "unit": "?", "name": name}
        for name, data in params_data.items()
    }
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [LNG, LAT]},
            "properties": {"parameters": params},
        }],
        "timestamps": timestamps,
    }


def _nowcast_response() -> dict:
    return _make_geosphere_response(
        {"rr": NOWCAST_RR, "t2m": NOWCAST_T2M, "ff": NOWCAST_FF, "fx": NOWCAST_FX},
        NOWCAST_TIMESTAMPS,
    )


def _nwp_response() -> dict:
    return _make_geosphere_response(
        {
            "rr_acc": NWP_RR_ACC, "u10m": NWP_U10M, "v10m": NWP_V10M,
            "ugust": NWP_UGUST, "vgust": NWP_VGUST, "t2m": NWP_T2M,
        },
        NWP_TIMESTAMPS,
    )


def _mock_httpx_response(json_data: dict):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(return_value=None)
    mock_resp.json = MagicMock(return_value=json_data)
    return mock_resp


@pytest.fixture(autouse=True)
def clear_weather_cache():
    """Clears the weather cache before each test."""
    _cache.clear()
    yield
    _cache.clear()


# ── Helper function unit tests ─────────────────────────────────────────────────

def test_safe_float_converts_numbers():
    assert _safe_float(1.5) == 1.5
    assert _safe_float("3.0") == 3.0
    assert _safe_float(0) == 0.0


def test_safe_float_returns_none_for_invalid():
    assert _safe_float(None) is None
    assert _safe_float("abc") is None
    assert _safe_float(float("nan")) is None


def test_wind_speed_from_uv():
    speed = _wind_speed_from_uv(3.0, 4.0)
    assert speed == pytest.approx(5.0, rel=1e-3)


def test_wind_speed_from_uv_none():
    assert _wind_speed_from_uv(None, 4.0) is None
    assert _wind_speed_from_uv(3.0, None) is None


def test_wind_dir_from_uv_westerly():
    # u>0, v=0 → wind from west (270°)
    deg = _wind_dir_from_uv(5.0, 0.0)
    assert deg == pytest.approx(270.0, abs=1.0)


def test_wind_dir_from_uv_northerly():
    # u=0, v>0 → wind from south (180°)... wait
    # meteorological: v>0 means wind blowing northward → coming from south → 180°
    deg = _wind_dir_from_uv(0.0, 5.0)
    assert deg == pytest.approx(180.0, abs=1.0)


def test_wind_dir_from_uv_none():
    assert _wind_dir_from_uv(None, 5.0) is None


def test_compute_trend_increasing():
    values = [0.0, 0.1, 0.1, 0.2, 0.5, 0.8, 1.0, 1.2]
    assert _compute_trend(values) == "increasing"


def test_compute_trend_decreasing():
    values = [1.2, 1.0, 0.8, 0.5, 0.2, 0.1, 0.1, 0.0]
    assert _compute_trend(values) == "decreasing"


def test_compute_trend_stable():
    values = [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2]
    assert _compute_trend(values) == "stable"


def test_compute_trend_too_short():
    assert _compute_trend([0.1, 0.2]) == "stable"


# ── Cache tests ────────────────────────────────────────────────────────────────

def test_has_cached_false_when_empty():
    assert has_cached(LAT, LNG) is False


def test_has_cached_true_after_set():
    dummy = NowcastResult(steps=[], peak_mm=0, peak_at=None, total_mm=0, trend="stable", source="x")
    _cache_set(_cache_key("nowcast", LAT, LNG), dummy, ttl=300)
    assert has_cached(LAT, LNG) is True


def test_has_cached_false_after_expiry():
    dummy = NowcastResult(steps=[], peak_mm=0, peak_at=None, total_mm=0, trend="stable", source="x")
    # TTL = 0 → already expired
    key = _cache_key("nowcast", LAT, LNG)
    _cache[key] = (dummy, datetime.now(UTC).timestamp() - 1)
    assert has_cached(LAT, LNG) is False


def test_get_cached_nowcast_returns_none_when_empty():
    assert get_cached_nowcast(LAT, LNG) is None


def test_get_cached_nowcast_returns_cached():
    dummy = NowcastResult(steps=[], peak_mm=0, peak_at=None, total_mm=0, trend="stable", source="x")
    _cache_set(_cache_key("nowcast", LAT, LNG), dummy, ttl=300)
    assert get_cached_nowcast(LAT, LNG) is dummy


# ── get_nowcast ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_nowcast_parses_geosphere_response():
    mock_resp = _mock_httpx_response(_nowcast_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_nowcast(LAT, LNG)

    assert result is not None
    assert result.source == "geosphere"
    assert len(result.steps) == 13
    assert result.steps[0].precipitation_mm == pytest.approx(0.0)
    assert result.steps[3].precipitation_mm == pytest.approx(1.2)
    assert result.steps[0].temperature_c == pytest.approx(15.2)
    assert result.peak_mm == pytest.approx(1.2)
    assert result.total_mm == pytest.approx(sum(NOWCAST_RR))


@pytest.mark.asyncio
async def test_get_nowcast_trend_increasing():
    mock_resp = _mock_httpx_response(_nowcast_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_nowcast(LAT, LNG)

    assert result is not None
    # RR rises from 0 to 1.2 then falls back — first half low, mid peak
    # actual trend depends on values: [0, 0.1, 0.5, 1.2, 0.8, 0.3, 0.1, 0, 0, 0, 0, 0, 0]
    # first 6: avg = (0+0.1+0.5+1.2+0.8+0.3)/6 = 0.48
    # last 7: avg = (0.1+0+0+0+0+0+0)/7 ≈ 0.014 → decreasing
    assert result.trend == "decreasing"


@pytest.mark.asyncio
async def test_get_nowcast_caches_result():
    mock_resp = _mock_httpx_response(_nowcast_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result1 = await get_nowcast(LAT, LNG)
        result2 = await get_nowcast(LAT, LNG)

    # Second call returns cached — only 1 HTTP request
    assert mock_client.get.call_count == 1
    assert result1 is result2


@pytest.mark.asyncio
async def test_get_nowcast_falls_back_to_openmeteo_on_timeout():
    openmeteo_data = {
        "minutely_15": {
            "time": [(_BASE + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M") for i in range(13)],
            "precipitation": [0.0] * 13,
            "temperature_2m": [14.0] * 13,
            "wind_speed_10m": [18.0] * 13,   # km/h
            "wind_gusts_10m": [36.0] * 13,   # km/h
        }
    }
    call_count = {"n": 0}

    async def fake_get(url, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        if "geosphere" in url:
            import httpx as _httpx
            raise _httpx.TimeoutException("timeout", request=MagicMock())
        resp.raise_for_status = MagicMock(return_value=None)
        resp.json = MagicMock(return_value=openmeteo_data)
        return resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = fake_get
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_nowcast(LAT, LNG)

    assert result is not None
    assert result.source == "openmeteo"
    assert result.steps[0].wind_speed_ms == pytest.approx(18.0 / 3.6, rel=1e-3)


@pytest.mark.asyncio
async def test_get_nowcast_returns_none_when_both_fail():
    with patch("httpx.AsyncClient") as mock_cls:
        import httpx as _httpx
        mock_client = AsyncMock()
        mock_client.get.side_effect = _httpx.TimeoutException("timeout", request=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_nowcast(LAT, LNG)

    assert result is None


@pytest.mark.asyncio
async def test_get_nowcast_returns_none_when_disabled(settings_patch):
    result = await get_nowcast(LAT, LNG)
    assert result is None


@pytest.fixture
def settings_patch(monkeypatch):
    monkeypatch.setattr(weather_service.settings, "WEATHER_ENABLED", False)


# ── get_current ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_parses_nwp_response():
    mock_resp = _mock_httpx_response(_nwp_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_current(LAT, LNG)

    assert result is not None
    assert result.source == "geosphere_nwp"
    assert result.temperature_c == pytest.approx(14.0)
    expected_speed = math.sqrt(1.0**2 + 2.0**2)
    assert result.wind_speed_ms == pytest.approx(expected_speed, rel=1e-3)
    expected_gust = math.sqrt(2.0**2 + 4.0**2)
    assert result.gust_speed_ms == pytest.approx(expected_gust, rel=1e-3)
    assert result.wind_direction_deg is not None


@pytest.mark.asyncio
async def test_get_current_returns_none_on_error():
    with patch("httpx.AsyncClient") as mock_cls:
        import httpx as _httpx
        mock_client = AsyncMock()
        mock_client.get.side_effect = _httpx.TimeoutException("t", request=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_current(LAT, LNG)

    assert result is None


@pytest.mark.asyncio
async def test_get_current_caches_result():
    mock_resp = _mock_httpx_response(_nwp_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        r1 = await get_current(LAT, LNG)
        r2 = await get_current(LAT, LNG)

    assert mock_client.get.call_count == 1
    assert r1 is r2


# ── get_forecast ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_forecast_parses_horizons():
    mock_resp = _mock_httpx_response(_nwp_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_forecast(LAT, LNG, horizons=(6, 12))

    assert result is not None
    assert result.source == "geosphere_nwp"
    assert len(result.horizons) == 2
    h6 = result.horizons[0]
    assert h6.hours == 6
    assert h6.precipitation_acc_mm == pytest.approx(NWP_RR_ACC[6])
    assert h6.temperature_c == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_get_forecast_horizon_beyond_data():
    mock_resp = _mock_httpx_response(_nwp_response())
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        # NWP response only has 24 steps, request h=100
        result = await get_forecast(LAT, LNG, horizons=(100,))

    assert result is not None
    assert result.horizons[0].precipitation_acc_mm is None


@pytest.mark.asyncio
async def test_get_forecast_returns_none_on_error():
    with patch("httpx.AsyncClient") as mock_cls:
        import httpx as _httpx
        mock_client = AsyncMock()
        mock_client.get.side_effect = _httpx.TimeoutException("t", request=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_forecast(LAT, LNG)

    assert result is None


# ── get_warnings ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_warnings_returns_empty_list_on_timeout():
    with patch("httpx.AsyncClient") as mock_cls:
        import httpx as _httpx
        mock_client = AsyncMock()
        mock_client.get.side_effect = _httpx.TimeoutException("t", request=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_warnings(LAT, LNG)
    assert result == []


@pytest.mark.asyncio
async def test_get_warnings_parses_response():
    from datetime import timezone, timedelta
    future = datetime.now(UTC) + timedelta(hours=3)
    warn_data = {"warnings": [{
        "level": 2,
        "event": "RAIN",
        "text": "Starkregen erwartet",
        "onset": datetime.now(UTC).isoformat(),
        "expires": future.isoformat(),
        "regionName": "Bregenz",
    }]}
    mock_resp = _mock_httpx_response(warn_data)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_warnings(LAT, LNG)

    assert len(result) == 1
    assert result[0].level == 2
    assert result[0].event_type == "Starkregen"
    assert result[0].region == "Bregenz"


@pytest.mark.asyncio
async def test_get_warnings_skips_expired():
    from datetime import timezone, timedelta
    past = datetime.now(UTC) - timedelta(hours=1)
    warn_data = {"warnings": [{
        "level": 3,
        "event": "WIND",
        "text": "Sturm",
        "onset": (datetime.now(UTC) - timedelta(hours=6)).isoformat(),
        "expires": past.isoformat(),
    }]}
    mock_resp = _mock_httpx_response(warn_data)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_warnings(LAT, LNG)

    assert result == []


@pytest.mark.asyncio
async def test_get_warnings_sorted_by_level_desc():
    from datetime import timezone, timedelta
    future = datetime.now(UTC) + timedelta(hours=3)
    warn_data = {"warnings": [
        {"level": 1, "event": "FOG", "text": "", "onset": datetime.now(UTC).isoformat(), "expires": future.isoformat()},
        {"level": 3, "event": "WIND", "text": "", "onset": datetime.now(UTC).isoformat(), "expires": future.isoformat()},
        {"level": 2, "event": "RAIN", "text": "", "onset": datetime.now(UTC).isoformat(), "expires": future.isoformat()},
    ]}
    mock_resp = _mock_httpx_response(warn_data)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_warnings(LAT, LNG)

    assert [w.level for w in result] == [3, 2, 1]


# ── Empty feature edge cases ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_nowcast_empty_features_returns_none():
    resp = {"type": "FeatureCollection", "features": [], "timestamps": []}
    mock_resp = _mock_httpx_response(resp)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_nowcast(LAT, LNG)

    assert result is None


@pytest.mark.asyncio
async def test_get_nowcast_missing_parameter_uses_zero():
    # rr missing from parameters → precipitation should default to 0.0
    resp = _make_geosphere_response({"t2m": NOWCAST_T2M}, NOWCAST_TIMESTAMPS)
    mock_resp = _mock_httpx_response(resp)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_nowcast(LAT, LNG)

    assert result is not None
    assert all(s.precipitation_mm == 0.0 for s in result.steps)
    assert result.total_mm == 0.0


# ── Open-Meteo fallbacks ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_falls_back_to_openmeteo():
    openmeteo_data = {
        "current": {
            "temperature_2m": 22.5,
            "relative_humidity_2m": 55.0,
            "wind_speed_10m": 36.0,   # km/h → 10 m/s
            "wind_gusts_10m": 54.0,   # km/h → 15 m/s
            "wind_direction_10m": 270.0,
            "precipitation": 0.0,
        }
    }

    async def fake_get(url, **kwargs):
        resp = MagicMock()
        if "geosphere" in url:
            import httpx as _httpx
            raise _httpx.TimeoutException("timeout", request=MagicMock())
        resp.raise_for_status = MagicMock(return_value=None)
        resp.json = MagicMock(return_value=openmeteo_data)
        return resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = fake_get
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_current(LAT, LNG)

    assert result is not None
    assert result.source == "openmeteo"
    assert result.temperature_c == pytest.approx(22.5)
    assert result.wind_speed_ms == pytest.approx(36.0 / 3.6, rel=1e-3)
    assert result.wind_direction_deg == pytest.approx(270.0)


@pytest.mark.asyncio
async def test_get_forecast_falls_back_to_openmeteo():
    hours = list(range(25))
    prec = [0.1] * 25      # mm/h each hour
    openmeteo_data = {
        "hourly": {
            "temperature_2m": [20.0] * 25,
            "wind_speed_10m": [36.0] * 25,   # km/h
            "wind_gusts_10m": [54.0] * 25,   # km/h
            "precipitation": prec,
        }
    }

    async def fake_get(url, **kwargs):
        resp = MagicMock()
        if "geosphere" in url:
            import httpx as _httpx
            raise _httpx.TimeoutException("timeout", request=MagicMock())
        resp.raise_for_status = MagicMock(return_value=None)
        resp.json = MagicMock(return_value=openmeteo_data)
        return resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = fake_get
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_forecast(LAT, LNG, horizons=(6, 12))

    assert result is not None
    assert result.source == "openmeteo"
    assert len(result.horizons) == 2
    h6 = result.horizons[0]
    assert h6.hours == 6
    # accumulated: sum of prec[0..6] = 7 * 0.1 = 0.7
    assert h6.precipitation_acc_mm == pytest.approx(0.7, rel=1e-3)
    assert h6.wind_speed_ms == pytest.approx(36.0 / 3.6, rel=1e-3)


# ── analyze_weather ───────────────────────────────────────────────────────────

def _make_current(temp=15.0, hum=60.0, wind=5.0, gust=8.0) -> CurrentWeather:
    return CurrentWeather(
        temperature_c=temp,
        humidity_pct=hum,
        wind_speed_ms=wind,
        gust_speed_ms=gust,
        source="test",
    )


def _make_forecast(gust_ms: float = 5.0) -> ForecastResult:
    return ForecastResult(
        horizons=[ForecastHorizon(hours=6, gust_speed_ms=gust_ms)],
        source="test",
    )


def test_analyze_no_alerts_in_mild_conditions():
    result = analyze_weather(_make_current(), _make_forecast())
    assert result == []


def test_analyze_storm_warn_at_bf8():
    current = _make_current(gust=17.2)
    alerts = analyze_weather(current, None)
    assert len(alerts) == 1
    assert alerts[0].key == "storm"
    assert alerts[0].level == "warn"


def test_analyze_storm_danger_at_bf10():
    current = _make_current(gust=25.0)
    alerts = analyze_weather(current, None)
    assert len(alerts) == 1
    assert alerts[0].key == "storm"
    assert alerts[0].level == "danger"


def test_analyze_storm_upcoming_from_forecast():
    # current is calm; forecast has BF8 gust in 6h
    current = _make_current(gust=5.0)
    forecast = _make_forecast(gust_ms=18.0)
    alerts = analyze_weather(current, forecast)
    assert len(alerts) == 1
    assert alerts[0].key == "storm"
    assert alerts[0].level == "warn"
    assert "6h" in alerts[0].label_de


def test_analyze_storm_active_suppresses_forecast():
    # active BF8 storm should not also add an "upcoming" alert
    current = _make_current(gust=18.0)
    forecast = _make_forecast(gust_ms=18.0)
    alerts = analyze_weather(current, forecast)
    storm_alerts = [a for a in alerts if a.key == "storm"]
    assert len(storm_alerts) == 1


def test_analyze_wildfire_warn():
    current = _make_current(temp=32.0, hum=28.0, gust=5.0)
    nowcast = NowcastResult(steps=[], peak_mm=0, peak_at=None, total_mm=0.0, trend="stable", source="x")
    alerts = analyze_weather(current, None, nowcast)
    assert any(a.key == "wildfire" and a.level == "warn" for a in alerts)


def test_analyze_wildfire_danger():
    current = _make_current(temp=33.0, hum=18.0, gust=5.0)
    alerts = analyze_weather(current, None)
    assert any(a.key == "wildfire" and a.level == "danger" for a in alerts)


def test_analyze_wildfire_suppressed_by_rain():
    current = _make_current(temp=32.0, hum=28.0, gust=5.0)
    nowcast = NowcastResult(steps=[], peak_mm=1.0, peak_at=None, total_mm=2.5, trend="stable", source="x")
    alerts = analyze_weather(current, None, nowcast)
    assert not any(a.key == "wildfire" for a in alerts)


def test_analyze_missing_humidity_no_wildfire():
    current = CurrentWeather(temperature_c=35.0, humidity_pct=None, source="test")
    alerts = analyze_weather(current, None)
    assert not any(a.key == "wildfire" for a in alerts)
