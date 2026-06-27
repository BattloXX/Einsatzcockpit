"""Weather data service — GeoSphere Austria Data Hub (primary) + Open-Meteo (fallback).

Provides: Nowcast (15-min precipitation), current conditions, NWP forecasts (+6/+12/+24h).
All external calls are cached in-process per configured TTL to respect rate limits.
Attribution (CC BY 4.0 required): use GEOSPHERE_ATTRIBUTION in any UI showing this data.
"""
import logging
import math
from dataclasses import dataclass
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


def get_cached_nowcast(lat: float, lng: float) -> NowcastResult | None:
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
class DailyForecastDay:
    date_label: str                    # z.B. "Mo 24.06."
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    precip_mm: float | None = None
    wind_max_ms: float | None = None   # m/s


@dataclass
class DailyForecast:
    days: list[DailyForecastDay]
    source: str = "openmeteo"


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
    key: str        # "storm" | "wildfire" | "rain" | "snow" | "thunder" | "ice"
    level: str      # "warn" | "danger"
    label_de: str
    detail_de: str
    icon: str = ""


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
            headers={"User-Agent": "Einsatzcockpit/2.x (weather)"},
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

    Primary: Kachelmann (wenn API-Key gesetzt), sonst GeoSphere NWP t=0.
    Fallback: GeoSphere NWP → Open-Meteo current.
    """
    if not settings.WEATHER_ENABLED:
        return None

    from app.services import kachelmann_service
    use_kachelmann = kachelmann_service.is_configured()
    key = _cache_key("current_k" if use_kachelmann else "current", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result: CurrentWeather | None = None
    if use_kachelmann:
        result = await kachelmann_service.fetch_current(lat, lng)
    if result is None:
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

    from app.services import kachelmann_service
    use_kachelmann = kachelmann_service.is_configured()
    key = _cache_key("nwp_k" if use_kachelmann else "nwp", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result: ForecastResult | None = None
    if use_kachelmann:
        result = await kachelmann_service.fetch_forecast(lat, lng, horizons)
    if result is None:
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

# warntypid (ZAMG-Warn-API) → event key used for label lookup
_WARN_TYPE_EVENT = {
    1: "WIND", 2: "RAIN", 3: "SNOW", 4: "ICE",
    5: "THUNDERSTORM", 6: "HEAT", 7: "FROST",
}
_WARN_EVENT_DE = {
    "RAIN": "Starkregen", "THUNDERSTORM": "Gewitter", "WIND": "Sturm",
    "SNOW": "Schneefall", "ICE": "Glatteis", "FROST": "Frost", "FOG": "Nebel",
    "AVALANCHE": "Lawinengefahr", "FLOOD": "Hochwasser", "HEAT": "Hitze",
}


try:
    from zoneinfo import ZoneInfo
    _VIENNA_TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - zoneinfo immer vorhanden ab 3.9
    _VIENNA_TZ = UTC


def _parse_warn_time(epoch: Any, text: Any) -> datetime | None:
    """Parst einen Warnungs-Zeitpunkt: bevorzugt Unix-Epoch, sonst dt-String.

    ZAMG liefert in rawinfo Unix-Sekunden (UTC) und daneben deutsche Strings
    "DD.MM.YYYY HH:MM" (Lokalzeit Europe/Vienna). ISO wird ebenfalls akzeptiert.
    """
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (ValueError, TypeError, OSError):
            pass
    s = str(text or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=_VIENNA_TZ)
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=_VIENNA_TZ)
        except ValueError:
            continue
    return None


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
    # GeoSphere Warn-API v1: /getWarningsForCoords returns a GeoJSON Feature
    url = f"{settings.GEOSPHERE_WARN_URL}/getWarningsForCoords"
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Einsatzcockpit/2.x (warnings)"},
            timeout=settings.WEATHER_HTTP_TIMEOUT,
        ) as client:
            resp = await client.get(url, params={"lat": lat, "lon": lng, "lang": "de"})
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

    props = data.get("properties", {})
    region = props.get("location", {}).get("properties", {}).get("name", "")
    raw_list = props.get("warnings", [])

    now = datetime.now(UTC)
    warnings: list[WeatherWarning] = []
    for raw_item in raw_list:
        item = {}
        try:
            # ZAMG-Warn-API: Felder liegen verschachtelt unter "properties"
            item = raw_item.get("properties", raw_item) if isinstance(raw_item, dict) else {}
            rawinfo = item.get("rawinfo") if isinstance(item.get("rawinfo"), dict) else {}

            level = int(item.get("warnstufeid") or rawinfo.get("wlevel") or 1)
            type_id = int(item.get("warntypid") or rawinfo.get("wtype") or 0)
            event = _WARN_TYPE_EVENT.get(type_id, "UNKNOWN")
            text = item.get("text") or item.get("meteotext") or _WARN_EVENT_DE.get(event, event)

            # Zeiten: bevorzugt Unix-Epoch aus rawinfo (UTC, eindeutig), sonst
            # deutsches Format "DD.MM.YYYY HH:MM" (Europe/Vienna) bzw. ISO.
            valid_from = _parse_warn_time(rawinfo.get("start"), item.get("begin"))
            valid_to = _parse_warn_time(rawinfo.get("end"), item.get("end"))
            if valid_to is not None and valid_to < now:
                continue   # already expired

            warnings.append(WeatherWarning(
                level=level,
                event_type=_WARN_EVENT_DE.get(event, event),
                text=str(text),
                valid_from=valid_from or now,
                valid_to=valid_to or now,
                region=region,
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

# Starkregen / Hochwasser — Schwellwerte
_RAIN_NOWCAST_WARN = 15.0    # mm Summe im ~3h-Nowcast
_RAIN_NOWCAST_DANGER = 30.0
_RAIN_FC6_WARN = 25.0        # mm akkumuliert bis +6h
_RAIN_FC6_DANGER = 40.0

# Schneefall / Schneelast — Schwellwerte (Niederschlag als Schnee bei Temp ≤ Grenze)
_SNOW_TEMP_MAX = 1.0         # °C — darunter fällt Niederschlag als Schnee
_SNOW_NOWCAST_WARN = 3.0     # mm Wasseräquivalent / ~3h  (≈ 3 cm Neuschnee)
_SNOW_NOWCAST_DANGER = 8.0   # mm  (≈ 8–10 cm Neuschnee)
_SNOW_FC6_WARN = 8.0         # mm akkumuliert bis +6h
_SNOW_FC6_DANGER = 15.0

# Glatteis / gefrierender Regen — Temperaturfenster
_ICE_TEMP_LOW = -1.0
_ICE_TEMP_HIGH = 1.0
_ICE_MIN_RAIN = 0.2          # mm Niederschlag im Nowcast nötig


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
                icon="⚡",
            ))
        elif active_gust >= _BF8:
            alerts.append(ScenarioAlert(
                key="storm",
                level="warn",
                label_de="Sturmwarnung",
                detail_de=f"Böe {active_gust:.0f} m/s (BF8+) – Windgefahr beachten",
                icon="⚡",
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
                    icon="⚡",
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
            icon="🔥",
        )]
    if temp >= _WILDFIRE_TEMP and hum <= _WILDFIRE_HUM_WARN and no_rain:
        return [ScenarioAlert(
            key="wildfire",
            level="warn",
            label_de="Erhöhte Waldbrandgefahr",
            detail_de=f"{temp:.0f}°C, {hum:.0f}% RF – trockene Bedingungen",
            icon="🔥",
        )]
    return []


def _fc_acc_at(forecast: ForecastResult | None, hours: int) -> float | None:
    """Akkumulierter Niederschlag bis zum Horizont <= hours (mm)."""
    if not forecast:
        return None
    best = None
    for h in forecast.horizons:
        if h.hours <= hours and h.precipitation_acc_mm is not None:
            best = h.precipitation_acc_mm
    return best


def _heavy_rain_alerts(
    nowcast: NowcastResult | None,
    forecast: ForecastResult | None,
) -> list[ScenarioAlert]:
    """Starkregen / Hochwasser – hohe Niederschlagsmengen kurzfristig/mittelfristig."""
    now_total = nowcast.total_mm if nowcast else None
    fc6 = _fc_acc_at(forecast, 6)

    is_danger = (now_total is not None and now_total >= _RAIN_NOWCAST_DANGER) or \
                (fc6 is not None and fc6 >= _RAIN_FC6_DANGER)
    is_warn = (now_total is not None and now_total >= _RAIN_NOWCAST_WARN) or \
              (fc6 is not None and fc6 >= _RAIN_FC6_WARN)

    if not (is_warn or is_danger):
        return []

    parts: list[str] = []
    if now_total is not None and now_total >= _RAIN_NOWCAST_WARN:
        parts.append(f"{now_total:.0f} mm in ~3h")
    if fc6 is not None and fc6 >= _RAIN_FC6_WARN:
        parts.append(f"{fc6:.0f} mm bis +6h")
    detail = " · ".join(parts) or "ergiebiger Niederschlag erwartet"

    if is_danger:
        return [ScenarioAlert(
            key="rain", level="danger",
            label_de="Starkregen / Hochwassergefahr",
            detail_de=f"{detail} – Überflutung/Vermurung möglich",
            icon="🌊",
        )]
    return [ScenarioAlert(
        key="rain", level="warn",
        label_de="Ergiebiger Regen",
        detail_de=f"{detail} – Niederschlag beobachten",
        icon="🌊",
    )]


def _snow_alerts(
    current: CurrentWeather | None,
    nowcast: NowcastResult | None,
    forecast: ForecastResult | None,
) -> list[ScenarioAlert]:
    """Schneefall / Schneelast – Niederschlag bei Temperaturen um/unter dem Gefrierpunkt."""
    temp = current.temperature_c if current else None
    # Temperatur ggf. aus Nowcast-Step ableiten, wenn kein current-Wert
    if temp is None and nowcast and nowcast.steps:
        temps = [s.temperature_c for s in nowcast.steps if s.temperature_c is not None]
        temp = min(temps) if temps else None
    if temp is None or temp > _SNOW_TEMP_MAX:
        return []

    now_total = nowcast.total_mm if nowcast else None
    fc6 = _fc_acc_at(forecast, 6)

    is_danger = (now_total is not None and now_total >= _SNOW_NOWCAST_DANGER) or \
                (fc6 is not None and fc6 >= _SNOW_FC6_DANGER)
    is_warn = (now_total is not None and now_total >= _SNOW_NOWCAST_WARN) or \
              (fc6 is not None and fc6 >= _SNOW_FC6_WARN)

    if not (is_warn or is_danger):
        return []

    parts: list[str] = []
    if now_total is not None and now_total >= _SNOW_NOWCAST_WARN:
        parts.append(f"~{now_total:.0f} mm Wasseräqu. in 3h")
    if fc6 is not None and fc6 >= _SNOW_FC6_WARN:
        parts.append(f"~{fc6:.0f} mm bis +6h")
    detail = " · ".join(parts) or "kräftiger Schneefall"

    if is_danger:
        return [ScenarioAlert(
            key="snow", level="danger",
            label_de="Kräftiger Schneefall / Schneelast",
            detail_de=f"{temp:.0f}°C, {detail} – Dachlast/Verkehr beachten",
            icon="❄️",
        )]
    return [ScenarioAlert(
        key="snow", level="warn",
        label_de="Schneefall",
        detail_de=f"{temp:.0f}°C, {detail}",
        icon="❄️",
    )]


def _thunderstorm_alerts(
    warnings: list[WeatherWarning] | None,
) -> list[ScenarioAlert]:
    """Gewitter – aus amtlichen ZAMG-Warnungen abgeleitet."""
    if not warnings:
        return []
    for w in warnings:
        if w.event_type == "Gewitter":
            level = "danger" if w.level >= 3 else "warn"
            return [ScenarioAlert(
                key="thunder", level=level,
                label_de=f"Gewitter (Stufe {w.level})",
                detail_de=(w.text[:90] if w.text else "Blitzschlag, Sturmböen, Starkregen möglich"),
                icon="⛈️",
            )]
    return []


def _ice_alerts(
    current: CurrentWeather | None,
    nowcast: NowcastResult | None,
) -> list[ScenarioAlert]:
    """Glatteis / gefrierender Regen – Niederschlag im Temperaturfenster um 0 °C."""
    temp = current.temperature_c if current else None
    if temp is None and nowcast and nowcast.steps:
        temps = [s.temperature_c for s in nowcast.steps if s.temperature_c is not None]
        temp = min(temps) if temps else None
    if temp is None or not (_ICE_TEMP_LOW <= temp <= _ICE_TEMP_HIGH):
        return []

    rain = nowcast.total_mm if nowcast else (
        current.precipitation_1h_mm if current and current.precipitation_1h_mm else 0.0
    )
    if rain is None or rain < _ICE_MIN_RAIN:
        return []

    return [ScenarioAlert(
        key="ice", level="warn",
        label_de="Glatteisgefahr",
        detail_de=f"{temp:.0f}°C bei Niederschlag – gefrierende Nässe möglich",
        icon="🧊",
    )]


def analyze_weather(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,
    nowcast: NowcastResult | None = None,
    warnings: list[WeatherWarning] | None = None,
) -> list[ScenarioAlert]:
    """Returns scenario alerts (storm, wildfire, rain, snow, thunder, ice).

    Danger-Alarme werden vor warn-Alarmen einsortiert.
    """
    alerts = (
        _storm_alerts(current, forecast)
        + _wildfire_alerts(current, forecast, nowcast)
        + _heavy_rain_alerts(nowcast, forecast)
        + _snow_alerts(current, nowcast, forecast)
        + _thunderstorm_alerts(warnings)
        + _ice_alerts(current, nowcast)
    )
    return sorted(alerts, key=lambda a: 0 if a.level == "danger" else 1)


# ── 7-Tage-Tagesvorhersage (Open-Meteo daily endpoint) ───────────────────────

_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


async def get_daily_forecast(lat: float, lng: float) -> DailyForecast | None:
    """7-Tage-Tagesvorhersage: Temp-Min/Max, Niederschlag, Windspitze (Open-Meteo daily).

    Gecacht fuer 1 Stunde (genug fuer ein Dashboard das alle 5 min neu laedt).
    """
    if not settings.WEATHER_ENABLED:
        return None

    key = _cache_key("daily7", lat, lng)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=settings.WEATHER_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                    "forecast_days": 7,
                    "timezone": "Europe/Vienna",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo 7-Tage-Forecast fehlgeschlagen: %s", exc)
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    wind_max = daily.get("wind_speed_10m_max", [])  # km/h

    days: list[DailyForecastDay] = []
    for i, dt_str in enumerate(dates[:7]):
        try:
            from datetime import date as _d
            dt = _d.fromisoformat(dt_str)
            label = f"{_WEEKDAYS_DE[dt.weekday()]} {dt.day:02d}.{dt.month:02d}."
        except (ValueError, AttributeError):
            label = dt_str
        days.append(DailyForecastDay(
            date_label=label,
            temp_max_c=temp_max[i] if i < len(temp_max) else None,
            temp_min_c=temp_min[i] if i < len(temp_min) else None,
            precip_mm=precip[i] if i < len(precip) else None,
            wind_max_ms=(wind_max[i] / 3.6) if (i < len(wind_max) and wind_max[i] is not None) else None,
        ))

    result = DailyForecast(days=days)
    _cache_set(key, result, 3600)
    return result


# ── Nowcast grid stub (PR 4) ──────────────────────────────────────────────────

async def get_nowcast_grid(
    bbox: tuple[float, float, float, float],
) -> dict | None:
    """INCA nowcast grid as GeoJSON for map overlay. Full implementation in PR 4."""
    return None
