"""Zweistufige Retention der Wetterstations-Zeitreihe.

Alte Rohwerte werden stundenweise verdichtet und erst nach dem erfolgreichen
Commit des Rollups gelöscht. Alle Datumswerte sind naive UTC.
"""
import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from itertools import groupby

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db_weather import get_weather_session, weather_db_enabled

logger = logging.getLogger("einsatzleiter.weather")

_CHUNK_SIZE = 5000
_PURGE_HOUR = 3
_PURGE_MINUTE = 30
_ROLLUP_SAFETY_HOURS = 2
_ROLLUP_BUCKET_LIMIT = 1000
_STAT_FIELDS = (
    "temp_c", "hum_pct", "wind_ms", "gust_ms", "pressure_hpa",
    "dewpoint_c", "solar_wm2", "uv", "rain_rate_mmh",
)

try:
    from zoneinfo import ZoneInfo
    _VIENNA_TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover
    _VIENNA_TZ = UTC  # type: ignore[assignment]


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hour_start(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0, tzinfo=None)


def _stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return min(values), sum(values) / len(values), max(values)


def _rain_delta(readings: list) -> float | None:
    values = [r.rain_day_mm for r in readings if r.rain_day_mm is not None]
    if not values:
        return None
    if any(current < previous for previous, current in zip(values, values[1:])):
        # Ein Reset innerhalb der Stunde macht die tatsächliche Summe unbekannt.
        # max() ist eine konservative Untergrenze fuer den Anteil nach dem Reset.
        return max(values)
    return max(0.0, max(values) - min(values))


def _wind_direction(readings: list) -> float | None:
    directional = [r for r in readings if r.wind_dir_deg is not None]
    if not directional:
        return None
    weighted = [r for r in directional if r.wind_ms is not None and r.wind_ms > 0]
    source = weighted or directional
    x = sum(math.cos(math.radians(r.wind_dir_deg)) * (r.wind_ms if weighted else 1.0) for r in source)
    y = sum(math.sin(math.radians(r.wind_dir_deg)) * (r.wind_ms if weighted else 1.0) for r in source)
    if math.isclose(x, 0.0, abs_tol=1e-12) and math.isclose(y, 0.0, abs_tol=1e-12):
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


def _aggregate_bucket(readings: list, bucket_start: datetime) -> dict:
    first = readings[0]
    values = {
        "org_id": first.org_id,
        "station_id": first.station_id,
        "bucket_start": bucket_start,
        "sample_count": len(readings),
        "rain_hour_mm": _rain_delta(readings),
        "wind_dir_deg": _wind_direction(readings),
    }
    for field in _STAT_FIELDS:
        minimum, average, maximum = _stats([
            value for r in readings if (value := getattr(r, field)) is not None
        ])
        values[f"{field}_min"] = minimum
        values[f"{field}_avg"] = average
        values[f"{field}_max"] = maximum
    return values


def _upsert_hourly(session, values: dict) -> None:
    from app.models.weather import WeatherReadingHourly

    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        sqlite_statement = sqlite_insert(WeatherReadingHourly).values(**values)
        updates = {
            key: getattr(sqlite_statement.excluded, key)
            for key in values
            if key not in {"org_id", "station_id", "bucket_start"}
        }
        sqlite_upsert = sqlite_statement.on_conflict_do_update(
            index_elements=["org_id", "station_id", "bucket_start"], set_=updates
        )
        session.execute(sqlite_upsert)
    else:
        mysql_statement = mysql_insert(WeatherReadingHourly).values(**values)
        updates = {
            key: getattr(mysql_statement.inserted, key)
            for key in values
            if key not in {"org_id", "station_id", "bucket_start"}
        }
        mysql_upsert = mysql_statement.on_duplicate_key_update(**updates)
        session.execute(mysql_upsert)


