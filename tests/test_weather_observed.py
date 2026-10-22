"""Messrückblick: echte Wetter-DB-Abfragen ohne Prognosevermischung."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import db_weather
from app.models.weather import WeatherReadingHourly
from app.services import weather_observed

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def weather_db(monkeypatch):
    engine = create_engine("sqlite://")
    WeatherReadingHourly.__table__.create(engine)
    monkeypatch.setattr(db_weather, "weather_db_enabled", lambda: True)
    monkeypatch.setattr(weather_observed, "weather_db_enabled", lambda: True)
    monkeypatch.setattr(db_weather, "get_weather_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def insert(engine, values, org_id=7, station_id=3, id_start=1):
    with Session(engine) as session:
        for i, value in enumerate(values):
            session.add(WeatherReadingHourly(
                id=id_start + i, org_id=org_id, station_id=station_id,
                bucket_start=(NOW - timedelta(hours=3-i)).replace(tzinfo=None),
                sample_count=60, rain_hour_mm=value,
            ))
        session.commit()


def test_sum_uses_hourly_amount_and_explicit_org_station_filter(weather_db):
    insert(weather_db, [1, 2, 3])
    insert(weather_db, [90, 90, 90], org_id=8, id_start=10)
    insert(weather_db, [80, 80, 80], station_id=4, id_start=20)
    assert weather_observed.observed_precip(3, 3, NOW, org_id=7) == (6, True)


@pytest.mark.parametrize("values", [[1, None, 3], [1, 2], []])
def test_missing_null_or_empty_is_not_partial(weather_db, values):
    insert(weather_db, values)
    assert weather_observed.observed_precip(3, 3, NOW, org_id=7) == (None, False)


def test_partial_hours_and_offset(weather_db):
    insert(weather_db, [2, 4, 8])
    now = datetime.fromisoformat("2026-10-22T13:30:00+02:00")
    assert weather_observed.observed_precip(3, 2, now, org_id=7) == (9, True)


def test_disabled_db(monkeypatch):
    monkeypatch.setattr(weather_observed, "weather_db_enabled", lambda: False)
    assert weather_observed.observed_precip(3, 3, NOW) == (None, False)


def test_unreachable_db(weather_db, monkeypatch, caplog):
    def fail():
        raise RuntimeError("offline")
    monkeypatch.setattr(db_weather, "get_weather_session", fail)
    assert weather_observed.observed_precip(3, 3, NOW, org_id=7) == (None, False)
    assert "offline" in caplog.text


def test_org_resolved_from_station(weather_db):
    insert(weather_db, [1, 2, 3])
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value.org_id = 7
    with patch("app.db.SessionLocal") as session:
        session.return_value.__enter__.return_value = db
        assert weather_observed.observed_precip(3, 3, NOW) == (6, True)


def test_unknown_station(weather_db):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with patch("app.db.SessionLocal") as session:
        session.return_value.__enter__.return_value = db
        assert weather_observed.observed_precip(999, 3, NOW) == (None, False)


def test_now_default(weather_db, monkeypatch):
    insert(weather_db, [1, 2, 3])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(weather_observed, "datetime", Clock)
    assert weather_observed.observed_precip(3, 3, org_id=7) == (6, True)
