"""Phase 6: Lazy-Rendering, Org-Scope und Diagnose ohne Zugangsdaten."""
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.services import kachelmann_service, weather_health
from app.services import weather_compare as wc
from app.services import weather_service as ws
from tests.test_weather_compare import forecast
from tests.test_weather_status_panels import panel_routes as base_panel_routes

panel_routes = base_panel_routes


def test_compare_route_scoped_and_lazy(client, panel_routes, monkeypatch):
    _, org_id = panel_routes
    start = datetime(2026, 10, 22, 10, tzinfo=UTC)
    mock = AsyncMock(return_value=wc.evaluate({"test": forecast()}, start, 6))
    monkeypatch.setattr(wc, "compare", mock)
    monkeypatch.setattr(ws, "get_warnings_result", AsyncMock(return_value=ws.WarningsResult("ok_no_warnings", [])))
    html = client.get('/wetter').text
    mock.assert_not_called()
    assert 'hx-trigger="toggle[this.open] once"' in html
    for id_ in ("wetter-map", "radar-play-btn", "radar-scrubber", "radar-time-label", "radar-live-badge"):
        assert f'id="{id_}"' in html
    assert '/static/js/weather_radar.js' in html
    response = client.get('/wetter/vergleich?org_id=999999&lat=0&lng=0')
    assert response.status_code == 200
    assert "22.10.2026 12:00" in response.text
    assert "Nicht vergleichbar" in response.text
    mock.assert_awaited_once_with(47.466, 9.745, 6, org_id)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if row is None:
            row = OrgSettings(org_id=org_id)
            db.add(row)
        row.weather_enabled = False
        db.commit()
    assert client.get('/wetter/vergleich').status_code == 404
    assert mock.await_count == 1


def test_admin_diagnostics_without_secrets(client, panel_routes, monkeypatch):
    _, org_id = panel_routes
    secret = "phase6-never-render-this-api-key"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if row is None:
            row = OrgSettings(org_id=org_id)
            db.add(row)
        row.kachelmann_api_key = secret
        db.commit()
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(ws, "get_warnings_result", AsyncMock(return_value=ws.WarningsResult("unavailable", [])))
    monkeypatch.setattr(weather_health, "provider_health", AsyncMock(return_value={
        "geosphere": {"last_success_at": "2026-10-22T10:00:00+00:00", "last_error_at": None,
                      "last_error": None, "last_latency_ms": 12, "success_count": 2, "error_count": 0}}))
    kachelmann_service.reset_key_cache(org_id)
    html = client.get('/admin/settings/wetter?org_id=999999').text
    assert 'id="diagnose"' in html
    assert "WEATHER_COMPARE_WARN_PRECIP_MM" in html and "Cache: Lokal" in html
    assert "22.10.2026 12:00" in html
    assert "konfiguriert: Ja" in html
    assert secret not in html
    assert "REDIS_URL" not in html


def test_shared_radar_loaded_on_both_maps():
    for name in ('weather/index.html', 'incident_major/karte.html'):
        assert '/static/js/weather_radar.js' in Path('app/templates', name).read_text()


def test_focus_wind_and_warning_geometries(client, panel_routes, monkeypatch):
    routes, org_id = panel_routes
    at = datetime(2026, 10, 22, 10, tzinfo=UTC)
    meta = ws.DataMeta("geosphere", "AROME", 47.466, 9.745, None, at, at, "forecast")
    monkeypatch.setattr(ws, "get_current", AsyncMock(return_value=ws.CurrentWeather(
        wind_direction_deg=0, wind_speed_ms=0, gust_speed_ms=0, meta=meta)))
    focus = client.get(routes[2].replace('/panel', '/focus.json'))
    assert focus.status_code == 200
    assert focus.json()['wind_dir_deg'] == 0
    assert focus.json()['wind_ms'] == 0
    assert focus.json()['time'] == at.isoformat()
    polygon = {"type": "Polygon", "coordinates": [[[9, 47], [10, 47], [10, 48], [9, 47]]]}
    warnings = [ws.WeatherWarning(2, "Wind", "Gebiet", at, at, geometry=polygon),
                ws.WeatherWarning(1, "Regen", "Ohne Gebiet", at, at)]
    monkeypatch.setattr(ws, "get_warnings_result",
                        AsyncMock(return_value=ws.WarningsResult("ok_with_warnings", warnings)))
    result = client.get(routes[2].replace('/panel', '/warnungen.geojson')).json()
    assert result['features'][0]['geometry'] == polygon
    assert result['features'][1]['geometry'] == {"type": "Point", "coordinates": [9.745, 47.466]}
    assert ws.get_warnings_result.await_args.kwargs == {"org_id": org_id}


def test_diagnostics_admin_only(client, panel_routes, monkeypatch):
    import uuid

    from tests.test_weather_dashboard_tokens import _login

    _, org_id = panel_routes
    _login(client, org_id, 'wx-readonly-' + uuid.uuid4().hex[:8], role_code='readonly')
    assert client.get('/admin/settings/wetter').status_code == 403
    mock = AsyncMock(return_value=wc.evaluate({}, datetime.now(UTC), 6))
    monkeypatch.setattr(wc, 'compare', mock)
    assert client.get('/wetter/vergleich').status_code == 200
    assert mock.await_args.args[-1] == org_id
