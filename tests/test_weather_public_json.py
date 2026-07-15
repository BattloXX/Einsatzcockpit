"""Tests fuer den oeffentlichen Wetter-JSON-Endpoint (GET /wetter/oeffentlich/{token}.json).

Fuer externe Einbettung (z.B. WordPress-Widget der Vereins-Website) gedacht -- nutzt
denselben Token wie das bestehende Infoscreen-Dashboard (OrgSettings.weather_dashboard_token_hash)."""
import uuid
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.core.security import generate_weather_dashboard_token, generate_weather_station_token, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings
from app.models.weather import WeatherStation


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
        org_settings = OrgSettings(org_id=org.id, weather_dashboard_token_hash=hash_api_key(raw))
        db.add(org_settings)
        db.commit()
        return raw, org.id
    finally:
        db.close()


def _make_station(org_id: int, *, with_snapshot: bool = True) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        st = WeatherStation(
            org_id=org_id, name="FW-Haus Test",
            ingest_token_hash=hash_api_key(generate_weather_station_token()),
            active=True,
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
