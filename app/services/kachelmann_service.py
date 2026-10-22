"""Kachelmann Wetter (Plus-API) client — premium primary weather source.

Aktiv nur wenn ein API-Key in OrgSettings.kachelmann_api_key gesetzt ist.
Liefert Ist-Werte und Vorhersage in denselben Dataclasses wie `weather_service`
(CurrentWeather, ForecastResult), damit Router und Templates unverändert bleiben.
Quelle wird mit `source="kachelmann"` markiert.

Robustheit: Alle HTTP-/Parsing-Fehler werden geloggt und als None zurückgegeben —
weather_service fällt dann still auf ZAMG/GeoSphere zurück. Niemals Exception nach
außen.

Hinweis: Die exakten Feldnamen/Einheiten der Kachelmann-Antwort sind erst mit
gültigem Key endgültig verifizierbar. Das Parsing arbeitet daher mit Feld-Aliasen
und einer Wind-Einheiten-Heuristik (siehe `_norm_wind_ms`). Bei Abweichung nur die
Alias-Listen bzw. die Heuristik anpassen.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.services import weather_http
from app.services.weather_service import (
    CurrentWeather,
    ForecastResult,
    _hourly_series,
    _safe_float,
    _series_horizons,
)

logger = logging.getLogger("einsatzleiter.weather")

_SOURCE = "kachelmann"

# ── API-Key-Lookup (OrgSettings), per Org gecacht ─────────────────────────────

_KEY_CACHE_TTL = 60.0   # s
_key_cache: dict[int | None, tuple[str | None, float]] = {}


def _get_api_key(org_id: int | None) -> str | None:
    """Liest `kachelmann_api_key` aus OrgSettings der jeweiligen Org. 60 s gecacht."""
    now = time.monotonic()
    cached = _key_cache.get(org_id)
    if cached is not None and now - cached[1] < _KEY_CACHE_TTL:
        return cached[0]

    value: str | None = None
    if org_id is not None:
        try:
            from app.db import SessionLocal
            from app.models.master import OrgSettings as _OS
            db = SessionLocal()
            try:
                row = db.query(_OS).filter(_OS.org_id == org_id).first()
                raw = (row.kachelmann_api_key or "").strip() if row else ""
                value = raw or None
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Kachelmann-Key konnte nicht aus OrgSettings gelesen werden (org=%s): %s",
                           org_id, type(exc).__name__)

    if value is None:
        logger.debug("Kein Kachelmann-API-Key fuer Org %s – Wetterdaten kommen von ZAMG/GeoSphere.", org_id)
    else:
        logger.debug("Kachelmann-Key aus OrgSettings geladen (org=%s).", org_id)

    _key_cache[org_id] = (value, now)
    return value


def reset_key_cache(org_id: int | None = None) -> None:
    """Erzwingt erneutes Einlesen des Keys (z.B. nach Speichern in den Settings).

    Ohne org_id: gesamten Cache leeren.
    """
    if org_id is None:
        _key_cache.clear()
    else:
        _key_cache.pop(org_id, None)


def is_configured(org_id: int | None) -> bool:
    """True wenn ein Kachelmann-API-Key fuer die Org vorhanden ist."""
    return _get_api_key(org_id) is not None


# ── HTTP-Client ───────────────────────────────────────────────────────────────

async def _get(path: str, org_id: int | None, params: dict | None = None) -> dict | None:
    """Single GET gegen die Kachelmann-API. Gibt JSON-dict oder None zurück."""
    api_key = _get_api_key(org_id)
    if not api_key:
        return None
    url = f"{settings.KACHELMANN_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(
            headers={
                "X-API-Key": api_key,
                "User-Agent": "Einsatzcockpit/2.x (weather)",
                "Accept": "application/json",
            },
            timeout=settings.WEATHER_HTTP_TIMEOUT,
        ) as client:
            resp = await weather_http.get(client, url, params=dict(params or {}, units="metric"))
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
    except httpx.TimeoutException:
        logger.warning("Kachelmann Timeout: %s", path.split("?", 1)[0])
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            logger.warning(
                "Kachelmann HTTP %s bei %s – API-Key ungültig oder Abo deckt den Endpunkt "
                "nicht ab. Es wird auf ZAMG/GeoSphere zurückgegriffen.", status, path.split("?", 1)[0],
            )
        elif status == 429:
            logger.warning("Kachelmann HTTP 429 bei %s – Rate-Limit erreicht.", path.split("?", 1)[0])
        else:
            logger.warning("Kachelmann HTTP %s: %s", status, path.split("?", 1)[0])
    except Exception as exc:
        logger.warning("Kachelmann Fehler: %s", type(exc).__name__)
    return None


# ── Parsing-Helfer ────────────────────────────────────────────────────────────

def _pick(d: dict, *keys: str) -> Any:
    """Erstes vorhandenes (nicht-None) Feld aus einer Alias-Liste (Rohwert)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _num(obj: Any) -> float | None:
    """Float aus einem Rohwert ODER einem verpackten {"value": x}-Objekt.

    /current liefert je Feld ein Objekt {"value": 21.9, "dateTime": …, "station": …};
    /forecast liefert flache Zahlen. Beides wird hier abgedeckt.
    """
    if isinstance(obj, dict):
        obj = obj.get("value")
    return _safe_float(obj)


