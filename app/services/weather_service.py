"""Weather data service — GeoSphere Austria Data Hub (primary) + Open-Meteo (fallback).

Provides: Nowcast (15-min precipitation), current conditions, NWP forecasts (+6/+12/+24h).
All external calls are cached in-process per configured TTL to respect rate limits.
Attribution (CC BY 4.0 required): use GEOSPHERE_ATTRIBUTION in any UI showing this data.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.weather")

GEOSPHERE_ATTRIBUTION = "GeoSphere Austria (CC BY 4.0)"

# ── In-process TTL cache ──────────────────────────────────────────────────────

_cache: dict[str, tuple[Any, float]] = {}


def _cache_key(resource: str, lat: float, lng: float) -> str:
    return f"{resource}:{round(lat, 3)}:{round(lng, 3)}"


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expires_ts = entry
    if datetime.now(UTC).timestamp() > expires_ts:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int) -> None:
    _cache[key] = (value, datetime.now(UTC).timestamp() + ttl)


def has_cached(lat: float, lng: float) -> bool:
    """Returns True if nowcast data is cached for this location (no fetch)."""
    return _cache_get(_cache_key("nowcast", lat, lng)) is not None


def get_cached_nowcast(lat: float, lng: float) -> "NowcastResult | None":
    """Returns cached NowcastResult without triggering a fetch."""
    return _cache_get(_cache_key("nowcast", lat, lng))


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class NowcastStep:
    timestamp: datetime
    precipitation_mm: float
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None


@dataclass
class NowcastResult:
    steps: list[NowcastStep]
    peak_mm: float
    peak_at: datetime | None
    total_mm: float
    trend: str          # "increasing" | "decreasing" | "stable"
    source: str         # "geosphere" | "openmeteo"


@dataclass
class CurrentWeather:
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    humidity_pct: float | None = None
    precipitation_1h_mm: float | None = None
    source: str = ""


@dataclass
class ForecastHorizon:
    hours: int
    precipitation_acc_mm: float | None = None   # accumulated from T+0
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None


@dataclass
class ForecastResult:
    horizons: list[ForecastHorizon]
    source: str = "geosphere_nwp"


@dataclass
class WeatherWarning:
    level: int
    event_type: str
    text: str
    valid_from: datetime
    valid_to: datetime
    region: str = ""


@dataclass
class ScenarioAlert:
    key: str        # "storm" | "wildfire"
    level: str      # "warn" | "danger"
    label_de: str
    detail_de: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_timestamps(raw: list[str]) -> list[datetime]:
    result = []
    for s in raw:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            result.append(dt)
        except (ValueError, AttributeError):
            logger.warning("Ungültiger Timestamp: %s", s)
    return result


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _wind_dir_from_uv(u: float | None, v: float | None) -> float | None:
    """Meteorological wind direction (FROM direction) from U/V components."""
    if u is None or v is None:
        return None
    try:
        return (270.0 - math.degrees(math.atan2(v, u))) % 360.0
    except (ValueError, ZeroDivisionError):
        return None


def _wind_speed_from_uv(u: float | None, v: float | None) -> float | None:
    if u is None or v is None:
        return None
    try:
        return math.sqrt(u * u + v * v)
    except (ValueError, TypeError):
        return None


def _compute_trend(values: list[float]) -> str:
    if len(values) < 4:
        return "stable"
    mid = len(values) // 2
    first_half = sum(values[:mid]) / mid
    second_half = sum(values[mid:]) / (len(values) - mid)
    diff = second_half - first_half
    if diff > 0.1:
        return "increasing"
    if diff < -0.1:
        return "decreasing"
    return "stable"


def _extract_param_data(feature: dict, param: str) -> list[Any]:
    """Extracts the data array for a named parameter from a GeoSphere feature."""
    params = feature.get("properties", {}).get("parameters", {})
    p = params.get(param)
    if p is None:
        return []
    data = p.get("data", [])
    return data if isinstance(data, list) else []


# ── GeoSphere HTTP client ──────────────────────────────────────────────────────

async def _geosphere_get(mode: str, resource: str, params: dict) -> dict | None:
    """Single GET to dataset.api.hub.geosphere.at, returns parsed JSON or None."""
    url = f"{settings.GEOSPHERE_BASE_URL}/timeseries/{mode}/{resource}"
    params = dict(params, output_format="geojson")
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Einsatzleiter-Hilfswerkzeug/2.x (weather)"},
            timeout=settings.WEATHER_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        logger.warning(
            "GeoSphere Timeout: %s (%s)", resource, params.get("lat_lon", "")
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "GeoSphere HTTP %s: %s (%s)",
            exc.response.status_code,
            resource,
            params.get("lat_lon", ""),
        )
    except Exception as exc:
        logger.warning("GeoSphere Fehler: %s", exc)
    return None


# ── Nowcast ────────────────────────────────────────────────────────────────────

async def get_nowcast(lat: float, lng: float) -> NowcastResult | None:
    """15-min precipitation nowcast for the next ~3 h.

    Primary: GeoSphere nowcast-v1-15min-1km.
    Fallback: Open-Meteo minutely_15 (if WEATHER_FALLBACK_OPENMETEO and GeoSphere fails).
    Returns None if both sources unavailable.
    """
    if not settings.WEATHER_ENABLED:
        return None

    key = _cache_key("nowcast", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = await _fetch_geosphere_nowcast(lat, lng)
    if result is None and settings.WEATHER_FALLBACK_OPENMETEO:
        result = await _openmeteo_nowcast(lat, lng)

    if result is not None:
        _cache_set(key, result, settings.WEATHER_CACHE_TTL_NOWCAST)
    return result


async def _fetch_geosphere_nowcast(lat: float, lng: float) -> NowcastResult | None:
    data = await _geosphere_get(
        "forecast",
        settings.WEATHER_NOWCAST_RESOURCE,
        {"parameters": "rr,t2m,ff,fx", "lat_lon": f"{lat},{lng}"},
    )
    if not data:
        return None

    features = data.get("features", [])
    if not features:
        logger.warning("GeoSphere Nowcast: leere features-Liste für %s,%s", lat, lng)
        return None

    feature = features[0]
    timestamps = _parse_timestamps(data.get("timestamps", []))
    if not timestamps:
        return None

    rr = _extract_param_data(feature, "rr")
    t2m = _extract_param_data(feature, "t2m")
    ff = _extract_param_data(feature, "ff")
    fx = _extract_param_data(feature, "fx")

    steps: list[NowcastStep] = []
    for i, ts in enumerate(timestamps):
        steps.append(NowcastStep(
            timestamp=ts,
            precipitation_mm=_safe_float(rr[i] if i < len(rr) else None) or 0.0,
            temperature_c=_safe_float(t2m[i] if i < len(t2m) else None),
            wind_speed_ms=_safe_float(ff[i] if i < len(ff) else None),
            gust_speed_ms=_safe_float(fx[i] if i < len(fx) else None),
        ))

    if not steps:
        return None

    prec = [s.precipitation_mm for s in steps]
    peak_mm = max(prec, default=0.0)
    peak_idx = prec.index(peak_mm) if peak_mm > 0 else None
    return NowcastResult(
        steps=steps,
        peak_mm=peak_mm,
        peak_at=steps[peak_idx].timestamp if peak_idx is not None else None,
        total_mm=sum(prec),
        trend=_compute_trend(prec),
        source="geosphere",
    )


async def _openmeteo_nowcast(lat: float, lng: float) -> NowcastResult | None:
    """Open-Meteo minutely_15 fallback for nowcast."""
    try:
        async with httpx.AsyncClient(timeout=settings.WEATHER_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "minutely_15": "precipitation,temperature_2m,wind_speed_10m,wind_gusts_10m",
                    "forecast_minutely_15": 52,
                    "timeformat": "iso8601",
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo Fallback fehlgeschlagen: %s", exc)
        return None

    m15 = data.get("minutely_15", {})
    timestamps = _parse_timestamps(m15.get("time", []))
    if not timestamps:
        return None

    prec_data = m15.get("precipitation", [])
    t_data = m15.get("temperature_2m", [])
    ws_data = m15.get("wind_speed_10m", [])    # km/h from Open-Meteo
    wg_data = m15.get("wind_gusts_10m", [])    # km/h

    steps: list[NowcastStep] = []
    for i, ts in enumerate(timestamps):
        ws_kmh = _safe_float(ws_data[i] if i < len(ws_data) else None)
        wg_kmh = _safe_float(wg_data[i] if i < len(wg_data) else None)
        steps.append(NowcastStep(
            timestamp=ts,
            precipitation_mm=_safe_float(prec_data[i] if i < len(prec_data) else None) or 0.0,
            temperature_c=_safe_float(t_data[i] if i < len(t_data) else None),
            wind_speed_ms=ws_kmh / 3.6 if ws_kmh is not None else None,
            gust_speed_ms=wg_kmh / 3.6 if wg_kmh is not None else None,
        ))

    if not steps:
        return None

    prec = [s.precipitation_mm for s in steps]
    peak_mm = max(prec, default=0.0)
    peak_idx = prec.index(peak_mm) if peak_mm > 0 else None
    return NowcastResult(
        steps=steps,
        peak_mm=peak_mm,
        peak_at=steps[peak_idx].timestamp if peak_idx is not None else None,
        total_mm=sum(prec),
        trend=_compute_trend(prec),
        source="openmeteo",
    )


# ── Current conditions ────────────────────────────────────────────────────────

async def get_current(lat: float, lng: float) -> CurrentWeather | None:
    """Current weather conditions (temperature, wind, gust, humidity).

    Primary: GeoSphere NWP t=0. Fallback: Open-Meteo current.
    """
    if not settings.WEATHER_ENABLED:
        return None

    key = _cache_key("current", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = await _fetch_current_from_nwp(lat, lng)
    if result is None and settings.WEATHER_FALLBACK_OPENMETEO:
        result = await _openmeteo_current(lat, lng)

    if result is not None:
        _cache_set(key, result, settings.WEATHER_CACHE_TTL_NOWCAST)
    return result


async def _openmeteo_current(lat: float, lng: float) -> CurrentWeather | None:
    """Open-Meteo fallback for current conditions."""
    try:
        async with httpx.AsyncClient(timeout=settings.WEATHER_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "current": (
                        "temperature_2m,relative_humidity_2m,"
                        "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation"
                    ),
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo Current Fallback fehlgeschlagen: %s", exc)
        return None

    c = data.get("current", {})
    ws_kmh = _safe_float(c.get("wind_speed_10m"))
    wg_kmh = _safe_float(c.get("wind_gusts_10m"))
    return CurrentWeather(
        temperature_c=_safe_float(c.get("temperature_2m")),
        humidity_pct=_safe_float(c.get("relative_humidity_2m")),
        wind_speed_ms=ws_kmh / 3.6 if ws_kmh is not None else None,
        gust_speed_ms=wg_kmh / 3.6 if wg_kmh is not None else None,
        wind_direction_deg=_safe_float(c.get("wind_direction_10m")),
        precipitation_1h_mm=_safe_float(c.get("precipitation")),
        source="openmeteo",
    )


async def _fetch_current_from_nwp(lat: float, lng: float) -> CurrentWeather | None:
    data = await _geosphere_get(
        "forecast",
        settings.WEATHER_NWP_RESOURCE,
        {"parameters": "u10m,v10m,ugust,vgust,t2m,rh2m", "lat_lon": f"{lat},{lng}"},
    )
    if not data:
        return None

    features = data.get("features", [])
    if not features:
        return None

    feature = features[0]

    def _first(lst: list) -> float | None:
        return _safe_float(lst[0]) if lst else None

    u0 = _first(_extract_param_data(feature, "u10m"))
    v0 = _first(_extract_param_data(feature, "v10m"))
    ug0 = _first(_extract_param_data(feature, "ugust"))
    vg0 = _first(_extract_param_data(feature, "vgust"))

    return CurrentWeather(
        temperature_c=_first(_extract_param_data(feature, "t2m")),
        wind_speed_ms=_wind_speed_from_uv(u0, v0),
        gust_speed_ms=_wind_speed_from_uv(ug0, vg0),
        wind_direction_deg=_wind_dir_from_uv(u0, v0),
        humidity_pct=_first(_extract_param_data(feature, "rh2m")),
        source="geosphere_nwp",
    )


# ── NWP Forecast ──────────────────────────────────────────────────────────────

async def get_forecast(
    lat: float,
    lng: float,
    horizons: tuple[int, ...] = (6, 12, 24),
) -> ForecastResult | None:
    """NWP multi-horizon forecast: accumulated precipitation, wind, temperature.

    Primary: GeoSphere NWP. Fallback: Open-Meteo hourly.
    horizons: list of hours from now (default +6, +12, +24).
    """
    if not settings.WEATHER_ENABLED:
        return None

    key = _cache_key("nwp", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = await _fetch_geosphere_nwp(lat, lng, horizons)
    if result is None and settings.WEATHER_FALLBACK_OPENMETEO:
        result = await _openmeteo_forecast(lat, lng, horizons)

    if result is not None:
        _cache_set(key, result, settings.WEATHER_CACHE_TTL_NWP)
    return result


async def _openmeteo_forecast(
    lat: float, lng: float, horizons: tuple[int, ...]
) -> ForecastResult | None:
    """Open-Meteo hourly fallback for multi-horizon forecast."""
    try:
        async with httpx.AsyncClient(timeout=settings.WEATHER_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation",
                    "forecast_hours": max(horizons) + 1,
                    "timeformat": "iso8601",
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo Forecast Fallback fehlgeschlagen: %s", exc)
        return None

    h_data = data.get("hourly", {})
    t2m = h_data.get("temperature_2m", [])
    ws = h_data.get("wind_speed_10m", [])    # km/h
    wg = h_data.get("wind_gusts_10m", [])    # km/h
    prec = h_data.get("precipitation", [])   # mm/h

    result_horizons: list[ForecastHorizon] = []
    for h in horizons:
        ws_kmh = _safe_float(ws[h]) if h < len(ws) else None
        wg_kmh = _safe_float(wg[h]) if h < len(wg) else None
        acc = sum(_safe_float(p) or 0.0 for p in prec[: h + 1]) if prec else None
        result_horizons.append(ForecastHorizon(
            hours=h,
            precipitation_acc_mm=acc,
            temperature_c=_safe_float(t2m[h]) if h < len(t2m) else None,
            wind_speed_ms=ws_kmh / 3.6 if ws_kmh is not None else None,
            gust_speed_ms=wg_kmh / 3.6 if wg_kmh is not None else None,
        ))

    return ForecastResult(horizons=result_horizons, source="openmeteo")


async def _fetch_geosphere_nwp(
    lat: float, lng: float, horizons: tuple[int, ...]
) -> ForecastResult | None:
    data = await _geosphere_get(
        "forecast",
        settings.WEATHER_NWP_RESOURCE,
        {"parameters": "rr_acc,u10m,v10m,ugust,vgust,t2m", "lat_lon": f"{lat},{lng}"},
    )
    if not data:
        return None

    features = data.get("features", [])
    if not features:
        return None

    feature = features[0]
    rr_acc = _extract_param_data(feature, "rr_acc")
    u10m = _extract_param_data(feature, "u10m")
    v10m = _extract_param_data(feature, "v10m")
    ugust = _extract_param_data(feature, "ugust")
    vgust = _extract_param_data(feature, "vgust")
    t2m = _extract_param_data(feature, "t2m")

    result_horizons: list[ForecastHorizon] = []
    for h in horizons:
        def _at(lst: list, idx: int) -> float | None:
            return _safe_float(lst[idx]) if idx < len(lst) else None

        u_h = _at(u10m, h)
        v_h = _at(v10m, h)
        ug_h = _at(ugust, h)
        vg_h = _at(vgust, h)
        result_horizons.append(ForecastHorizon(
            hours=h,
            precipitation_acc_mm=_at(rr_acc, h),
            temperature_c=_at(t2m, h),
            wind_speed_ms=_wind_speed_from_uv(u_h, v_h),
            gust_speed_ms=_wind_speed_from_uv(ug_h, vg_h),
        ))

    return ForecastResult(horizons=result_horizons, source="geosphere_nwp")


# ── Warnings (GeoSphere Warn-API) ─────────────────────────────────────────────

_WARN_LEVEL_COLORS = {1: "#fbbf24", 2: "#f97316", 3: "#ef4444", 4: "#a855f7"}
_WARN_EVENT_DE = {
    "RAIN": "Starkregen", "THUNDERSTORM": "Gewitter", "WIND": "Sturm",
    "SNOW": "Schneefall", "FROST": "Frost", "FOG": "Nebel",
    "AVALANCHE": "Lawinengefahr", "FLOOD": "Hochwasser",
}


async def get_warnings(lat: float, lng: float) -> list[WeatherWarning]:
    """Active weather warnings from GeoSphere Warn-API for the given location."""
    if not settings.WEATHER_ENABLED:
        return []

    key = _cache_key("warn", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = await _fetch_geosphere_warnings(lat, lng)
    _cache_set(key, result, settings.WEATHER_CACHE_TTL_WARN)
    return result


async def _fetch_geosphere_warnings(lat: float, lng: float) -> list[WeatherWarning]:
    url = f"{settings.GEOSPHERE_WARN_URL}/warnings/byLocation"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Einsatzleiter-Hilfswerkzeug/2.x (warnings)"},
            timeout=settings.WEATHER_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(url, params={"lat": lat, "lon": lng})
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("GeoSphere Warn-API Timeout: lat=%s lng=%s", lat, lng)
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning("GeoSphere Warn-API HTTP %s", exc.response.status_code)
        return []
    except Exception as exc:
        logger.warning("GeoSphere Warn-API Fehler: %s", exc)
        return []

    warnings: list[WeatherWarning] = []
    raw_list = data if isinstance(data, list) else data.get("warnings", [])
    for item in raw_list:
        try:
            level = int(item.get("level", item.get("severity", 1)))
            event = str(item.get("event", item.get("type", "UNKNOWN"))).upper()
            text = item.get("text", item.get("description", _WARN_EVENT_DE.get(event, event)))
            valid_from = datetime.fromisoformat(
                str(item.get("onset", item.get("valid_from", ""))).replace("Z", "+00:00")
            )
            valid_to = datetime.fromisoformat(
                str(item.get("expires", item.get("valid_to", ""))).replace("Z", "+00:00")
            )
            now = datetime.now(UTC)
            if valid_to < now:
                continue   # already expired
            warnings.append(WeatherWarning(
                level=level,
                event_type=_WARN_EVENT_DE.get(event, event),
                text=str(text),
                valid_from=valid_from,
                valid_to=valid_to,
                region=str(item.get("region", item.get("regionName", ""))),
            ))
        except Exception as exc:
            logger.warning("Warnung konnte nicht geparst werden: %s — %s", item, exc)

    return sorted(warnings, key=lambda w: -w.level)


# ── Scenario analysis (Storm / Wildfire) ─────────────────────────────────────

_BF8 = 17.2    # m/s — Beaufort 8: storm begins, twigs break
_BF10 = 24.5   # m/s — Beaufort 10: whole gale, trees uprooted

_WILDFIRE_TEMP = 30.0   # °C threshold
_WILDFIRE_HUM_WARN = 30.0   # % — warn level
_WILDFIRE_HUM_DANGER = 20.0  # % — danger level
_WILDFIRE_NO_RAIN = 0.5  # mm — nowcast total below this = "no rain"


def _storm_alerts(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,
) -> list[ScenarioAlert]:
    alerts: list[ScenarioAlert] = []
    active_gust = None
    if current:
        active_gust = current.gust_speed_ms or current.wind_speed_ms

    if active_gust is not None:
        if active_gust >= _BF10:
            alerts.append(ScenarioAlert(
                key="storm",
                level="danger",
                label_de="Schwerer Sturm",
                detail_de=f"Böe {active_gust:.0f} m/s (BF10+) – erhöhte Einsatzgefährdung",
            ))
        elif active_gust >= _BF8:
            alerts.append(ScenarioAlert(
                key="storm",
                level="warn",
                label_de="Sturmwarnung",
                detail_de=f"Böe {active_gust:.0f} m/s (BF8+) – Windgefahr beachten",
            ))

    if not alerts and forecast:
        for h in forecast.horizons:
            g = h.gust_speed_ms or h.wind_speed_ms or 0.0
            if g >= _BF8:
                alerts.append(ScenarioAlert(
                    key="storm",
                    level="warn",
                    label_de=f"Sturm in {h.hours}h",
                    detail_de=f"Erwartete Böe {g:.0f} m/s (BF8+) in +{h.hours}h",
                ))
                break

    return alerts


def _wildfire_alerts(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,  # noqa: ARG001  (reserved for future use)
    nowcast: NowcastResult | None,
) -> list[ScenarioAlert]:
    if not current or current.temperature_c is None or current.humidity_pct is None:
        return []

    temp = current.temperature_c
    hum = current.humidity_pct
    no_rain = nowcast is None or nowcast.total_mm < _WILDFIRE_NO_RAIN

    if temp >= _WILDFIRE_TEMP and hum <= _WILDFIRE_HUM_DANGER:
        return [ScenarioAlert(
            key="wildfire",
            level="danger",
            label_de="Hohe Waldbrandgefahr",
            detail_de=f"{temp:.0f}°C, {hum:.0f}% RF – Brandbedingungen kritisch",
        )]
    if temp >= _WILDFIRE_TEMP and hum <= _WILDFIRE_HUM_WARN and no_rain:
        return [ScenarioAlert(
            key="wildfire",
            level="warn",
            label_de="Erhöhte Waldbrandgefahr",
            detail_de=f"{temp:.0f}°C, {hum:.0f}% RF – trockene Bedingungen",
        )]
    return []


def analyze_weather(
    current: "CurrentWeather | None",
    forecast: "ForecastResult | None",
    nowcast: "NowcastResult | None" = None,
) -> list[ScenarioAlert]:
    """Returns scenario alerts (storm, wildfire) derived from current weather data."""
    return _storm_alerts(current, forecast) + _wildfire_alerts(current, forecast, nowcast)


# ── Nowcast grid stub (PR 4) ──────────────────────────────────────────────────

async def get_nowcast_grid(
    bbox: tuple[float, float, float, float],
) -> dict | None:
    """INCA nowcast grid as GeoJSON for map overlay. Full implementation in PR 4."""
    return None
