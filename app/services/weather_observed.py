"""Gemessener Niederschlag aus der Wetter-DB, getrennt von Prognosen."""
import logging
from datetime import UTC, datetime, timedelta

from app.db_weather import run_weather_query, weather_db_enabled
from app.models.weather import WeatherReadingHourly
from app.services.weather_timeseries import utc, window_sum

logger = logging.getLogger("einsatzleiter.weather")


def observed_precip(
    station_id: int, hours: int, now: datetime | None = None, *, org_id: int | None = None,
) -> tuple[float | None, bool]:
    """Liefert (mm, vollständig) für [now-hours, now] aus rain_hour_mm.

    bucket_start ist naive UTC und bezeichnet den Stundenanfang. Teilstunden
    werden wie Prognosen proportional gewichtet. Fehlende/NULL-Buckets und eine
    deaktivierte, leere oder unerreichbare Wetter-DB liefern (None, False).
    Ohne org_id wird die Organisation über die global eindeutige Stations-ID
    ermittelt; die Wetter-Abfrage wird immer explizit nach Organisation gefiltert.
    """
    end = utc(now if now is not None else datetime.now(UTC))
    if hours <= 0:
        raise ValueError("Stunden müssen positiv sein")
    if not weather_db_enabled():
        return None, False
    if org_id is None:
        from app.db import SessionLocal
        from app.models.weather import WeatherStation

        try:
            with SessionLocal() as db:
                db.info["current_org_id"] = None
                station = db.query(WeatherStation).filter(WeatherStation.id == station_id).first()
                if station is None:
                    return None, False
                org_id = station.org_id
        except Exception as exc:
            logger.warning("Wetterstation konnte nicht ermittelt werden: %s", exc)
            return None, False
    if org_id is None:
        return None, False
    start = end - timedelta(hours=hours)
    rows = run_weather_query(
        lambda session: session.query(WeatherReadingHourly).filter(
            WeatherReadingHourly.org_id == org_id,
            WeatherReadingHourly.station_id == station_id,
            WeatherReadingHourly.bucket_start >= start.replace(minute=0, second=0, microsecond=0, tzinfo=None),
            WeatherReadingHourly.bucket_start < end.replace(tzinfo=None),
        ).all(),
        default=[],
    )
    series = [
        (row.bucket_start.replace(tzinfo=UTC) + timedelta(hours=1), row.rain_hour_mm)
        for row in rows
    ]
    return window_sum(series, start, hours)
