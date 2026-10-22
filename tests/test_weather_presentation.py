"""Darstellung der Phase 5 mit echten Nullwerten und unvollständigen Fenstern."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.routers import ui_weather
from app.services import weather_service as ws
from tests.test_weather_public_json import _make_org_with_token, _make_station
from tests.test_weather_status_panels import panel_routes as base_panel_routes

panel_routes = base_panel_routes


@pytest.fixture
def weather_data(monkeypatch):
    def install(at, complete=True):
        meta = ws.DataMeta("openmeteo", "Testmodell", 47.4, 9.7, None, at,
                           datetime.now(UTC), "forecast", is_fallback=True)
        forecast = ws.ForecastResult([
            ws.ForecastHorizon(h, precipitation_mm=0.0 if complete else None,
                               gust_max_ms=10.0, gust_max_at=at, complete=complete)
            for h in (1, 3, 6, 12, 24)
        ], meta=meta)
        monkeypatch.setattr(ws, "get_forecast", AsyncMock(return_value=forecast))
        monkeypatch.setattr(ws, "get_warnings_result", AsyncMock(return_value=ws.WarningsResult("ok_no_warnings", [])))
        return forecast
    return install


@pytest.mark.parametrize(("at", "display"), [
    (datetime(2026, 7, 20, 10, tzinfo=UTC), "20.07.2026 12:00"),
    (datetime(2026, 1, 20, 10, tzinfo=UTC), "20.01.2026 11:00"),
])
def test_panels_zero_fallback_timezone(client, panel_routes, weather_data, monkeypatch, at, display):
    routes, _ = panel_routes
    weather_data(at)
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "UTC")
    for route in [*routes, "/wetter"]:
        response = client.get(route)
        assert response.status_code == 200, response.text[:300]
        html = response.text
        assert "0.0 mm" in html
        assert "10.0 m/s · 36.0 km/h" in html
        assert display in html
        assert "Ersatzquelle" in html
        assert "Testmodell" in html
        assert "Interne Hinweise – keine amtliche Warnung" in html
        assert 'data-horizon="3"' in html and 'data-horizon="6"' in html
        if route in (routes[0], "/wetter"):
            for section in ("Aktuell", "Kurzfristig", "Prognose", "Warnungen"):
                assert section in html
            assert 'id="wetter-data-section"' in html
            assert 'hx-swap="outerHTML"' in html
        elif route == routes[1]:
            assert 'data-horizon="12"' not in html
        else:
            assert 'data-horizon="12"' in html
    summary = client.get(routes[1].replace("/panel", "/summary"))
    assert summary.status_code == 200
    assert "0.0 mm" in summary.text
    assert ws.get_forecast.await_count == 4


def test_panels_missing_precipitation(client, panel_routes, weather_data):
    routes, _ = panel_routes
    weather_data(datetime(2026, 7, 20, 10, tzinfo=UTC), complete=False)
    for route in routes:
        html = client.get(route).text
        assert "Daten unvollständig" in html
        assert "0.0 mm" not in html


@pytest.mark.parametrize("complete", [True, False])
def test_infoscreen_and_json(client, setup_db, monkeypatch, weather_data, complete):
    token, org_id = _make_org_with_token()
    _make_station(org_id, lat=47.4, lng=9.7)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(FireDept, org_id).timezone = "Europe/Vienna"
        db.commit()
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(ui_weather, "_build_abfluss_views", AsyncMock(return_value=[]))
    for name in ("get_current", "get_nowcast", "get_daily_forecast"):
        monkeypatch.setattr(ws, name, AsyncMock(return_value=None))
    weather_data(datetime(2026, 7, 20, 10, tzinfo=UTC), complete)
    response = client.get(f"/wetter/infoscreen/{token}")
    assert response.status_code == 200, response.text[:300]
    assert ('aria-label="Kurzfristig"' in response.text) is complete
    assert "Ersatzquelle" in response.text
    if complete:
        assert "20.07.2026 12:00" in response.text
        assert "0.0 mm" in response.text
    data = client.get(f"/wetter/oeffentlich/{token}.json").json()
    assert {"org_name", "station_name", "aktuell", "heute", "verlauf_24h", "standort",
            "warnings_status", "warnings_last_success_at", "warnungen", "vorhersage"} <= data.keys()
    assert data["prognose_kurz"][0]["complete"] is complete
    assert data["prognose_kurz"][0]["precipitation_mm"] == (0.0 if complete else None)
    assert any(d["is_fallback"] for d in data["datenstand"])
    assert all(call.kwargs == {"org_id": org_id} for call in ws.get_forecast.await_args_list)


def test_critical_hint_and_forecast_failure(client, panel_routes, weather_data, monkeypatch):
    routes, _ = panel_routes
    weather_data(datetime(2026, 7, 20, 10, tzinfo=UTC))
    current = ws.CurrentWeather(temperature_c=12.0, wind_speed_ms=3.0, gust_speed_ms=25.0)
    monkeypatch.setattr(ws, "get_current", AsyncMock(return_value=current))
    html = client.get(routes[1]).text
    assert "Sturm: mögliche Gefahr" in html
    assert "Interne Hinweise – keine amtliche Warnung" in html
    monkeypatch.setattr(ws, "get_forecast", AsyncMock(side_effect=TimeoutError("Ausfall")))
    response = client.get(routes[1])
    assert response.status_code == 200
    assert "Daten unvollständig" in response.text
    assert "Sturm: mögliche Gefahr" not in response.text
    assert "25.0 m/s" in response.text


def test_measured_metadata_and_daily_forecast(client, panel_routes, weather_data, monkeypatch):
    routes, _ = panel_routes
    at = datetime.now(UTC)
    forecast = weather_data(at)
    forecast.meta.freshness = "stale"
    current = ws.CurrentWeather(
        temperature_c=-2.0,
        meta=ws.DataMeta("station", "Gerätehaus", 47.4, 9.7, None, at, at, "measured"),
    )
    monkeypatch.setattr(ws, "get_current", AsyncMock(return_value=current))
    monkeypatch.setattr(ws, "get_daily_forecast", AsyncMock(return_value=ws.DailyForecast([
        ws.DailyForecastDay("Mo 20.07.", precip_mm=0.0),
    ])))
    response = client.get(routes[0])
    assert response.status_code == 200
    for text in ("Messung", "Gerätehaus", "Vorhersage (Modell)", "Amtliche Warninformationen",
                 "Tagesprognose", "Mo 20.07.", "0.0 mm"):
        assert text in response.text
