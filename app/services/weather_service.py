"""Weather data service — GeoSphere Austria Data Hub (primary) + Open-Meteo (fallback).

Provides: Nowcast (15-min precipitation), current conditions, NWP forecasts (+6/+12/+24h).
Weather results use configured TTLs, optionally shared through Redis across workers.
Attribution (CC BY 4.0 required): use GEOSPHERE_ATTRIBUTION in any UI showing this data.
"""
import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import wraps
from inspect import signature
from typing import Any, Literal, cast
from weakref import WeakKeyDictionary

import httpx

from app.config import settings
from app.services import shared_cache, weather_health, weather_http, weather_openmeteo
from app.services.weather_thresholds import (
    BF8 as _BF8,
)
from app.services.weather_thresholds import (
    BF10 as _BF10,
)
from app.services.weather_thresholds import (
    ICE_MIN_RAIN as _ICE_MIN_RAIN,
)
from app.services.weather_thresholds import (
    ICE_TEMP_HIGH as _ICE_TEMP_HIGH,
)
from app.services.weather_thresholds import (
    ICE_TEMP_LOW as _ICE_TEMP_LOW,
)
from app.services.weather_thresholds import (
    RAIN_FC6_DANGER as _RAIN_FC6_DANGER,
)
from app.services.weather_thresholds import (
    RAIN_FC6_WARN as _RAIN_FC6_WARN,
)
from app.services.weather_thresholds import (
    RAIN_NOWCAST_DANGER as _RAIN_NOWCAST_DANGER,
)
from app.services.weather_thresholds import (
    RAIN_NOWCAST_WARN as _RAIN_NOWCAST_WARN,
)
from app.services.weather_thresholds import (
    SNOW_FC6_DANGER as _SNOW_FC6_DANGER,
)
from app.services.weather_thresholds import (
    SNOW_FC6_WARN as _SNOW_FC6_WARN,
)
from app.services.weather_thresholds import (
    SNOW_NOWCAST_DANGER as _SNOW_NOWCAST_DANGER,
)
from app.services.weather_thresholds import (
    SNOW_NOWCAST_WARN as _SNOW_NOWCAST_WARN,
)
from app.services.weather_thresholds import (
    SNOW_TEMP_MAX as _SNOW_TEMP_MAX,
)
from app.services.weather_thresholds import (
    WILDFIRE_HUM_DANGER as _WILDFIRE_HUM_DANGER,
)
from app.services.weather_thresholds import (
    WILDFIRE_HUM_WARN as _WILDFIRE_HUM_WARN,
)
from app.services.weather_thresholds import (
    WILDFIRE_NO_RAIN as _WILDFIRE_NO_RAIN,
)
from app.services.weather_thresholds import (
    WILDFIRE_TEMP as _WILDFIRE_TEMP,
)
from app.services.weather_timeseries import (
    HourlySeries,
    accumulated_to_interval,
    value_at,
    window_max,
    window_sum,
)

logger = logging.getLogger("einsatzleiter.weather")

GEOSPHERE_ATTRIBUTION = "GeoSphere Austria (CC BY 4.0)"

# ── In-process TTL cache ──────────────────────────────────────────────────────

_cache: dict[str, tuple[Any, float]] = {}


_flights: WeakKeyDictionary = WeakKeyDictionary()
_request: ContextVar[tuple[str, int | None]] = ContextVar("weather_request", default=("current", None))