def _pick_num(d: dict, *keys: str) -> float | None:
    """Erster Alias, der einen verwertbaren Zahlenwert liefert (verpackt oder flach)."""
    for k in keys:
        if k in d and d[k] is not None:
            v = _num(d[k])
            if v is not None:
                return v
    return None


def _norm_wind_ms(val: float | None) -> float | None:
    """Normalisiert Windgeschwindigkeit auf m/s.

    Heuristik: Werte > 40 werden als km/h interpretiert (40 m/s ≈ BF13, in
    Mitteleuropa praktisch nie erreicht) und nach m/s umgerechnet. Bei `units=metric`
    liefert Kachelmann i.d.R. bereits m/s — dann bleibt der Wert unverändert.
    """
    if val is None:
        return None
    return val / 3.6 if val > 40.0 else val


# ── Ist-Werte ─────────────────────────────────────────────────────────────────

async def fetch_current(lat: float, lng: float, org_id: int | None = None) -> CurrentWeather | None:
    """Aktuelle Bedingungen von Kachelmann (`/current/{lat}/{lon}`)."""
    data = await _get(f"/current/{lat}/{lng}", org_id)
    if not data:
        return None
    return _parse_current(data)


def _parse_current(data: dict) -> CurrentWeather | None:
    # Nutzdaten können verschachtelt sein (z.B. {"data": {...}})
    d = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(d, dict):
        return None

    # Feldnamen gemäß Kachelmann v02 /current (data-Objekt, je Feld {"value": …}):
    # temp, humidityRelative, windSpeed, windGust, windDirection, prec1h. Aliase als Fallback.
    temp = _pick_num(d, "temp", "temperature", "airTemperature", "t2m")
    hum = _pick_num(d, "humidityRelative", "relativeHumidity", "humidity", "rh", "rh2m")
    wind = _norm_wind_ms(_pick_num(d, "windSpeed", "windspeed", "wind", "ff"))
    gust = _norm_wind_ms(_pick_num(d, "windGust", "windGust3h", "gust", "fx"))
    wdir = _pick_num(d, "windDirection", "windDir", "dd")
    prec = _pick_num(d, "prec1h", "prec", "precCurrent", "precipitation", "precip", "rr")

    if temp is None and wind is None and prec is None:
        # Antwortstruktur passt nicht → Fallback auslösen
        return None

    return CurrentWeather(
        temperature_c=temp,
        humidity_pct=hum,
        wind_speed_ms=wind,
        gust_speed_ms=gust,
        wind_direction_deg=wdir,
        precipitation_1h_mm=prec,
        source=_SOURCE,
    )


# ── Vorhersage ────────────────────────────────────────────────────────────────

async def fetch_forecast(
    lat: float, lng: float, horizons: tuple[int, ...] = (6, 12, 24),
    org_id: int | None = None,
) -> ForecastResult | None:
    """Mehrhorizont-Vorhersage von Kachelmann.

    Pfad: `/forecast/{lat}/{lon}/{details}/{steps}` (Reihenfolge: lat, lon, Detailgrad,
    Zeitschritt). `advanced` liefert u.a. Niederschlag, `1h` = stündliche Auflösung.
    """
    data = await _get(f"/forecast/{lat}/{lng}/advanced/1h", org_id)
    if not data:
        return None
    return _parse_forecast(data, horizons)


def _extract_hourly(data: dict) -> list[dict]:
    """Findet die stündliche Reihe in der Kachelmann-Antwort (tolerant)."""
    for key in ("hourly", "hours", "data", "forecast", "timesteps"):
        v = data.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
        if isinstance(v, dict):
            for k2 in ("hourly", "hours", "data", "timesteps"):
                v2 = v.get(k2)
                if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                    return v2
    return []


def _parse_forecast(data: dict, horizons: tuple[int, ...]) -> ForecastResult | None:
    rows = _extract_hourly(data)
    if not rows:
        return None

    ref = datetime.now(UTC)
    times = [row.get("dateTime", row.get("time", row.get("timestamp"))) for row in rows]
    series = {}
    for target, aliases in (
        ("precipitation", ("precCurrent", "prec", "prec1h", "precipitation", "rr", "rain")),
        ("temperature", ("temp", "temperature", "t2m")),
        ("wind", ("windSpeed", "windspeed", "wind", "ff")),
        ("gust", ("windGust", "gust", "fx")),
        ("humidity", ("humidityRelative", "relativeHumidity")),
        ("direction", ("windDirection",)),
    ):
        values = [_pick_num(row, *aliases) for row in rows]
        if target in {"wind", "gust"}:
            values = [_norm_wind_ms(v) for v in values]
        series[target] = _hourly_series(times, values)
    # precCurrent wird als stündlicher Intervallwert am Intervallende behandelt.
    # windGust3h ist kein Stundenwert und wird daher nicht als Ersatz verwendet.
    if not any(value is not None for values in series.values() for _, value in values):
        return None
    return ForecastResult(
        horizons=_series_horizons(series, ref, horizons), source=_SOURCE, reference_time=ref,
        precipitation_hourly=series["precipitation"], gust_hourly=series["gust"],
    )
