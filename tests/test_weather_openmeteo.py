"""Open-Meteo-Adapter und konfigurierbare Provider-Ketten."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.services import kachelmann_service, weather_openmeteo, weather_service

LAT, LNG = 47.26, 11.39
MODEL = "geosphere_seamless"


@pytest.fixture(autouse=True)
def weather_config(monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_OPENMETEO", True)
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_GEOSPHERE_ENABLED", False)
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_MODEL", MODEL)
    monkeypatch.setattr(settings, "OPENMETEO_BASE_URL", "https://weather.example/forecast")
    for kind, order in weather_service._PROVIDER_DEFAULTS.items():
        monkeypatch.setattr(settings, f"WEATHER_PRIORITY_{kind.upper()}", order)
    monkeypatch.setattr(kachelmann_service, "is_configured", lambda org_id: org_id == 7)
    weather_service._cache.clear()
    weather_service._UNKNOWN_PROVIDERS.clear()
    yield
    weather_service._cache.clear()
    weather_service._UNKNOWN_PROVIDERS.clear()


@pytest.fixture
def http_mock():
    calls = []
    payload = {}

    async def fake_get(url, **kwargs):
        calls.append((url, kwargs["params"]))
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.get.side_effect = fake_get
        client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        yield calls, payload, client, client_cls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hourly", "daily", "current", "minutely15"])
@pytest.mark.parametrize("model", [None, MODEL])
async def test_client_parameters(kind, model, http_mock):
    calls, payload, _, client_cls = http_mock
    payload["test"] = "erhalten"
    adapter = getattr(weather_openmeteo, f"fetch_{kind}")
    args = (LAT, LNG, 25) if kind == "hourly" else (LAT, LNG)
    assert await adapter(*args, model=model) == payload
    url, params = calls[0]
    assert url == settings.OPENMETEO_BASE_URL
    assert params["latitude"] == LAT
    assert params["longitude"] == LNG
    assert params.get("models") == model
    assert ("models" in params) == (model is not None)
    client_cls.assert_called_once_with(timeout=settings.WEATHER_HTTP_TIMEOUT)
    key = "minutely_15" if kind == "minutely15" else kind
    assert "temperature_2m" in params[key]
    if kind == "hourly":
        assert params["forecast_hours"] == 25
        if model:
            assert set(params[key].split(",")) >= {
                "relative_humidity_2m", "wind_direction_10m", "snowfall", "weather_code",
            }
    if kind == "daily":
        assert params["timezone"] == "Europe/Vienna"
        assert params["forecast_days"] == 7
        assert "wind_gusts_10m_max" in params[key]
        assert "weather_code" in params[key]
    else:
        assert params["timezone"] == "UTC"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.TimeoutException("Timeout"), RuntimeError("Fehler")])
async def test_client_failure_returns_none(error, http_mock):
    _, _, client, _ = http_mock
    client.get.side_effect = error
    assert await weather_openmeteo.fetch_current(LAT, LNG) is None


@pytest.mark.asyncio
async def test_current_model_values_and_missing_variables(http_mock):
    _, payload, _, _ = http_mock
    payload["current"] = {"temperature_2m": 0, "wind_speed_10m": 36, "weather_code": 3}
    result = await weather_service._openmeteo_current(LAT, LNG, model=MODEL)
    assert result.source == "open-meteo-geosphere"
    assert result.temperature_c == 0
    assert result.wind_speed_ms == 10
    assert result.weather_code == 3
    assert result.humidity_pct is None
    assert result.gust_speed_ms is None
    assert result.precipitation_1h_mm is None
    assert result.wind_direction_deg is None


@pytest.mark.asyncio
async def test_hourly_missing_precipitation_is_not_zero(http_mock, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 10, tzinfo=UTC).astimezone(tz)
    monkeypatch.setattr(weather_service, "datetime", Clock)
    _, payload, _, _ = http_mock
    payload["hourly"] = {
        "time": ["2026-10-22T10:00", "2026-10-22T11:00", "2026-10-22T12:00"],
        "temperature_2m": [1, 2, 3], "precipitation": [0, None, 1],
        "wind_speed_10m": [36] * 3, "relative_humidity_2m": [70] * 3,
        "snowfall": [0, 1, 2], "weather_code": [1, 2, 3],
    }
    result = await weather_service._openmeteo_forecast(LAT, LNG, (0, 2, 6), model=MODEL)
    assert result.source == "open-meteo-geosphere"
    assert result.horizons[0].precipitation_acc_mm == 0
    assert result.horizons[1].precipitation_acc_mm is None
    assert result.horizons[1].snowfall_cm == 3
    assert not result.horizons[1].complete
    assert result.horizons[1].humidity_pct == 70
    assert result.horizons[1].wind_speed_ms == 10
    assert result.horizons[1].wind_direction_deg is None
    assert result.horizons[2].temperature_c is None
    assert result.horizons[2].precipitation_acc_mm is None


@pytest.mark.asyncio
async def test_nowcast_missing_precipitation_is_not_zero(http_mock):
    _, payload, _, _ = http_mock
    payload["minutely_15"] = {"time": ["2026-10-22T10:00", "2026-10-22T10:15"], "precipitation": [0]}
    result = await weather_service._openmeteo_nowcast(LAT, LNG, model=MODEL)
    assert result.source == "open-meteo-geosphere"
    assert result.steps[0].precipitation_mm == 0
    assert result.steps[1].precipitation_mm is None
    assert result.steps[1].temperature_c is None
    assert result.peak_mm is None
    assert result.total_mm is None
    from app.routers.ui_weather import _build_nowcast_bars, _peak_label

    assert "keine Daten" in _build_nowcast_bars(result)[1]["label"]
    assert _peak_label(result) is None
    current = weather_service.CurrentWeather(temperature_c=35, humidity_pct=15)
    assert weather_service.analyze_weather(current, None, result)


@pytest.mark.asyncio
async def test_daily_model_gusts_missing_values_and_label(http_mock):
    _, payload, _, _ = http_mock
    payload["daily"] = {"time": ["2026-10-22"], "wind_gusts_10m_max": [72], "weather_code": [61]}
    result = await weather_service._openmeteo_daily(LAT, LNG, model=MODEL)
    assert result.source == "open-meteo-geosphere"
    day = result.days[0]
    assert day.date_label == "Do 22.10."
    assert day.wind_max_ms == 20
    assert day.weather_code == 61
    assert day.precip_mm is None
    assert day.temp_max_c is None
    assert day.temp_min_c is None


@pytest.mark.parametrize("kind", ["current", "nowcast", "forecast", "daily"])
def test_provider_defaults_and_org_key(kind):
    expected = weather_service._PROVIDER_DEFAULTS[kind].split(",")
    assert weather_service._provider_order(kind, 7) == expected
    assert weather_service._provider_order(kind, 8) == [name for name in expected if name != "kachelmann"]


def test_provider_order_unknown_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "WEATHER_PRIORITY_CURRENT", " unbekannt, open-meteo,geosphere,UNBEKANNT,open-meteo ")
    for _ in range(2):
        assert weather_service._provider_order("current", 7) == ["open-meteo", "geosphere"]
    assert caplog.text.count("Unbekannter Wetterprovider") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current", "nowcast", "forecast", "daily"])
async def test_disabled_geosphere_model_never_called(kind, monkeypatch):
    monkeypatch.setattr(settings, f"WEATHER_PRIORITY_{kind.upper()}", "open-meteo-geosphere,open-meteo")
    adapter_name = {"current": "current", "nowcast": "nowcast", "forecast": "forecast", "daily": "daily"}[kind]
    function = getattr(weather_service, "get_daily_forecast" if kind == "daily" else f"get_{kind}")
    sentinel = object()
    with patch.object(weather_service, f"_openmeteo_{adapter_name}", new=AsyncMock(return_value=sentinel)) as adapter:
        assert await function(LAT, LNG) is sentinel
        assert await function(LAT, LNG) is sentinel
        assert adapter.await_count == 1
        assert adapter.await_args.kwargs.get("model") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current", "nowcast", "forecast", "daily"])
async def test_enabled_model_and_fallback_order(kind, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_GEOSPHERE_ENABLED", True)
    monkeypatch.setattr(settings, f"WEATHER_PRIORITY_{kind.upper()}", "open-meteo-geosphere,open-meteo")
    function = getattr(weather_service, "get_daily_forecast" if kind == "daily" else f"get_{kind}")
    sentinel = object()
    with patch.object(weather_service, f"_openmeteo_{kind}", new=AsyncMock(side_effect=[None, sentinel])) as adapter:
        assert await function(LAT, LNG) is sentinel
        assert adapter.await_count == 2
        assert adapter.await_args_list[0].kwargs["model"] == MODEL
        assert adapter.await_args_list[1].kwargs.get("model") is None


@pytest.mark.asyncio
async def test_daily_ttl_and_model_cache_separation(monkeypatch, http_mock):
    _, payload, _, _ = http_mock
    payload["daily"] = {"time": ["2026-10-22"]}
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_GEOSPHERE_ENABLED", True)
    monkeypatch.setattr(settings, "WEATHER_PRIORITY_DAILY", "open-meteo-geosphere,open-meteo")
    monkeypatch.setattr(settings, "WEATHER_DAILY_TTL", 17)
    with patch.object(weather_service, "_cache_set", wraps=weather_service._cache_set) as cache_set:
        await weather_service.get_daily_forecast(LAT, LNG)
        first_key = cache_set.call_args.args[0]
        assert first_key.startswith("daily7:")
        assert cache_set.call_args.args[2] == 17
        monkeypatch.setattr(settings, "WEATHER_OPENMETEO_MODEL", "anderes_modell")
        await weather_service.get_daily_forecast(LAT, LNG)
        assert cache_set.call_args.args[0] != first_key
        assert cache_set.call_count == 2


@pytest.mark.parametrize("kind", ["current", "nowcast", "forecast", "daily"])
def test_openmeteo_switches_are_independent(kind, monkeypatch):
    monkeypatch.setattr(settings, f"WEATHER_PRIORITY_{kind.upper()}", "open-meteo,open-meteo-geosphere")
    monkeypatch.setattr(settings, "WEATHER_FALLBACK_OPENMETEO", False)
    assert weather_service._provider_order(kind) == []
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_GEOSPHERE_ENABLED", True)
    assert weather_service._provider_order(kind) == ["open-meteo-geosphere"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["current", "nowcast", "forecast", "daily"])
async def test_enabled_model_result_source(kind, monkeypatch, http_mock):
    calls, payload, _, _ = http_mock
    payload.update({
        "current": {"temperature_2m": 2},
        "hourly": {"temperature_2m": [2] * 25},
        "daily": {"time": ["2026-10-22"]},
        "minutely_15": {"time": ["2026-10-22T10:00"]},
    })
    monkeypatch.setattr(settings, "WEATHER_OPENMETEO_GEOSPHERE_ENABLED", True)
    monkeypatch.setattr(settings, f"WEATHER_PRIORITY_{kind.upper()}", "open-meteo-geosphere,open-meteo")
    function = getattr(weather_service, "get_daily_forecast" if kind == "daily" else f"get_{kind}")
    result = await function(LAT, LNG)
    assert result.source == "open-meteo-geosphere"
    assert len(calls) == 1
    assert calls[0][1]["models"] == MODEL


@pytest.mark.asyncio
async def test_daily_standard_wind_gust_and_code_are_separate(http_mock):
    _, payload, _, _ = http_mock
    payload["daily"] = {
        "time": ["2026-10-25"], "wind_speed_10m_max": [36],
        "wind_gusts_10m_max": [72], "weather_code": [61], "precipitation_sum": [4],
    }
    result = await weather_service._openmeteo_daily(LAT, LNG)
    assert result.source == "openmeteo"
    day = result.days[0]
    assert day.date_label == "So 25.10."
    assert day.wind_max_ms == 10
    assert day.gust_max_ms == 20
    assert day.precip_mm == 4
    assert day.weather_code == 61