def rollup_hourly_readings(
    raw_retention_days: int | None = None,
    safety_hours: int = _ROLLUP_SAFETY_HOURS,
) -> int:
    """Verdichtet abgelaufene Rohdaten; gibt die Zahl verarbeiteter Buckets zurück."""
    if not weather_db_enabled():
        return 0
    days = raw_retention_days if raw_retention_days is not None else settings.WEATHER_READING_RAW_RETENTION_DAYS
    if days <= 0:
        return 0

    from app.models.weather import WeatherReading

    cutoff = _hour_start(min(
        _naive_utc_now() - timedelta(days=days),
        _naive_utc_now() - timedelta(hours=safety_hours),
    ))
    session = get_weather_session()
    processed = 0
    try:
        # Wartungsjob ueber alle Tenants: org_id wird aus jeder Quellzeile
        # uebernommen; jede Bucket-Abfrage und -Loeschung ist danach explizit gescoped.
        candidates = (
            session.query(WeatherReading.org_id, WeatherReading.station_id, WeatherReading.ts)
            .filter(WeatherReading.ts < cutoff)
            .order_by(WeatherReading.org_id, WeatherReading.station_id, WeatherReading.ts)
            .all()
        )
        keys = []
        for key, _rows in groupby(candidates, key=lambda row: (row.org_id, row.station_id, _hour_start(row.ts))):
            keys.append(key)
            if len(keys) >= _ROLLUP_BUCKET_LIMIT:
                break

        for org_id, station_id, bucket_start in keys:
            bucket_end = bucket_start + timedelta(hours=1)
            readings = (
                session.query(WeatherReading)
                .filter(
                    WeatherReading.org_id == org_id,
                    WeatherReading.station_id == station_id,
                    WeatherReading.ts >= bucket_start,
                    WeatherReading.ts < bucket_end,
                )
                .order_by(WeatherReading.ts, WeatherReading.id)
                .all()
            )
            if not readings:
                continue
            _upsert_hourly(session, _aggregate_bucket(readings, bucket_start))
            session.commit()
            # Crash-Sicherheit: erst der erfolgreiche Rollup-Commit, dann die
            # explizit tenant- und bucket-gescopte Loeschung in eigener Transaktion.
            session.query(WeatherReading).filter(
                WeatherReading.org_id == org_id,
                WeatherReading.station_id == station_id,
                WeatherReading.ts >= bucket_start,
                WeatherReading.ts < bucket_end,
            ).delete(synchronize_session=False)
            session.commit()
            processed += 1
    except Exception:
        session.rollback()
        logger.exception("Wetter-Retention: Stunden-Rollup fehlgeschlagen")
        raise
    finally:
        session.close()
    return processed


def purge_old_readings(retention_days: int | None = None, chunk_size: int = _CHUNK_SIZE) -> int:
    """Entfernt nur Rohwerte, deren stündlicher Rollup bereits existiert."""
    if not weather_db_enabled():
        return 0
    days = retention_days if retention_days is not None else settings.WEATHER_READING_RAW_RETENTION_DAYS
    if days <= 0:
        return 0

    from app.models.weather import WeatherReading, WeatherReadingHourly

    cutoff = _naive_utc_now() - timedelta(days=days)
    total = 0
    session = get_weather_session()
    try:
        candidates = session.query(WeatherReading).filter(WeatherReading.ts < cutoff).all()
        for offset in range(0, len(candidates), chunk_size):
            for reading in candidates[offset:offset + chunk_size]:
                bucket_start = _hour_start(reading.ts)
                exists = session.query(WeatherReadingHourly.id).filter(
                    WeatherReadingHourly.org_id == reading.org_id,
                    WeatherReadingHourly.station_id == reading.station_id,
                    WeatherReadingHourly.bucket_start == bucket_start,
                ).first()
                if exists:
                    session.delete(reading)
                    total += 1
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Wetter-Retention: Löschen fehlgeschlagen")
        raise
    finally:
        session.close()
    return total


def purge_old_hourly_readings(retention_days: int | None = None, chunk_size: int = _CHUNK_SIZE) -> int:
    """Löscht abgelaufene Stundenwerte in Chunks."""
    if not weather_db_enabled():
        return 0
    days = retention_days if retention_days is not None else settings.WEATHER_READING_HOURLY_RETENTION_DAYS
    if days <= 0:
        return 0
    from app.models.weather import WeatherReadingHourly
    cutoff = _naive_utc_now() - timedelta(days=days)
    total = 0
    session = get_weather_session()
    try:
        while True:
            rows = session.query(WeatherReadingHourly).filter(
                WeatherReadingHourly.bucket_start < cutoff
            ).limit(chunk_size).all()
            if not rows:
                break
            for row in rows:
                session.delete(row)
            session.commit()
            total += len(rows)
            if len(rows) < chunk_size:
                break
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return total


def purge_old_abfluss_readings(retention_days: int | None = None, chunk_size: int = _CHUNK_SIZE) -> int:
    """Löscht Pegelmessungen unverändert nach dem Legacy-Retentionwert."""
    if not weather_db_enabled():
        return 0
    days = retention_days if retention_days is not None else settings.WEATHER_READING_RETENTION_DAYS
    if days <= 0:
        return 0
    from app.models.weather import AbflussReading
    cutoff = _naive_utc_now() - timedelta(days=days)
    total = 0
    session = get_weather_session()
    try:
        while True:
            rows = session.query(AbflussReading).filter(AbflussReading.ts < cutoff).limit(chunk_size).all()
            if not rows:
                break
            for row in rows:
                session.delete(row)
            session.commit()
            total += len(rows)
            if len(rows) < chunk_size:
                break
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return total


def _seconds_until_next(hour: int, minute: int) -> float:
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def weather_retention_loop() -> None:
    if not weather_db_enabled():
        logger.info("Wetter-Retention deaktiviert (keine Wetter-DB konfiguriert).")
        return
    while True:
        await asyncio.sleep(_seconds_until_next(_PURGE_HOUR, _PURGE_MINUTE))
        try:
            await asyncio.to_thread(rollup_hourly_readings)
            await asyncio.to_thread(purge_old_readings)
            await asyncio.to_thread(purge_old_hourly_readings)
            await asyncio.to_thread(purge_old_abfluss_readings)
        except Exception:
            logger.exception("Fehler im Wetter-Retention-Loop")
