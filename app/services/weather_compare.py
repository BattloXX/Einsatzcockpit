"""Punktvergleich vollständiger Stundenfenster; ohne Einfluss auf Alarmregeln.

Keine Flächeninterpolation und keine Genauigkeits-/Wahrscheinlichkeitsaussagen.
Open-Meteo-GeoSphere und GeoSphere direkt sind keine unabhängigen Modelle.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakKeyDictionary

from app.config import settings
from app.services import kachelmann_service, shared_cache
from app.services import weather_service as ws
from app.services.weather_timeseries import window_max, window_sum

_locks: WeakKeyDictionary = WeakKeyDictionary()


def evaluate(results: dict[str, Any], start: datetime, hours: int) -> dict:
    """Alle Anbieter am identischen valid_at-Raster und getrennt je Größe prüfen."""
    rows = []
    excluded = []
    expected = {start + timedelta(hours=i) for i in range(1, hours + 1)}
    end = start + timedelta(hours=hours)

    def aligned(series):
        # Keine Teilstunden zwischen abweichenden valid_at-Rastern vergleichen.
        return all(t in expected for t, _ in series if start < t and t - timedelta(hours=1) < end)

    for name, result in results.items():
        row: dict[str, Any] = {"provider": name, "precipitation_mm": None, "gust_max_ms": None}
        if not isinstance(result, ws.ForecastResult):
            excluded.append({"provider": name,
                             "reason": result if isinstance(result, str) else "Daten nicht verfügbar"})
            continue
        rain, rain_ok = window_sum(result.precipitation_hourly, start, hours)
        gust, _, gust_ok = window_max(result.gust_hourly, start, hours)
        rain_ok = rain_ok and aligned(result.precipitation_hourly)
        gust_ok = gust_ok and aligned(result.gust_hourly)
        for variable, value, complete, label in (
            ("precipitation_mm", rain, rain_ok, "Niederschlag"),
            ("gust_max_ms", gust, gust_ok, "Böenmaximum"),
        ):
            if complete and value is not None:
                row[variable] = value
            else:
                excluded.append({"provider": name,
                                 "reason": f"{label}: Stundenreihe unvollständig oder abweichendes Zeitraster"})
        if rain_ok or gust_ok:
            rows.append(row)
    differences = {}
    hints = []
    for variable, threshold, label in (
        ("precipitation_mm", settings.WEATHER_COMPARE_WARN_PRECIP_MM, "Niederschlag"),
        ("gust_max_ms", settings.WEATHER_COMPARE_WARN_GUST_MS, "Böenmaximum"),
    ):
        values = [row[variable] for row in rows if row[variable] is not None]
        difference = max(values) - min(values) if len(values) >= 2 else None
        differences[variable] = difference
        if difference is not None and difference > threshold:
            hints.append(f"{label}: große Abweichung zwischen den Anbietern. Entwicklung beobachten.")
    return {"providers": rows, "excluded": excluded, "window_start": start,
            "window_end": start + timedelta(hours=hours), "differences": differences,
            "deviation_hint": " ".join(hints) or None}


async def _fetch(lat: float, lng: float, hours: int, org_id: int) -> dict[str, Any]:
    async def bounded(fn, *args, **kwargs):
        try:
            async with asyncio.timeout(settings.WEATHER_HTTP_TIMEOUT * (1 + settings.WEATHER_HTTP_RETRIES) + 1):
                return await fn(*args, **kwargs)
        except Exception:
            return None

    calls = {
        "geosphere": bounded(ws._fetch_geosphere_nwp, lat, lng, (hours,)),
        "open-meteo-geosphere": bounded(ws._openmeteo_forecast, lat, lng, (hours,), model="geosphere_seamless"),
        "open-meteo": bounded(ws._openmeteo_forecast, lat, lng, (hours,), model="best_match"),
    }
    configured = kachelmann_service.is_configured(org_id)
    if configured:
        calls["kachelmann"] = bounded(kachelmann_service.fetch_forecast, lat, lng, (hours,), org_id=org_id)
    values = await asyncio.gather(*calls.values())
    result = dict(zip(calls, values, strict=True))
    if not configured:
        result["kachelmann"] = "Für diese Organisation nicht konfiguriert"
    return result


async def compare(lat: float, lng: float, hours: int, org_id: int) -> dict:
    """Fail-open, Org-getrennter Cache und lokale/Worker-Sperre, begrenzte HTTP-Zeit."""
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    empty = evaluate({}, start, hours if 1 <= hours <= 24 else 6)
    try:
        if not settings.WEATHER_ENABLED or not 1 <= hours <= 24:
            return empty
        key = repr(("compare-v1", org_id, lat, lng, hours, start.isoformat(),
                    settings.WEATHER_NWP_RESOURCE, settings.OPENMETEO_BASE_URL,
                    kachelmann_service.is_configured(org_id),
                    settings.WEATHER_COMPARE_WARN_PRECIP_MM, settings.WEATHER_COMPARE_WARN_GUST_MS))
        locks = _locks.setdefault(asyncio.get_running_loop(), {})
        budget = settings.WEATHER_HTTP_TIMEOUT * (1 + settings.WEATHER_HTTP_RETRIES) + 2
        async with asyncio.timeout(budget), locks.setdefault(key, asyncio.Lock()):
            async with shared_cache.lock(key):
                cached = await ws._shared_cache_get(key)
                if cached is not None:
                    return cached
                result = evaluate(await _fetch(lat, lng, hours, org_id), start, hours)
                result.update(lat=lat, lng=lng)
                await ws._shared_cache_set(key, result, settings.WEATHER_CACHE_TTL_NWP)
                return result
    except Exception:
        empty["excluded"] = [{"provider": "Vergleich", "reason": "Daten derzeit nicht verfügbar"}]
        return empty
