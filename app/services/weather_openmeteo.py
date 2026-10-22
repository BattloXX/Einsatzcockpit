"""Gemeinsamer Open-Meteo-HTTP-Client; Einheiten und Zeitparameter bleiben explizit.

Das Modell geosphere_seamless kombiniert AROME (2,5 km, ca. 60 h) mit ECMWF IFS.
Die Modellwahl und die Zusammenführung erfolgen bei Open-Meteo.
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.weather")


async def _fetch(lat: float, lng: float, params: dict[str, Any], model: str | None) -> dict | None:
    params = {"latitude": lat, "longitude": lng, **params}
    if model:
        params["models"] = model
    try:
        async with httpx.AsyncClient(timeout=settings.WEATHER_HTTP_TIMEOUT) as client:
            response = await client.get(settings.OPENMETEO_BASE_URL, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("Open-Meteo-Abfrage fehlgeschlagen: %s", exc)
        return None


async def fetch_hourly(lat: float, lng: float, hours: int, model: str | None = None) -> dict | None:
    variables = "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation"
    if model:
        variables += ",relative_humidity_2m,wind_direction_10m,snowfall,weather_code"
    return await _fetch(lat, lng, {
        "hourly": variables,
        "forecast_hours": hours,
        "timeformat": "iso8601",
        "timezone": "UTC",
    }, model)


async def fetch_daily(lat: float, lng: float, model: str | None = None) -> dict | None:
    variables = "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    variables += "wind_gusts_10m_max,weather_code" if model else "wind_speed_10m_max"
    return await _fetch(lat, lng, {
        "daily": variables,
        "forecast_days": 7,
        "timezone": "Europe/Vienna",
    }, model)


async def fetch_current(lat: float, lng: float, model: str | None = None) -> dict | None:
    variables = (
        "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
        "wind_gusts_10m,precipitation"
    )
    if model:
        variables += ",weather_code"
    return await _fetch(lat, lng, {"current": variables, "timezone": "UTC"}, model)


async def fetch_minutely15(lat: float, lng: float, model: str | None = None) -> dict | None:
    return await _fetch(lat, lng, {
        "minutely_15": "precipitation,temperature_2m,wind_speed_10m,wind_gusts_10m",
        "forecast_minutely_15": 52,
        "timeformat": "iso8601",
        "timezone": "UTC",
    }, model)
