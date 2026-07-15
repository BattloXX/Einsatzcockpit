"""Tests fuer den oeffentlichen Wetter-JSON-Endpoint (GET /wetter/oeffentlich/{token}.json).

Fuer externe Einbettung (z.B. WordPress-Widget der Vereins-Website) gedacht -- nutzt dasselbe
Token-Modell wie das bestehende Infoscreen-Dashboard (WeatherDashboardToken, mehrere
beschriftete Tokens je Org moeglich)."""
import uuid
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.core.security import generate_weather_dashboard_token, generate_weather_station_token, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.weather import WeatherDashboardToken, WeatherStation


def _make_org_with_token():
    # Eigene, frische Org statt der geteilten Home-Org: andere Testdateien (z.B.
    # test_weather_ingest.py) legen dort ebenfalls WeatherStation-Zeilen an, die in der
    # gemeinsamen session-weiten Test-DB ueber die gesamte Suite hinweg bestehen bleiben --
    # _build_station_views() wuerde sonst je nach Testreihenfolge eine fremde Station liefern.
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"wx-json-{uuid.uuid4().hex[:10]}", name="Test-Org Wetter-JSON",
                        color="#a4000a", bos="Feuerwehr")
        db.add(org)
        db.flush()
        raw = generate_weather_dashboard_token()
        db.add(WeatherDashboardToken(token_hash=hash_api_key(raw), label="Test", org_id=org.id))
        db.commit()
        return raw, org.id
    finally:
        db.close()


def _make_station(org_id: int, *, with_snapshot: bool = True, lat: float | None = None,
                   lng: float | None = None) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        st = WeatherStation(
            org_id=org_id, name="FW-Haus Test",
            ingest_token_hash=hash_api_key(generate_weather_station_token()),
            active=True, lat=lat, lng=lng,
        )
        if with_snapshot:
            st.last_measured_at = datetime.now(UTC)
            st.last_temp_c = 24.3
            st.last_hum_pct = 61.0
            st.last_wind_ms = 2.1     # -> 7.6 km/h
            st.last_gust_ms = 4.0     # -> 14.4 km/h
            st.last_wind_dir_deg = 225.0  # SW
            st.last_pressure_hpa = 1016.2
            st.last_rain_rate_mmh = 0.0
            st.last_rain_day_mm = 3.2
            st.last_dewpoint_c = 15.1
            st.last_solar_wm2 = 480.0
            st.last_uv = 4.2
        db.add(st)
        db.commit()
        return st.id
    finally:
        db.close()


def _init_weather_db(monkeypatch, tmp_path):
    dburl = "sqlite:///" + str(tmp_path / "wx_public_json.db").replace("\\", "/")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", dburl)
    import app.db_weather as dbw
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)
    dbw.init_weather_db()
    return dbw


def test_public_json_invalid_token_401(client, setup_db):
    r = client.get("/wetter/oeffentlich/nicht-existent.json")
    assert r.status_code == 401


def test_public_json_valid_token_ohne_station_404(client, setup_db):
    token, _ = _make_org_with_token()
    r = client.get(f"/wetter/oeffentlich/{token}.json")
    assert r.status_code == 404