def _single_flight[**P, T](fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Gleichzeitige Abfragen je Argumentkombination und Event-Loop bündeln."""
    sig = signature(fn)

    @wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        key = (fn.__name__, repr(bound.arguments))
        locks = _flights.setdefault(asyncio.get_running_loop(), {})
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            kind = (fn.__name__.removeprefix("get_").replace("daily_forecast", "daily")
                    .replace("warnings_result", "warnings"))
            context = _request.set((kind, bound.arguments.get("org_id")))
            try:
                if not settings.REDIS_URL or not settings.WEATHER_ENABLED:
                    return await fn(*args, **kwargs)
                # Konfigurationswechsel und Organisationen erhalten eigene Worker-Sperren.
                try:
                    async with shared_cache.lock(_flight_cache_key(kind, bound.arguments)):
                        async with asyncio.timeout(settings.WEATHER_LOCK_TTL_S * 0.9):
                            return await fn(*args, **kwargs)
                except TimeoutError:
                    weather_health.log_failure("geosphere", kind, "timeout")
                    if kind == "warnings":
                        return cast(T, WarningsResult("unavailable", [], error="timeout"))
                    return cast(T, None)
            finally:
                _request.reset(context)

    return wrapped


def _flight_cache_key(kind: str, arguments: Any) -> str:
    """Sperren teilen dieselbe Identität wie Ergebnisse, auch ohne Org-Key."""
    org_id = arguments.get("org_id")
    if kind == "warnings":
        resource = f"warn:{settings.GEOSPHERE_WARN_URL}:org{org_id}"
    else:
        order = _provider_order(kind, org_id)
        resource = {"current": "current", "forecast": "nwp", "nowcast": "nowcast", "daily": "daily7"}[kind]
        if kind in {"current", "forecast"} and "kachelmann" in order:
            resource += f"_k{org_id}"
        if kind == "forecast":
            resource += "_h" + "-".join(str(hour) for hour in arguments["horizons"])
        resource = _provider_resource(resource, kind, order)
    return _cache_key(resource, arguments["lat"], arguments["lng"])


def _cache_key(resource: str, lat: float, lng: float) -> str:
    return f"{resource}:{lat!r}:{lng!r}"


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expires_ts = entry
    if datetime.now(UTC).timestamp() > expires_ts:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int | None) -> None:
    _cache[key] = (value, datetime.now(UTC).timestamp() + ttl if ttl is not None else math.inf)


async def _shared_cache_get(key: str) -> Any | None:
    local = _cache_get(key)
    if local is not None or not settings.REDIS_URL:
        return local
    # Auch synchrone Leser erhalten den Wert; Rest-TTL keinesfalls neu starten.
    started = datetime.now(UTC).timestamp()
    entry = await shared_cache.get_entry(key)
    if entry is None:
        return None
    value, remaining = entry
    _cache[key] = (value, started + remaining)
    return value


async def _shared_cache_set(key: str, value: Any, ttl: int | None) -> None:
    _cache_set(key, value, ttl)
    if settings.REDIS_URL:
        await shared_cache.set(key, value, ttl if ttl is not None else settings.WEATHER_WARN_STALE_S)


def has_cached(lat: float, lng: float) -> bool:
    """Returns True if nowcast data is cached for this location (no fetch)."""
    return get_cached_nowcast(lat, lng) is not None


def get_cached_nowcast(lat: float, lng: float) -> NowcastResult | None:
    """Returns cached NowcastResult without triggering a fetch."""
    result = _cache_get(_cache_key(_provider_resource("nowcast", "nowcast", _provider_order("nowcast")), lat, lng))
    return _refresh_meta(result, "nowcast") if result is not None else None


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class DataMeta:
    provider: str
    model_or_station: str | None
    lat: float | None
    lng: float | None
    run_time: datetime | None
    valid_at: datetime | None
    fetched_at: datetime
    kind: Literal["measured", "forecast", "nowcast"]
    freshness: Literal["fresh", "aging", "stale"] = "fresh"
    unit: str | None = None  # Mehrere Messgrößen: Einheiten stehen an den Feldern.
    is_fallback: bool = False


def _refresh_meta(result: Any, kind: str) -> Any:
    if result.meta is not None:
        maximum = getattr(settings, f"WEATHER_MAX_AGE_{kind.upper()}_S")
        age = (datetime.now(UTC) - result.meta.fetched_at).total_seconds()
        result.meta.freshness = "stale" if age > maximum else ("aging" if age > maximum / 2 else "fresh")
    return result


def _set_meta(result: Any, kind: str, lat: float, lng: float, provider: str, order: list[str]) -> None:
    now = datetime.now(UTC)
    previous = result.meta
    result.meta = DataMeta(
        provider=provider,
        model_or_station=(settings.WEATHER_OPENMETEO_MODEL if provider == "open-meteo-geosphere"
                          else (settings.WEATHER_NOWCAST_RESOURCE if kind == "nowcast"
                                else settings.WEATHER_NWP_RESOURCE)
                          if provider == "geosphere" else "best_match" if provider == "open-meteo" else None),
        lat=lat, lng=lng, run_time=previous.run_time if previous else None,
        valid_at=((previous.valid_at if previous else None) or getattr(result, "reference_time", None) or
                  (result.steps[0].timestamp if kind == "nowcast" and result.steps else None)), fetched_at=now,
        kind="nowcast" if kind == "nowcast" else "forecast",
        is_fallback=bool(order and provider != order[0]),
    )
    _refresh_meta(result, kind)
    if result.meta.is_fallback:
        weather_health.log_failure(order[0], kind, "fallback", provider)


@dataclass
class NowcastStep:
    timestamp: datetime
    precipitation_mm: float | None
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None


@dataclass
class NowcastResult:
    steps: list[NowcastStep]
    peak_mm: float | None
    peak_at: datetime | None
    total_mm: float | None
    trend: str          # "increasing" | "decreasing" | "stable"
    source: str         # "geosphere" | "openmeteo" | "open-meteo-geosphere"
    meta: DataMeta | None = None


@dataclass
class CurrentWeather:
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    humidity_pct: float | None = None
    precipitation_1h_mm: float | None = None
    source: str = ""
    snowfall_cm: float | None = None
    weather_code: float | None = None
    precipitation_rate_mmh: float | None = None
    meta: DataMeta | None = None


@dataclass
class ForecastHorizon:
    hours: int
    precipitation_acc_mm: float | None = None   # accumulated from T+0
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None
    humidity_pct: float | None = None
    wind_direction_deg: float | None = None
    snowfall_cm: float | None = None
    weather_code: float | None = None
    precipitation_mm: float | None = None
    gust_max_ms: float | None = None
    gust_max_at: datetime | None = None
    complete: bool = True
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None


@dataclass
class ForecastResult:
    horizons: list[ForecastHorizon]
    source: str = "geosphere_nwp"
    reference_time: datetime | None = None
    meta: DataMeta | None = None
    precipitation_hourly: HourlySeries = field(default_factory=list)


@dataclass
class DailyForecastDay:
    date_label: str                    # z.B. "Mo 24.06."
    temp_max_c: float | None = None
    temp_min_c: float | None = None
    precip_mm: float | None = None
    wind_max_ms: float | None = None   # m/s
    weather_code: float | None = None
    gust_max_ms: float | None = None


@dataclass
class DailyForecast:
    days: list[DailyForecastDay]
    source: str = "openmeteo"
    meta: DataMeta | None = None


@dataclass
class WeatherWarning:
    level: int
    event_type: str
    text: str
    valid_from: datetime
    valid_to: datetime
    region: str = ""


WarningStatus = Literal["ok_no_warnings", "ok_with_warnings", "unavailable", "stale", "disabled"]


@dataclass
class WarningsResult:
    status: WarningStatus
    warnings: list[WeatherWarning]
    fetched_at: datetime | None = None
    last_success_at: datetime | None = None
    error: str | None = None


@dataclass
class ScenarioAlert:
    key: str        # "storm" | "wildfire" | "rain" | "snow" | "thunder" | "ice"
    level: str      # "warn" | "danger"
    label_de: str
    detail_de: str
    icon_name: str = ""


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
            resp = await weather_http.get(client, url, params=params)
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
        logger.warning("GeoSphere Fehler: %s", type(exc).__name__)
    return None


_UNKNOWN_PROVIDERS: set[str] = set()
_PROVIDER_DEFAULTS = {
    "nowcast": "geosphere,open-meteo",
    "current": "kachelmann,geosphere,open-meteo",
    "forecast": "kachelmann,geosphere,open-meteo",
    "daily": "open-meteo",
}


def _provider_order(kind: str, org_id: int | None = None) -> list[str]:
    """Konfigurierte Reihenfolge, gefiltert nach unterstützten und aktiven Quellen."""
    from app.services import kachelmann_service

    supported = {"open-meteo", "open-meteo-geosphere"}
    if kind != "daily":
        supported.add("geosphere")
    if kind in {"current", "forecast"}:
        supported.add("kachelmann")
    order: list[str] = []
    for name in getattr(settings, f"WEATHER_PRIORITY_{kind.upper()}").split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in {"kachelmann", "geosphere", "open-meteo", "open-meteo-geosphere"}:
            if name not in _UNKNOWN_PROVIDERS:
                logger.warning("Unbekannter Wetterprovider wird ignoriert: %s", name)
                _UNKNOWN_PROVIDERS.add(name)
            continue
        if name not in supported or name in order:
            continue
        if name == "kachelmann" and not kachelmann_service.is_configured(org_id):
            continue
        if name == "open-meteo-geosphere" and not settings.WEATHER_OPENMETEO_GEOSPHERE_ENABLED:
            continue
        if name == "open-meteo" and not settings.WEATHER_FALLBACK_OPENMETEO:
            continue
        order.append(name)
    return order


def _provider_resource(resource: str, kind: str, order: list[str]) -> str:
    """Bestehende Präfixe behalten; abweichende Konfiguration im Cache trennen."""
    configured = getattr(settings, f"WEATHER_PRIORITY_{kind.upper()}")
    if (
        configured == _PROVIDER_DEFAULTS[kind]
        and settings.WEATHER_FALLBACK_OPENMETEO
        and settings.OPENMETEO_BASE_URL == "https://api.open-meteo.com/v1/forecast"
    ):
        return resource
    suffix = ",".join(order)
    if any(name.startswith("open-meteo") for name in order):
        suffix += f":{settings.OPENMETEO_BASE_URL}"
    if "open-meteo-geosphere" in order:
        suffix += f":{settings.WEATHER_OPENMETEO_MODEL}"
    return f"{resource}:{suffix}"


async def _provider_fetch(provider: str, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    """Auch fehlerhafte Provider-Antworten dürfen den nächsten Fallback nicht verhindern."""
    kind, org_id = _request.get()
    started = time.monotonic()
    reason = "unavailable"
    try:
        result = await fn(*args, **kwargs)
    except Exception as exc:
        result = None
        reason = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "provider_error"
    await weather_health.record(provider, result is not None, (time.monotonic() - started) * 1000, reason)
    if result is None:
        order = _provider_order(kind, org_id)
        index = order.index(provider) if provider in order else len(order)
        fallback = order[index + 1] if index + 1 < len(order) else None
        weather_health.log_failure(provider, kind, reason, fallback)
    return result


# ── Nowcast ────────────────────────────────────────────────────────────────────

@_single_flight
async def get_nowcast(lat: float, lng: float) -> NowcastResult | None:
    """15-min precipitation nowcast for the next ~3 h.

    Primary: GeoSphere nowcast-v1-15min-1km.
    Fallback: Open-Meteo minutely_15 (if WEATHER_FALLBACK_OPENMETEO and GeoSphere fails).
    Returns None if both sources unavailable.
    """
    if not settings.WEATHER_ENABLED:
        return None

    key = _cache_key(_provider_resource("nowcast", "nowcast", _provider_order("nowcast")), lat, lng)
    cached = await _shared_cache_get(key)
    if cached is not None:
        return _refresh_meta(cached, "nowcast")

    result = None
    order = _provider_order("nowcast")
    for provider in order:
        if provider == "geosphere":
            result = await _provider_fetch(provider, _fetch_geosphere_nowcast, lat, lng)
        elif provider == "open-meteo-geosphere":
            result = await _provider_fetch(
                provider, _openmeteo_nowcast, lat, lng, model=settings.WEATHER_OPENMETEO_MODEL,
            )
        else:
            result = await _provider_fetch(provider, _openmeteo_nowcast, lat, lng)
        if result is not None:
            break

    if result is not None:
        _set_meta(result, "nowcast", lat, lng, provider, order)
        await _shared_cache_set(key, result, settings.WEATHER_CACHE_TTL_NOWCAST)
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
            precipitation_mm=_safe_float(rr[i] if i < len(rr) else None),
            temperature_c=_safe_float(t2m[i] if i < len(t2m) else None),
            wind_speed_ms=_safe_float(ff[i] if i < len(ff) else None),
            gust_speed_ms=_safe_float(fx[i] if i < len(fx) else None),
        ))

    if not steps:
        return None

    prec = [s.precipitation_mm for s in steps if s.precipitation_mm is not None]
    complete = len(prec) == len(steps)
    peak_mm = max(prec, default=0.0)
    peak_idx = (next((i for i, step in enumerate(steps) if step.precipitation_mm == peak_mm), None)
                if peak_mm > 0 else None)
    return NowcastResult(
        steps=steps,
        peak_mm=peak_mm if complete else None,
        peak_at=steps[peak_idx].timestamp if complete and peak_idx is not None else None,
        total_mm=sum(prec) if complete else None,
        trend=_compute_trend(prec),
        source="geosphere",
    )


async def _openmeteo_nowcast(lat: float, lng: float, model: str | None = None) -> NowcastResult | None:
    """Open-Meteo minutely_15 fallback for nowcast."""
    data = await weather_openmeteo.fetch_minutely15(lat, lng, model=model)
    if data is None:
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
        precipitation = _safe_float(prec_data[i] if i < len(prec_data) else None)
        steps.append(NowcastStep(
            timestamp=ts,
            precipitation_mm=precipitation,
            temperature_c=_safe_float(t_data[i] if i < len(t_data) else None),
            wind_speed_ms=ws_kmh / 3.6 if ws_kmh is not None else None,
            gust_speed_ms=wg_kmh / 3.6 if wg_kmh is not None else None,
        ))

    if not steps:
        return None

    prec = [s.precipitation_mm for s in steps if s.precipitation_mm is not None]
    peak_mm = max(prec, default=0.0)
    peak_idx = (
        next((i for i, step in enumerate(steps) if step.precipitation_mm == peak_mm), None)
        if peak_mm > 0 else None
    )
    complete = len(prec) == len(steps)
    return NowcastResult(
        steps=steps,
        peak_mm=peak_mm if complete else None,
        peak_at=steps[peak_idx].timestamp if complete and peak_idx is not None else None,
        total_mm=sum(prec) if complete else None,
        trend=_compute_trend(prec),
        source="open-meteo-geosphere" if model else "openmeteo",
    )


# ── Current conditions ────────────────────────────────────────────────────────

@_single_flight
async def get_current(lat: float, lng: float, org_id: int | None = None) -> CurrentWeather | None:
    """Current weather conditions (temperature, wind, gust, humidity).

    Primary: Kachelmann (wenn org-spezifischer API-Key gesetzt), sonst GeoSphere NWP t=0.
    Fallback: GeoSphere NWP → Open-Meteo current.
    """
    if not settings.WEATHER_ENABLED:
        return None

    from app.services import kachelmann_service
    order = _provider_order("current", org_id)
    use_kachelmann = "kachelmann" in order
    resource = f"current_k{org_id}" if use_kachelmann else "current"
    key = _cache_key(_provider_resource(resource, "current", order), lat, lng)
    cached = await _shared_cache_get(key)
    if cached is not None:
        return _refresh_meta(cached, "current")

    result: CurrentWeather | None = None
    for provider in order:
        if provider == "kachelmann":
            result = await _provider_fetch(provider, kachelmann_service.fetch_current, lat, lng, org_id=org_id)
        elif provider == "geosphere":
            result = await _provider_fetch(provider, _fetch_current_from_nwp, lat, lng)
        elif provider == "open-meteo-geosphere":
            result = await _provider_fetch(
                provider, _openmeteo_current, lat, lng, model=settings.WEATHER_OPENMETEO_MODEL,
            )
        else:
            result = await _provider_fetch(provider, _openmeteo_current, lat, lng)
        if result is not None:
            break

    if result is not None:
        _set_meta(result, "current", lat, lng, provider, order)
        await _shared_cache_set(key, result, settings.WEATHER_CACHE_TTL_CURRENT)
    return result


async def _openmeteo_current(lat: float, lng: float, model: str | None = None) -> CurrentWeather | None:
    """Open-Meteo fallback for current conditions."""
    data = await weather_openmeteo.fetch_current(lat, lng, model=model)
    if data is None:
        return None

    c = data.get("current", {})
    ws_kmh = _safe_float(c.get("wind_speed_10m"))
    wg_kmh = _safe_float(c.get("wind_gusts_10m"))
    result = CurrentWeather(
        temperature_c=_safe_float(c.get("temperature_2m")),
        humidity_pct=_safe_float(c.get("relative_humidity_2m")),
        wind_speed_ms=ws_kmh / 3.6 if ws_kmh is not None else None,
        gust_speed_ms=wg_kmh / 3.6 if wg_kmh is not None else None,
        wind_direction_deg=_safe_float(c.get("wind_direction_10m")),
        precipitation_1h_mm=_safe_float(c.get("precipitation")),
        snowfall_cm=_safe_float(c.get("snowfall")),
        weather_code=_safe_float(c.get("weather_code")),
        source="open-meteo-geosphere" if model else "openmeteo",
    )
    _set_meta(result, "current", lat, lng, "open-meteo-geosphere" if model else "open-meteo", [])
    raw_time = c.get("time")
    times = _parse_timestamps([raw_time]) if isinstance(raw_time, str) else []
    if result.meta is not None:
        result.meta.valid_at = times[0] if times else None
    return result


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

    result = CurrentWeather(
        temperature_c=_first(_extract_param_data(feature, "t2m")),
        wind_speed_ms=_wind_speed_from_uv(u0, v0),
        gust_speed_ms=_wind_speed_from_uv(ug0, vg0),
        wind_direction_deg=_wind_dir_from_uv(u0, v0),
        humidity_pct=_first(_extract_param_data(feature, "rh2m")),
        source="geosphere_nwp",
    )
    _set_meta(result, "current", lat, lng, "geosphere", [])
    raw_time = next(iter(data.get("timestamps", [])), None)
    times = _parse_timestamps([raw_time]) if isinstance(raw_time, str) else []
    if result.meta is not None:
        result.meta.valid_at = times[0] if times else None
    return result


# ── NWP Forecast ──────────────────────────────────────────────────────────────

@_single_flight
async def get_forecast(
    lat: float,
    lng: float,
    horizons: tuple[int, ...] = (1, 3, 6, 12, 24),
    org_id: int | None = None,
) -> ForecastResult | None:
    """NWP multi-horizon forecast: accumulated precipitation, wind, temperature.

    Primary: Kachelmann (wenn org-spezifischer Key gesetzt), sonst GeoSphere NWP.
    Fallback: Open-Meteo hourly.
    horizons: list of hours from now (default +1, +3, +6, +12, +24).
    """
    if not settings.WEATHER_ENABLED:
        return None

    from app.services import kachelmann_service
    order = _provider_order("forecast", org_id)
    use_kachelmann = "kachelmann" in order
    horizon_key = "-".join(str(hour) for hour in horizons)
    resource = f"nwp_k{org_id}" if use_kachelmann else "nwp"
    key = _cache_key(_provider_resource(f"{resource}_h{horizon_key}", "forecast", order), lat, lng)
    cached = await _shared_cache_get(key)
    if cached is not None:
        return _refresh_meta(cached, "forecast")

    result: ForecastResult | None = None
    for provider in order:
        if provider == "kachelmann":
            result = await _provider_fetch(
                provider, kachelmann_service.fetch_forecast, lat, lng, horizons, org_id=org_id,
            )
        elif provider == "geosphere":
            result = await _provider_fetch(provider, _fetch_geosphere_nwp, lat, lng, horizons)
        elif provider == "open-meteo-geosphere":
            result = await _provider_fetch(
                provider, _openmeteo_forecast, lat, lng, horizons, model=settings.WEATHER_OPENMETEO_MODEL,
            )
        else:
            result = await _provider_fetch(provider, _openmeteo_forecast, lat, lng, horizons)
        if result is not None:
            break

    if result is not None:
        _set_meta(result, "forecast", lat, lng, provider, order)
        await _shared_cache_set(key, result, settings.WEATHER_CACHE_TTL_NWP)
    return result


def _hourly_series(times: list, values: list, factor: float = 1) -> HourlySeries:
    """Parst jeden Zeitstempel einzeln, damit ungültige Zeiten keine Werte verschieben."""
    series: HourlySeries = []
    for i, raw in enumerate(times):
        parsed = _parse_timestamps([raw])
        if parsed:
            value = _safe_float(values[i]) if i < len(values) else None
            series.append((parsed[0].astimezone(UTC), value * factor if value is not None else None))
    return series


def _series_horizons(
    series: dict[str, HourlySeries], ref: datetime, horizons: tuple[int, ...],
) -> list[ForecastHorizon]:
    """Gemeinsame Fenster; optionale, nicht gelieferte Variablen bleiben None."""
    result = []
    for hours in horizons:
        at = ref + timedelta(hours=hours)
        rain, rain_ok = window_sum(series.get("precipitation", []), ref, hours)
        gust, gust_at, gust_ok = window_max(series.get("gust", []), ref, hours)
        temperatures = series.get("temperature", [])
        high, _, temp_ok = window_max(temperatures, ref, hours)
        low_neg, _, _ = window_max([(t, -v if v is not None else None) for t, v in temperatures], ref, hours)
        _, _, wind_ok = window_max(series.get("wind", []), ref, hours)
        snow_series = series.get("snowfall", [])
        snow, snow_ok = window_sum(snow_series, ref, hours)
        complete = rain_ok and gust_ok and temp_ok and wind_ok
        if any(v is not None for _, v in snow_series):
            complete = complete and snow_ok
        result.append(ForecastHorizon(
            hours=hours, precipitation_acc_mm=rain, precipitation_mm=rain,
            temperature_c=value_at(temperatures, at),
            wind_speed_ms=value_at(series.get("wind", []), at),
            gust_speed_ms=value_at(series.get("gust", []), at),
            humidity_pct=value_at(series.get("humidity", []), at),
            wind_direction_deg=value_at(series.get("direction", []), at),
            weather_code=value_at(series.get("code", []), at),
            snowfall_cm=snow if any(v is not None for _, v in snow_series) else None,
            gust_max_ms=gust, gust_max_at=gust_at, complete=complete,
            temperature_min_c=-low_neg if low_neg is not None else None,
            temperature_max_c=high,
        ))
    return result


async def _openmeteo_forecast(
    lat: float, lng: float, horizons: tuple[int, ...], model: str | None = None
) -> ForecastResult | None:
    """Open-Meteo hourly fallback for multi-horizon forecast."""
    data = await weather_openmeteo.fetch_hourly(lat, lng, max(horizons) + 2, model=model)
    if data is None:
        return None

    ref = datetime.now(UTC)
    hourly = data.get("hourly", {})
    series = {
        target: _hourly_series(hourly.get("time", []), hourly.get(name, []), factor)
        for target, name, factor in (
            ("temperature", "temperature_2m", 1), ("wind", "wind_speed_10m", 1 / 3.6),
            ("gust", "wind_gusts_10m", 1 / 3.6), ("precipitation", "precipitation", 1),
            ("humidity", "relative_humidity_2m", 1), ("direction", "wind_direction_10m", 1),
            ("snowfall", "snowfall", 1), ("code", "weather_code", 1),
        )
    }
    return ForecastResult(
        horizons=_series_horizons(series, ref, horizons),
        source="open-meteo-geosphere" if model else "openmeteo", reference_time=ref,
        precipitation_hourly=series["precipitation"],
    )


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
    ref = datetime.now(UTC)
    times = data.get("timestamps", [])

    def parameter(name: str) -> HourlySeries:
        return _hourly_series(times, _extract_param_data(feature, name))

    def vector(u: str, v: str) -> HourlySeries:
        return [(t, _wind_speed_from_uv(a, b))
                for (t, a), (_, b) in zip(parameter(u), parameter(v), strict=True)]

    series = {
        "precipitation": accumulated_to_interval(parameter("rr_acc")),
        "temperature": parameter("t2m"),
        "wind": vector("u10m", "v10m"), "gust": vector("ugust", "vgust"),
    }
    return ForecastResult(
        horizons=_series_horizons(series, ref, horizons), source="geosphere_nwp",
        reference_time=ref, precipitation_hourly=series["precipitation"],
    )


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
    _VIENNA_TZ = UTC  # type: ignore[assignment]


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


def _warnings_enabled(org_id: int | None) -> bool:
    """Org-Flag auch bei direkten Service-Aufrufen berücksichtigen."""
    if not settings.WEATHER_ENABLED:
        return False
    if org_id is None:
        return True
    from app.db import SessionLocal
    from app.models.master import OrgSettings

    with SessionLocal() as db:
        row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        return row is None or row.weather_enabled is not False


def _warning_failure(cached: WarningsResult, good: WarningsResult | None) -> WarningsResult:
    now = datetime.now(UTC)
    if good and good.last_success_at and (now - good.last_success_at).total_seconds() <= settings.WEATHER_WARN_STALE_S:
        return WarningsResult("stale", good.warnings, cached.fetched_at, good.last_success_at, cached.error)
    return WarningsResult("unavailable", [], cached.fetched_at,
                          good.last_success_at if good else None, cached.error)


@_single_flight
async def get_warnings_result(lat: float, lng: float, org_id: int | None = None) -> WarningsResult:
    """Erfolg, Ausfall und Last-Known-Good getrennt halten; Ausfälle nur kurz cachen."""
    if not await asyncio.to_thread(_warnings_enabled, org_id):
        return WarningsResult("disabled", [])
    key = _cache_key(f"warn:{settings.GEOSPHERE_WARN_URL}:org{org_id}", lat, lng)
    good = await _shared_cache_get(key + ":good")
    cached = await _shared_cache_get(key)
    if cached is not None:
        if cached.error is not None:
            return _warning_failure(cached, good)
        return cached
    now = datetime.now(UTC)
    started = time.monotonic()
    try:
        warnings = await _fetch_geosphere_warnings(lat, lng)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        weather_health.log_failure("geosphere", "warnings", "unavailable")
        await weather_health.record("geosphere", False, (time.monotonic() - started) * 1000)
        error = type(exc).__name__ if settings.REDIS_URL else (str(exc) or type(exc).__name__)
        failure = WarningsResult("unavailable", [], now, error=error)
        await _shared_cache_set(key, failure, settings.WEATHER_WARN_ERROR_TTL_S)
        return _warning_failure(failure, good)
    await weather_health.record("geosphere", True, (time.monotonic() - started) * 1000)
    result = WarningsResult("ok_with_warnings" if warnings else "ok_no_warnings", warnings, now, now)
    await _shared_cache_set(key, result, settings.WEATHER_CACHE_TTL_WARN)
    # Letzten Erfolg bis zum Cache-Reset behalten; die Nutzungsgrenze prüft _warning_failure.
    await _shared_cache_set(key + ":good", result, None)
    return result


async def get_warnings(lat: float, lng: float, org_id: int | None = None) -> list[WeatherWarning]:
    """Kompatible Listen-Schnittstelle; Statusbewertung über get_warnings_result."""
    return (await get_warnings_result(lat, lng, org_id)).warnings


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
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GeoSphere Warn-API Fehler: %s", type(exc).__name__)
        raise

    if not isinstance(data, dict) or not isinstance(data.get("properties"), dict):
        raise ValueError("Ungültige Warnantwort: properties fehlt")
    props = data["properties"]
    region = props.get("location", {}).get("properties", {}).get("name", "")
    raw_list = props.get("warnings")
    if not isinstance(raw_list, list):
        raise ValueError("Ungültige Warnantwort: warnings fehlt")

    now = datetime.now(UTC)
    warnings: list[WeatherWarning] = []
    for raw_item in raw_list:
        item: dict = {}
        try:
            # ZAMG-Warn-API: Felder liegen verschachtelt unter "properties"
            item = raw_item.get("properties", raw_item) if isinstance(raw_item, dict) else {}
            rawinfo = item.get("rawinfo") if isinstance(item.get("rawinfo"), dict) else {}

            level = int(item.get("warnstufeid") or rawinfo.get("wlevel") or 1)  # type: ignore[union-attr]
            type_id = int(item.get("warntypid") or rawinfo.get("wtype") or 0)  # type: ignore[union-attr]
            event = _WARN_TYPE_EVENT.get(type_id, "UNKNOWN")
            text = item.get("text") or item.get("meteotext") or _WARN_EVENT_DE.get(event, event)

            # Zeiten: bevorzugt Unix-Epoch aus rawinfo (UTC, eindeutig), sonst
            # deutsches Format "DD.MM.YYYY HH:MM" (Europe/Vienna) bzw. ISO.
            valid_from = _parse_warn_time(rawinfo.get("start"), item.get("begin"))  # type: ignore[union-attr]
            valid_to = _parse_warn_time(rawinfo.get("end"), item.get("end"))  # type: ignore[union-attr]
            if valid_from is None or valid_to is None:
                raise ValueError("Warnungszeitraum fehlt")
            if valid_to < now:
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
            raise ValueError("Ungültiger Warneintrag") from exc

    return sorted(warnings, key=lambda w: -w.level)


# ── Scenario analysis (Storm / Wildfire) ─────────────────────────────────────



def _storm_alerts(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,
) -> list[ScenarioAlert]:
    alerts: list[ScenarioAlert] = []
    active_gust = None
    if current:
        active_gust = current.gust_speed_ms if current.gust_speed_ms is not None else current.wind_speed_ms

    if active_gust is not None:
        if active_gust >= _BF10:
            alerts.append(ScenarioAlert(
                key="storm",
                level="danger",
                label_de="Schwerer Sturm",
                detail_de=f"Böe {active_gust:.0f} m/s (BF10+) – erhöhte Einsatzgefährdung",
                icon_name="bolt",
            ))
        elif active_gust >= _BF8:
            alerts.append(ScenarioAlert(
                key="storm",
                level="warn",
                label_de="Sturmwarnung",
                detail_de=f"Böe {active_gust:.0f} m/s (BF8+) – Windgefahr beachten",
                icon_name="bolt",
            ))

    if not alerts and forecast:
        for h in forecast.horizons:
            g = h.gust_max_ms if h.gust_max_ms is not None else (
                h.gust_speed_ms if h.gust_speed_ms is not None else h.wind_speed_ms
            )
            if g is not None and g >= _BF8:
                alerts.append(ScenarioAlert(
                    key="storm",
                    level="warn",
                    label_de=f"Sturm in {h.hours}h",
                    detail_de=f"Erwartete Böe {g:.0f} m/s (BF8+) in +{h.hours}h",
                    icon_name="bolt",
                ))
                break

    return alerts


def _wildfire_alerts(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,  # noqa: ARG001  (reserved for future use)
    nowcast: NowcastResult | None,
) -> list[ScenarioAlert]:
    if not current or current.temperature_c is None or current.humidity_pct is None or not nowcast:
        return []

    temp = current.temperature_c
    hum = current.humidity_pct
    no_rain = nowcast is not None and nowcast.total_mm is not None and nowcast.total_mm < _WILDFIRE_NO_RAIN

    if temp >= _WILDFIRE_TEMP and hum <= _WILDFIRE_HUM_DANGER:
        return [ScenarioAlert(
            key="wildfire",
            level="danger",
            label_de="Hohe Waldbrandgefahr",
            detail_de=f"{temp:.0f}°C, {hum:.0f}% RF – Brandbedingungen kritisch",
            icon_name="flame",
        )]
    if temp >= _WILDFIRE_TEMP and hum <= _WILDFIRE_HUM_WARN and no_rain:
        return [ScenarioAlert(
            key="wildfire",
            level="warn",
            label_de="Erhöhte Waldbrandgefahr",
            detail_de=f"{temp:.0f}°C, {hum:.0f}% RF – trockene Bedingungen",
            icon_name="flame",
        )]
    return []


def _fc_acc_at(forecast: ForecastResult | None, hours: int) -> float | None:
    """Akkumulierter Niederschlag bis zum Horizont <= hours (mm)."""
    if not forecast:
        return None
    best = None
    for h in forecast.horizons:
        if h.hours <= hours:
            value = h.precipitation_mm if h.precipitation_mm is not None else h.precipitation_acc_mm
            if value is not None:
                best = value
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
            icon_name="ripple",
        )]
    return [ScenarioAlert(
        key="rain", level="warn",
        label_de="Ergiebiger Regen",
        detail_de=f"{detail} – Niederschlag beobachten",
        icon_name="ripple",
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
            icon_name="snowflake",
        )]
    return [ScenarioAlert(
        key="snow", level="warn",
        label_de="Schneefall",
        detail_de=f"{temp:.0f}°C, {detail}",
        icon_name="snowflake",
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
                icon_name="cloud-storm",
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
        icon_name="snowflake",
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
    incomplete = any(value is None for value in (current, forecast, nowcast))
    if current and any(value is None for value in (
        current.temperature_c, current.humidity_pct, current.wind_speed_ms, current.gust_speed_ms,
    )):
        incomplete = True
    if forecast and (not forecast.horizons or any(
        (h.precipitation_mm is None and h.precipitation_acc_mm is None)
        or (h.gust_max_ms is None and h.gust_speed_ms is None) for h in forecast.horizons
    )):
        incomplete = True
    if current and current.meta and _refresh_meta(current, "current").meta.freshness == "stale":
        current = None
        incomplete = True
    if nowcast and (nowcast.total_mm is None or
                    (nowcast.meta and _refresh_meta(nowcast, "nowcast").meta.freshness == "stale")):
        nowcast = None
        incomplete = True
    if forecast:
        if forecast.meta and _refresh_meta(forecast, "forecast").meta.freshness == "stale":
            forecast = None
            incomplete = True
        elif any(not h.complete for h in forecast.horizons):
            incomplete = True
            forecast = ForecastResult([h for h in forecast.horizons if h.complete], forecast.source,
                                      forecast.reference_time, forecast.meta)
    alerts = (
        _storm_alerts(current, forecast)
        + _wildfire_alerts(current, forecast, nowcast)
        + _heavy_rain_alerts(nowcast, forecast)
        + _snow_alerts(current, nowcast, forecast)
        + _thunderstorm_alerts(warnings)
        + _ice_alerts(current, nowcast)
    )
    if incomplete:
        alerts.append(ScenarioAlert("data_quality", "warn", "Daten unvollständig",
                                    "Fehlende oder veraltete Daten werden nicht bewertet.", "alert-triangle"))
    return sorted(alerts, key=lambda a: 0 if a.level == "danger" else 1)


# ── 7-Tage-Tagesvorhersage (Open-Meteo daily endpoint) ───────────────────────

_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


@_single_flight
async def get_daily_forecast(lat: float, lng: float, org_id: int | None = None) -> DailyForecast | None:
    """7-Tage-Tagesvorhersage über die konfigurierte Provider-Kette und Cache-TTL."""
    if not settings.WEATHER_ENABLED:
        return None

    order = _provider_order("daily", org_id)
    # Daily verwendet keine Org-Schlüssel: identische Providerdaten teilen den Cache.
    key = _cache_key(_provider_resource("daily7", "daily", order), lat, lng)
    cached = await _shared_cache_get(key)
    if cached is not None:
        return _refresh_meta(cached, "daily")
    for provider in order:
        model = settings.WEATHER_OPENMETEO_MODEL if provider == "open-meteo-geosphere" else None
        result = await _provider_fetch(provider, _openmeteo_daily, lat, lng, model=model)
        if result is not None:
            _set_meta(result, "daily", lat, lng, provider, order)
            await _shared_cache_set(key, result, settings.WEATHER_DAILY_TTL)
            return result
    return None


async def _openmeteo_daily(lat: float, lng: float, model: str | None = None) -> DailyForecast | None:
    data = await weather_openmeteo.fetch_daily(lat, lng, model=model)
    if data is None:
        return None

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temp_max = daily.get("temperature_2m_max", [])
    temp_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    codes = daily.get("weather_code", [])
    gusts = daily.get("wind_gusts_10m_max", [])
    wind_max = daily.get("wind_gusts_10m_max" if model else "wind_speed_10m_max", [])  # km/h

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
            weather_code=_safe_float(codes[i]) if i < len(codes) else None,
            temp_max_c=temp_max[i] if i < len(temp_max) else None,
            temp_min_c=temp_min[i] if i < len(temp_min) else None,
            precip_mm=precip[i] if i < len(precip) else None,
            gust_max_ms=(gusts[i] / 3.6) if i < len(gusts) and gusts[i] is not None else None,
            wind_max_ms=(wind_max[i] / 3.6) if (i < len(wind_max) and wind_max[i] is not None) else None,
        ))

    return DailyForecast(days=days, source="open-meteo-geosphere" if model else "openmeteo")


# ── Nowcast grid stub (PR 4) ──────────────────────────────────────────────────

async def get_nowcast_grid(
    bbox: tuple[float, float, float, float],
) -> dict | None:
    """INCA nowcast grid as GeoJSON for map overlay. Full implementation in PR 4."""
    return None
