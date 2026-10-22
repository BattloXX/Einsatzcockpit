"""Routen rendern Warnstatus für Wetterseite, Einsatz und GSL über dieselbe Modulreferenz."""
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.icons import render_icon
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.master import FireDept
from app.routers import ui_weather
from app.services import weather_service as ws
from tests.test_weather_dashboard_tokens import _login, _make_org
from tests.test_weather_public_json import _make_org_with_token, _make_station

STATUSES = ["ok_no_warnings", "ok_with_warnings", "unavailable", "stale", "disabled"]


@pytest.fixture
def panel_routes(client, setup_db, monkeypatch):
    org_id = _make_org()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = db.get(FireDept, org_id)
        org.fallback_lat, org.fallback_lng = 47.466, 9.745
        org.timezone = "Europe/Vienna"
        incident = Incident(primary_org_id=org_id, lat=47.466, lng=9.745)
        lage = MajorIncident(org_id=org_id, name="Warnstatus Test")
        db.add_all([incident, lage])
        db.commit()
        routes = ["/wetter/panel", f"/einsatz/{incident.id}/wetter/panel", f"/gsl/{lage.id}/wetter/panel"]
    _login(client, org_id, "warnstatus-" + uuid.uuid4().hex[:10])
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(ui_weather, "_build_abfluss_views", AsyncMock(return_value=[]))
    monkeypatch.setattr(ui_weather, "_build_station_views", lambda *args: [])
    for fn in ["get_current", "get_nowcast", "get_forecast", "get_daily_forecast"]:
        monkeypatch.setattr(ws, fn, AsyncMock(return_value=None))
    return routes, org_id


@pytest.mark.parametrize("status", STATUSES)
def test_panel_routes_render_all_statuses(client, panel_routes, monkeypatch, status):
    routes, org_id = panel_routes
    last = datetime(2026, 10, 22, 10, tzinfo=UTC)
    warnings = [ws.WeatherWarning(3, "Gewitter", "Alte Warnung", last - timedelta(days=1), last + timedelta(days=1))]
    result = ws.WarningsResult(status, warnings if status in {"stale", "ok_with_warnings"} else [], last, last)
    mock = AsyncMock(return_value=result)
    monkeypatch.setattr(ws, "get_warnings_result", mock)
    texts = {"ok_no_warnings": "Keine aktiven amtlichen Warnungen",
             "unavailable": "Amtliche Wetterwarnungen derzeit nicht verfügbar",
             "stale": "Warninformationen veraltet – letzte erfolgreiche Abfrage:",
             "disabled": "Amtliche Wetterwarnungen deaktiviert", "ok_with_warnings": "Gewitter"}
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, response.text[:300]
        assert f'data-warnings-status="{status}"' in response.text
        assert texts[status] in response.text
        if status == "stale":
            assert "22.10.2026 12:00" in response.text
            assert str(render_icon("alert-triangle")) in response.text
            assert "border:2px solid" in response.text
        if status in {"unavailable", "stale", "disabled"}:
            assert "Keine aktiven amtlichen Warnungen" not in response.text
            assert "keine aktive Warnung" not in response.text
    assert mock.await_count == 3
    assert all(call.kwargs == {"org_id": org_id} for call in mock.await_args_list)


def test_globally_disabled_panels_show_notice_without_fetch(client, panel_routes, monkeypatch):
    routes, _ = panel_routes
    monkeypatch.setattr(settings, "WEATHER_ENABLED", False)
    fetch = AsyncMock()
    monkeypatch.setattr(ws, "get_warnings_result", fetch)
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200
        assert 'data-warnings-status="disabled"' in response.text
    fetch.assert_not_awaited()


@pytest.mark.parametrize("status", STATUSES)
def test_infoscreen_and_public_json_warning_status(client, setup_db, monkeypatch, status):
    token, org_id = _make_org_with_token()
    _make_station(org_id, lat=47.466, lng=9.745)
    monkeypatch.setattr(settings, "WEATHER_ENABLED", True)
    monkeypatch.setattr(ui_weather, "_build_abfluss_views", AsyncMock(return_value=[]))
    last = datetime(2026, 10, 22, 10, tzinfo=UTC)
    mock = AsyncMock(return_value=ws.WarningsResult(status, [], last, last))
    monkeypatch.setattr(ws, "get_warnings_result", mock)
    for fn in ["get_current", "get_nowcast", "get_daily_forecast"]:
        monkeypatch.setattr(ws, fn, AsyncMock(return_value=None))
    response = client.get(f"/wetter/oeffentlich/{token}.json")
    assert response.status_code == 200
    assert response.json()["warnings_status"] == status
    assert response.json()["warnings_last_success_at"] == last.isoformat()
    response = client.get(f"/wetter/infoscreen/{token}")
    assert response.status_code == 200
    assert f'data-warnings-status="{status}"' in response.text
    assert all(call.kwargs == {"org_id": org_id} for call in mock.await_args_list)


@pytest.mark.parametrize("status", STATUSES)
def test_warning_geojson_exposes_status(client, panel_routes, monkeypatch, status):
    routes, org_id = panel_routes
    last = datetime(2026, 10, 22, 10, tzinfo=UTC)
    mock = AsyncMock(return_value=ws.WarningsResult(status, [], last, last))
    monkeypatch.setattr(ws, "get_warnings_result", mock)
    response = client.get(routes[-1].replace("/panel", "/warnungen.geojson"))
    assert response.status_code == 200
    assert response.json()["warnings_status"] == status
    assert response.json()["warnings_last_success_at"] == last.isoformat()
    mock.assert_awaited_once_with(47.466, 9.745, org_id=org_id)


def test_warning_geojson_disabled_status(client, panel_routes, monkeypatch):
    routes, _ = panel_routes
    monkeypatch.setattr(settings, "WEATHER_ENABLED", False)
    fetch = AsyncMock()
    monkeypatch.setattr(ws, "get_warnings_result", fetch)
    response = client.get(routes[-1].replace("/panel", "/warnungen.geojson"))
    assert response.status_code == 200
    assert response.json()["warnings_status"] == "disabled"
    fetch.assert_not_awaited()