def test_public_json_liefert_aktuelle_werte(client, setup_db, monkeypatch, tmp_path):
    dbw = _init_weather_db(monkeypatch, tmp_path)
    token, org_id = _make_org_with_token()
    station_id = _make_station(org_id)

    from app.models.weather import WeatherReading
    now = datetime.now(UTC)
    s = dbw.get_weather_session()
    try:
        for i, temp in enumerate([14.2, 18.0, 22.5, 26.8, 24.3]):
            s.add(WeatherReading(org_id=org_id, station_id=station_id,
                                  ts=now - timedelta(hours=4 - i), temp_c=temp,
                                  rain_rate_mmh=0.1 * i))
        s.commit()
    finally:
        s.close()

    r = client.get(f"/wetter/oeffentlich/{token}.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()

    assert data["station_name"] == "FW-Haus Test"
    assert data["org_name"]

    aktuell = data["aktuell"]
    assert aktuell["temp_c"] == 24.3
    assert aktuell["wind_kmh"] == 7.6   # 2.1 m/s * 3.6
    assert aktuell["gust_kmh"] == 14.4  # 4.0 m/s * 3.6
    assert aktuell["wind_dir_label"] == "SW"
    assert aktuell["pressure_hpa"] == 1016.2
    assert aktuell["solar_wm2"] == 480.0
    assert aktuell["uv"] == 4.2

    # Tages-Min/Max aus den soeben eingefuegten WeatherReadings (alle "heute" == UTC-naher Test)
    assert data["heute"]["temp_min_c"] == 14.2
    assert data["heute"]["temp_max_c"] == 26.8

    # 24h-Verlauf: mind. 2 Punkte vorhanden -> SVG-Polyline-Daten
    verlauf = data["verlauf_24h"]
    assert verlauf["temp"] is not None
    assert "points" in verlauf["temp"]
    assert " " in verlauf["temp"]["points"]


def test_public_json_ohne_verlaufsdaten_liefert_none(client, setup_db, monkeypatch, tmp_path):
    """Ohne konfigurierte Wetter-DB (oder ohne Readings) bleibt verlauf_24h leer statt zu crashen."""
    _init_weather_db(monkeypatch, tmp_path)
    token, org_id = _make_org_with_token()
    _make_station(org_id)

    r = client.get(f"/wetter/oeffentlich/{token}.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data["verlauf_24h"]["temp"] is None
    assert data["verlauf_24h"]["regen"] is None
    assert data["heute"]["temp_min_c"] is None


def test_public_json_ohne_koordinaten_liefert_leere_listen(client, setup_db):
    """Ohne Stations-/Org-Koordinaten bleiben warnungen/vorhersage leer statt zu crashen
    (kein Aufruf externer Wetterdienste ohne bekannten Ort)."""
    token, org_id = _make_org_with_token()
    _make_station(org_id)  # kein lat/lng

    r = client.get(f"/wetter/oeffentlich/{token}.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data["warnungen"] == []
    assert data["vorhersage"] == []


def test_public_json_liefert_warnungen_und_vorhersage(client, setup_db, monkeypatch):
    """Mit bekannten Koordinaten werden GeoSphere-Warnungen und die Open-Meteo-
    Tagesvorhersage abgefragt und ins JSON uebernommen (dieselben Quellen wie das
    bestehende Infoscreen-Dashboard)."""
    token, org_id = _make_org_with_token()
    _make_station(org_id, lat=47.466, lng=9.738)

    from app.routers import ui_weather
    from app.services import weather_service
    from app.services.weather_service import DailyForecast, DailyForecastDay, WeatherWarning

    now = datetime.now(UTC)

    async def _fake_warnings(lat, lng):
        return [WeatherWarning(level=2, event_type="wind", text="Sturmwarnung",
                                valid_from=now - timedelta(hours=1), valid_to=now + timedelta(hours=5))]

    async def _fake_forecast(lat, lng):
        return DailyForecast(days=[
            DailyForecastDay(date_label="Mo 20.07.", temp_max_c=27.0, temp_min_c=15.0,
                              precip_mm=0.5, wind_max_ms=5.0),
        ])

    monkeypatch.setattr(weather_service, "get_warnings", _fake_warnings)
    monkeypatch.setattr(weather_service, "get_daily_forecast", _fake_forecast)
    assert ui_weather.weather_service is weather_service  # gleiche Modul-Referenz wie gepatcht

    r = client.get(f"/wetter/oeffentlich/{token}.json")
    assert r.status_code == 200, r.text[:300]
    data = r.json()

    assert len(data["warnungen"]) == 1
    assert data["warnungen"][0]["event_type"] == "wind"
    assert data["warnungen"][0]["text"] == "Sturmwarnung"

    assert len(data["vorhersage"]) == 1
    tag = data["vorhersage"][0]
    assert tag["datum_label"] == "Mo 20.07."
    assert tag["temp_max_c"] == 27.0
    assert tag["temp_min_c"] == 15.0
    assert tag["regen_mm"] == 0.5
    assert tag["wind_max_kmh"] == 18.0  # 5.0 m/s * 3.6
