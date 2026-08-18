"""Einheitlicher Zugriff auf rohe und stündlich verdichtete Wetterhistorie."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db_weather import run_weather_query, weather_db_enabled

_METRICS = (
    "temp_c", "hum_pct", "wind_ms", "gust_ms", "pressure_hpa",
    "rain_rate_mmh", "dewpoint_c", "solar_wm2", "uv",
)


@dataclass(frozen=True)
class WeatherHistoryPoint:
    ts: datetime
    resolution: str
    sample_count: int
    values: dict[str, float | None]
    minima: dict[str, float | None]
    maxima: dict[str, float | None]


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def get_weather_history(
    org_id: int,
    station_id: int,
    start: datetime,
    end: datetime,
) -> list[WeatherHistoryPoint]:
    """Liefert ältere Stundenmittel und neuere Rohwerte chronologisch.

    ``values`` enthält bei Rohwerten den Istwert, bei Stundenwerten den Mittelwert.
    ``minima``/``maxima`` entsprechen bei Rohwerten ebenfalls dem Istwert.
    """
    if not weather_db_enabled() or end <= start:
        return []
    from app.models.weather import WeatherReading, WeatherReadingHourly

    start_naive = _naive_utc(start)
    end_naive = _naive_utc(end)
    raw_boundary = (
        datetime.now(UTC).replace(tzinfo=None)
        - timedelta(days=settings.WEATHER_READING_RAW_RETENTION_DAYS)
    ).replace(minute=0, second=0, microsecond=0)
    points: list[WeatherHistoryPoint] = []
    hourly_end = min(end_naive, raw_boundary)
    if start_naive < hourly_end:
        rows = run_weather_query(
            lambda session: session.query(WeatherReadingHourly).filter(
                WeatherReadingHourly.org_id == org_id,
                WeatherReadingHourly.station_id == station_id,
                WeatherReadingHourly.bucket_start >= start_naive,
                WeatherReadingHourly.bucket_start < hourly_end,
            ).all(),
            default=[],
        )
        for row in rows:
            values = {metric: getattr(row, f"{metric}_avg") for metric in _METRICS}
            values.update(rain_hour_mm=row.rain_hour_mm, wind_dir_deg=row.wind_dir_deg)
            points.append(WeatherHistoryPoint(
                ts=row.bucket_start,
                resolution="hourly",
                sample_count=row.sample_count,
                values=values,
                minima={metric: getattr(row, f"{metric}_min") for metric in _METRICS},
                maxima={metric: getattr(row, f"{metric}_max") for metric in _METRICS},
            ))

    raw_start = max(start_naive, raw_boundary)
    if raw_start < end_naive:
        rows = run_weather_query(
            lambda session: session.query(WeatherReading).filter(
                WeatherReading.org_id == org_id,
                WeatherReading.station_id == station_id,
                WeatherReading.ts >= raw_start,
                WeatherReading.ts < end_naive,
            ).all(),
            default=[],
        )
        for row in rows:
            values = {metric: getattr(row, metric) for metric in _METRICS}
            values.update(rain_day_mm=row.rain_day_mm, wind_dir_deg=row.wind_dir_deg)
            points.append(WeatherHistoryPoint(
                ts=row.ts,
                resolution="raw",
                sample_count=1,
                values=values,
                minima=dict(values),
                maxima=dict(values),
            ))
    return sorted(points, key=lambda point: point.ts)
