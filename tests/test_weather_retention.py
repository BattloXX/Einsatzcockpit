"""Tests fuer Stunden-Rollup und crash-sichere Wetter-Retention."""
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings


@pytest.fixture
def weather_db(monkeypatch, tmp_path):
    import app.db_weather as dbw

    monkeypatch.setattr(settings, "WEATHER_DATABASE_URL", f"sqlite:///{tmp_path / 'retention.db'}")
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)
    dbw.init_weather_db()
    yield dbw
    dbw._engine.dispose()
    monkeypatch.setattr(dbw, "_engine", None)
    monkeypatch.setattr(dbw, "_SessionLocal", None)


def _old_bucket() -> datetime:
    return (datetime.now(UTC) - timedelta(days=40)).replace(
        minute=0, second=0, microsecond=0, tzinfo=None
    )


def _add_readings(dbw, bucket, rain=(1.0, 2.5), directions=(350.0, 10.0)):
    from app.models.weather import WeatherReading

    session = dbw.get_weather_session()
    try:
        for index, (rain, direction) in enumerate(zip(rain, directions)):
            session.add(WeatherReading(
                org_id=1,
                station_id=7,
                ts=bucket + timedelta(minutes=10 + index * 20),
                temp_c=10.0 + index * 2,
                wind_ms=2.0,
                wind_dir_deg=direction,
                rain_day_mm=rain,
            ))
        session.commit()
    finally:
        session.close()


def test_rollup_is_idempotent_and_uses_circular_wind_mean(weather_db):
    from app.models.weather import WeatherReadingHourly
    from app.services.weather_retention import rollup_hourly_readings

    bucket = _old_bucket()
    _add_readings(weather_db, bucket)
    assert rollup_hourly_readings(raw_retention_days=30) == 1
    # Simuliert einen Crash nach Rollup-Commit, vor Raw-Loeschung: dieselben
    # Quellen werden erneut eingespielt und muessen denselben Bucket ersetzen.
    _add_readings(weather_db, bucket)
    assert rollup_hourly_readings(raw_retention_days=30) == 1

    session = weather_db.get_weather_session()
    try:
        rows = session.query(WeatherReadingHourly).filter(
            WeatherReadingHourly.org_id == 1,
            WeatherReadingHourly.station_id == 7,
        ).all()
        assert len(rows) == 1
        assert rows[0].sample_count == 2
        assert rows[0].temp_c_avg == pytest.approx(11.0)
        assert min(abs(rows[0].wind_dir_deg), abs(rows[0].wind_dir_deg - 360)) < 0.01
        assert rows[0].rain_hour_mm == pytest.approx(1.5)
    finally:
        session.close()


def test_rain_counter_reset_uses_conservative_lower_bound(weather_db):
    from app.models.weather import WeatherReadingHourly
    from app.services.weather_retention import rollup_hourly_readings

    bucket = _old_bucket()
    _add_readings(weather_db, bucket, rain=(8.0, 0.7))
    rollup_hourly_readings(raw_retention_days=30)
    session = weather_db.get_weather_session()
    try:
        row = session.query(WeatherReadingHourly).filter(
            WeatherReadingHourly.org_id == 1,
            WeatherReadingHourly.station_id == 7,
        ).one()
        assert row.rain_hour_mm == pytest.approx(8.0)
    finally:
        session.close()


def test_raw_rows_are_not_deleted_when_rollup_commit_fails(weather_db, monkeypatch):
    from app.models.weather import WeatherReading, WeatherReadingHourly
    from app.services import weather_retention

    bucket = _old_bucket()
    _add_readings(weather_db, bucket)

    def fail_upsert(_session, _values):
        raise RuntimeError("simulated rollup failure")

    monkeypatch.setattr(weather_retention, "_upsert_hourly", fail_upsert)
    with pytest.raises(RuntimeError, match="simulated rollup failure"):
        weather_retention.rollup_hourly_readings(raw_retention_days=30)

    session = weather_db.get_weather_session()
    try:
        assert session.query(WeatherReading).filter(WeatherReading.org_id == 1).count() == 2
        assert session.query(WeatherReadingHourly).filter(WeatherReadingHourly.org_id == 1).count() == 0
    finally:
        session.close()
