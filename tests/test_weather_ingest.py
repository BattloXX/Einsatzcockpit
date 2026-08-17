"""Tests für den Wetterstations-Push-Ingest (PR 2)."""
import pytest

from app.config import settings
from app.core.security import generate_weather_station_token, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.weather import WeatherStation


@pytest.fixture
def station(setup_db):
    """Legt eine Wetterstation für die Home-Org an. Gibt (token, station_id, org_id)."""
    raw = generate_weather_station_token()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        st = WeatherStation(
            org_id=org.id,
            name="FW-Haus Test",
            ingest_token_hash=hash_api_key(raw),
            active=True,
        )
        db.add(st)
        db.commit()
        return raw, st.id, org.id
    finally:
        db.close()


def _get_station(station_id: int) -> WeatherStation:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.get(WeatherStation, station_id)
    finally:
        db.close()


def test_ingest_invalid_token(client, station):
    r = client.get("/api/v1/weather/ingest", params={"token": "wxst_nope", "temp": "10"})
    assert r.status_code == 401


def test_ingest_disabled_returns_404(client, station, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_STATION_INGEST_ENABLED", False)
    token, _, _ = station
    r = client.get("/api/v1/weather/ingest", params={"token": token, "temp": "10"})
    assert r.status_code == 404


def test_ingest_updates_snapshot(client, station, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    token, station_id, _ = station
    r = client.get("/api/v1/weather/ingest", params={
        "token": token, "temp": "12.3", "hum": "80", "wind": "4.5",
        "gust": "9.1", "dir": "180", "press": "1013.2", "rainrate": "0.4",
        "rainday": "5.2", "dew": "8.0", "solar": "350", "uv": "3",
    })
    assert r.status_code == 204
    st = _get_station(station_id)
    assert st.last_temp_c == 12.3
    assert st.last_hum_pct == 80.0
    assert st.last_wind_ms == 4.5
    assert st.last_pressure_hpa == 1013.2
    assert st.last_seen_at is not None
    assert st.last_measured_at is not None


def test_ingest_drops_implausible_values(client, station, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    token, station_id, _ = station
    # Temp 999 (>60) und Feuchte 250 (>100) müssen verworfen werden, Wind bleibt.
    r = client.get("/api/v1/weather/ingest", params={
        "token": token, "temp": "999", "hum": "250", "wind": "3.0",
    })
    assert r.status_code == 204
    st = _get_station(station_id)
    assert st.last_temp_c is None
    assert st.last_hum_pct is None
    assert st.last_wind_ms == 3.0


def test_ingest_throttled_keeps_old_snapshot(client, station, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    token, station_id, _ = station
    r1 = client.get("/api/v1/weather/ingest", params={"token": token, "temp": "10.0"})
    assert r1.status_code == 204
    assert _get_station(station_id).last_temp_c == 10.0

    # Jetzt Mindestintervall hochsetzen → zweiter Push wird gedrosselt (verworfen).
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 3600)
    r2 = client.get("/api/v1/weather/ingest", params={"token": token, "temp": "20.0"})
    assert r2.status_code == 204
    assert _get_station(station_id).last_temp_c == 10.0  # unverändert


def test_ingest_writes_history(client, station, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    dburl = "sqlite:///" + str(tmp_path / "wx.db").replace("\\", "/")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", dburl)

    import app.db_weather as dbw
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)
    dbw.init_weather_db()

    from app.models.weather import WeatherReading
    token, station_id, org_id = station
    r = client.get("/api/v1/weather/ingest", params={"token": token, "temp": "7.7", "wind": "2.0"})
    assert r.status_code == 204

    s = dbw.get_weather_session()
    try:
        rows = s.query(WeatherReading).filter(WeatherReading.org_id == org_id).all()
        assert len(rows) == 1
        assert rows[0].temp_c == 7.7
        assert rows[0].station_id == station_id
    finally:
        s.close()


def test_ingest_org_isolation_of_token(client, setup_db, monkeypatch):
    """Ein Token schreibt ausschließlich auf die Station seiner eigenen Org."""
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
        other = FireDept(slug="other-wx", name="Andere FF", is_home_org=False)
        db.add(other)
        db.flush()
        tok_a = generate_weather_station_token()
        tok_b = generate_weather_station_token()
        st_a = WeatherStation(org_id=org.id, name="A", ingest_token_hash=hash_api_key(tok_a))
        st_b = WeatherStation(org_id=other.id, name="B", ingest_token_hash=hash_api_key(tok_b))
        db.add_all([st_a, st_b])
        db.commit()
        id_a, id_b = st_a.id, st_b.id
    finally:
        db.close()

    client.get("/api/v1/weather/ingest", params={"token": tok_a, "temp": "1.0"})
    assert _get_station(id_a).last_temp_c == 1.0
    assert _get_station(id_b).last_temp_c is None


# ── Anzeige: View-Builder (PR 4) ──────────────────────────────────────────────

def _build_views(org_id):
    from app.routers.ui_weather import _build_station_views
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return _build_station_views(org_id, db)
    finally:
        db.close()


def _view_for(views, station_id):
    return next(v for v in views if v["id"] == station_id)


def test_station_views_offline_when_never_seen(station):
    _, station_id, org_id = station
    v = _view_for(_build_views(org_id), station_id)
    assert v["online"] is False
    assert v["seen_label"] is None


def test_station_views_online_after_ingest(client, station, monkeypatch):
    monkeypatch.setattr(settings, "WEATHER_INGEST_MIN_INTERVAL_S", 0)
    token, station_id, org_id = station
    client.get("/api/v1/weather/ingest", params={
        "token": token, "temp": "11.0", "wind": "2.5", "dir": "90",
    })
    v = _view_for(_build_views(org_id), station_id)
    assert v["online"] is True
    assert v["seen_label"] is not None
    assert v["temp"] == 11.0
    assert v["wind"] == 2.5
    assert v["wind_dir"] == "O"   # 90° → Ost


def test_station_card_template_compiles():
    from app.core.templating import templates
    for name in ("weather/_station_card.html",
                 "incident_major/_weather_panel.html",
                 "weather/index.html"):
        templates.env.get_template(name)


# ── Retention (PR 5) ──────────────────────────────────────────────────────────

# ── Szenario-Integration + Sparkline (PR 6) ───────────────────────────────────

def test_station_current_weather_none_when_offline():
    """_station_current_weather gibt None zurück, wenn keine Station online ist."""
    from app.routers.ui_weather import _station_current_weather
    assert _station_current_weather([]) is None
    assert _station_current_weather([{"online": False, "temp": 20.0}]) is None


def test_station_current_weather_builds_from_online_station():
    """_station_current_weather nutzt erste Online-Station für Szenario-Analyse."""
    from app.routers.ui_weather import _station_current_weather
    from app.services.weather_service import CurrentWeather
    views = [
        {"online": False, "temp": 5.0, "wind": 1.0, "gust": None, "hum": 80.0, "rain_rate": None},
        {"online": True,  "temp": 28.0, "wind": 18.5, "gust": 24.0, "hum": 22.0, "rain_rate": 0.0},
    ]
    cw = _station_current_weather(views)
    assert isinstance(cw, CurrentWeather)
    assert cw.temperature_c == 28.0
    assert cw.wind_speed_ms == 18.5
    assert cw.gust_speed_ms == 24.0
    assert cw.humidity_pct == 22.0
    assert cw.source == "station"


def test_station_current_triggers_storm_scenario():
    """Wenn Böe >= BF8 aus lokaler Station, wird Sturmwarnung erzeugt."""
    from app.routers.ui_weather import _station_current_weather
    from app.services.weather_service import analyze_weather
    views = [{"online": True, "temp": 15.0, "wind": 15.0, "gust": 20.0, "hum": 60.0, "rain_rate": None}]
    station_current = _station_current_weather(views)
    alerts = analyze_weather(station_current, None, None, None)
    keys = [a.key for a in alerts]
    assert "storm" in keys


def test_build_sparkline_svg_none_on_insufficient_data():
    """_build_sparkline_svg gibt None zurück bei weniger als 2 Datenpunkten."""
    from app.routers.ui_weather import _build_sparkline_svg
    assert _build_sparkline_svg([], "temp_c") is None


def test_build_sparkline_svg_produces_valid_data(monkeypatch, tmp_path):
    """_build_sparkline_svg liefert SVG-Koordinaten aus WeatherReading-Zeitreihe."""
    from datetime import UTC, datetime, timedelta

    from app.routers.ui_weather import _build_sparkline_svg
    dburl = "sqlite:///" + str(tmp_path / "wxsp.db").replace("\\", "/")
    monkeypatch.setattr(__import__("app.config", fromlist=["settings"]).settings,
                        "WEATHER_DATABASE_URL", dburl)
    import app.db_weather as dbw
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)
    dbw.init_weather_db()

    from app.models.weather import WeatherReading
    now = datetime.now(UTC)
    s = dbw.get_weather_session()
    try:
        for i, temp in enumerate([10.0, 12.5, 11.0, 15.0]):
            s.add(WeatherReading(org_id=1, station_id=1,
                                 ts=now - timedelta(hours=3 - i), temp_c=temp))
        s.commit()
        readings = s.query(WeatherReading).order_by(WeatherReading.ts).all()
    finally:
        s.close()

    svg = _build_sparkline_svg(readings, "temp_c")
    assert svg is not None
    assert svg["min"] == 10.0
    assert svg["max"] == 15.0
    assert svg["latest"] == 15.0
    assert " " in svg["points"]  # mind. zwei Koordinatenpaare


def test_sparkline_template_compiles():
    from app.core.templating import templates
    templates.env.get_template("weather/_station_sparkline.html")


# ── Retention (PR 5) ──────────────────────────────────────────────────────────

def test_retention_noop_when_db_disabled(monkeypatch):
    from app.services.weather_retention import purge_old_readings
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", "")
    assert purge_old_readings() == 0


def test_retention_purges_only_old_readings(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    dburl = "sqlite:///" + str(tmp_path / "wxret.db").replace("\\", "/")
    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", dburl)
    import app.db_weather as dbw
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)
    dbw.init_weather_db()

    from app.models.weather import WeatherReading, WeatherReadingHourly
    from app.services.weather_retention import purge_old_readings

    now = datetime.now(UTC)
    s = dbw.get_weather_session()
    try:
        s.add(WeatherReading(org_id=1, station_id=1, ts=now - timedelta(days=400), temp_c=1.0))
        s.add(WeatherReading(org_id=1, station_id=1, ts=now - timedelta(days=400), temp_c=1.1))
        s.add(WeatherReading(org_id=1, station_id=1, ts=now - timedelta(days=10), temp_c=2.0))
        bucket = (now - timedelta(days=400)).replace(
            minute=0, second=0, microsecond=0, tzinfo=None
        )
        s.add(WeatherReadingHourly(
            org_id=1, station_id=1, bucket_start=bucket, sample_count=2
        ))
        s.commit()
    finally:
        s.close()

    deleted = purge_old_readings(retention_days=365, chunk_size=1)  # chunk_size=1 testet Schleife
    assert deleted == 2

    s = dbw.get_weather_session()
    try:
        assert s.query(WeatherReading).count() == 1
    finally:
        s.close()
