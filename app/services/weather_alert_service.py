"""Wetterwarnung-Engine: Wetterbild, Regelauswertung und Zustandsmaschine.

Alle Bewertungsfunktionen sind rein synchron und ohne Netzwerk-Mocks testbar.
build_weather_picture() aggregiert die Daten einmal pro Loop-Durchlauf.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("einsatzleiter.weather_alert")

# ── Regelkatalog mit Default-Parametern ──────────────────────────────────────

RULE_KEYS = [
    "sturm", "starkregen", "schneefall", "glatteis",
    "gewitter", "lake_effekt", "amtlich",
    "foehn", "waldbrand", "tauwetter", "downburst",
]

RULE_DEFAULTS: dict[str, dict] = {
    "sturm":       {"vorwarn_gust_ms": 17.0, "akut_gust_ms": 25.0, "hysterese_ms": 3.0},
    "starkregen":  {"vorwarn_mmh": 15.0, "akut_mmh": 25.0, "akut_3h_mm": 30.0},
    "schneefall":  {"temp_max_c": 1.0, "vorwarn_mmh": 3.0, "akut_mmh": 5.0},
    "glatteis":    {"temp_max_c": 1.0, "temp_min_reif_c": -6.0, "spread_max_k": 0.5},
    "gewitter":    {"min_level": 2},
    "lake_effekt": {
        "temp_max_c": 1.0, "delta_t_min": 12.0,
        "dir_min": 260.0, "dir_max": 330.0,
        "v_min": 2.0, "v_max": 14.0, "rh_min": 80.0,
    },
    "amtlich":     {"min_level": 2, "nur_typen": []},
    "foehn":       {"dir_min": 150.0, "dir_max": 210.0, "vorwarn_gust_ms": 13.0, "akut_gust_ms": 15.0, "rh_max_pct": 40.0},
    "waldbrand":   {"trocken_tage": 5, "max_nieder_mm": 1.0, "temp_min_c": 25.0, "rh_max_pct": 35.0, "wind_min_ms": 3.0},
    "tauwetter":   {"temp_anstieg_k": 8.0, "temp_schwelle_c": 2.0, "pegel_trend": "steigend"},
    "downburst":   {"min_level": 3, "boe_sprung_ms": 25.0},
}

RULE_LABELS: dict[str, str] = {
    "sturm":       "Sturm / Orkan",
    "starkregen":  "Starkregen",
    "schneefall":  "Starker Schneefall",
    "glatteis":    "Glatteis / Reifglätte",
    "gewitter":    "Gewitter",
    "lake_effekt": "Bodensee-Lake-Effekt",
    "amtlich":     "Amtliche Warnung (ZAMG)",
    "foehn":       "Föhnsturm",
    "waldbrand":   "Waldbrand-Bereitschaft",
    "tauwetter":   "Tauwetter / Schneeschmelze",
    "downburst":   "Schwergewitter / Downburst",
}


# ── Datentransfer-Objekte ─────────────────────────────────────────────────────

@dataclass
class RuleResult:
    state: str          # 'none' | 'vorwarnung' | 'akut'
    detail_de: str      # menschenlesbare Begründung
    values: dict        # ausschlaggebende Messwerte für den Meldungstext
    payload_hash: str | None = None   # für amtliche Warnungen (Dedup)


@dataclass
class Decision:
    notify: bool
    new_state: str
    detail_de: str
    values: dict
    payload_hash: str | None = None


@dataclass
class WeatherPicture:
    station: Any | None       # WeatherStation | None
    current: Any | None       # CurrentWeather | None
    nowcast: Any | None       # NowcastResult | None
    forecast: Any | None      # ForecastResult | None
    warnings: list            # list[WeatherWarning]
    bodensee_temp_c: float | None = None
    precip_sum_5d_mm: float | None = None  # für 'waldbrand'
    pegel_trend: str | None = None         # 'steigend'|'fallend'|'stabil' für 'tauwetter'


# ── Wetterbild aufbauen ───────────────────────────────────────────────────────

async def build_weather_picture(org_settings, db) -> WeatherPicture:
    """Aggregiert alle Wetterdaten für eine Org (einmal pro Loop-Durchlauf)."""
    import asyncio

    from app.models.weather import WeatherStation
    from app.services.weather_service import get_current, get_forecast, get_nowcast, get_warnings

    station = (
        db.query(WeatherStation)
        .filter(WeatherStation.org_id == org_settings.org_id, WeatherStation.active == True)  # noqa: E712
        .order_by(WeatherStation.id)
        .first()
    )

    # Koordinaten: bevorzugt aktive Station, sonst Org-Fallback
    lat, lng = None, None
    if station and station.lat and station.lng:
        lat, lng = station.lat, station.lng
    else:
        org = db.query(
            __import__("app.models.master", fromlist=["FireDept"]).FireDept
        ).filter_by(id=org_settings.org_id).first()
        if org and hasattr(org, "fallback_lat") and org.fallback_lat:
            lat, lng = org.fallback_lat, org.fallback_lng

    current = forecast = nowcast = None
    warnings: list = []
    if lat and lng:
        current, nowcast, forecast, warnings = await asyncio.gather(
            get_current(lat, lng),
            get_nowcast(lat, lng),
            get_forecast(lat, lng, horizons=(6, 12, 24)),
            get_warnings(lat, lng),
            return_exceptions=True,
        )
        if isinstance(current,  Exception): current  = None   # noqa: E701
        if isinstance(nowcast,  Exception): nowcast  = None   # noqa: E701
        if isinstance(forecast, Exception): forecast = None   # noqa: E701
        if isinstance(warnings, Exception): warnings = []     # noqa: E701

    from app.services.bodensee_service import get_surface_temp_c
    bodensee_temp_c = get_surface_temp_c(org_settings, db)

    return WeatherPicture(
        station=station,
        current=current,
        nowcast=nowcast,
        forecast=forecast,
        warnings=warnings if isinstance(warnings, list) else [],
        bodensee_temp_c=bodensee_temp_c,
    )


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _p(rule, key: str):
    """Parameterwert aus rule.params, Fallback auf RULE_DEFAULTS."""
    params = rule.params or {}
    defaults = RULE_DEFAULTS.get(rule.key, {})
    return params.get(key, defaults.get(key))


def _station_val(pic: WeatherPicture, attr: str):
    """Gibt den Stationswert zurück, Fallback auf CurrentWeather."""
    if pic.station is not None:
        v = getattr(pic.station, attr, None)
        if v is not None:
            return v
    return None


def _gust(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_gust_ms") or (
        pic.current.gust_speed_ms if pic.current else None
    )


def _wind(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_wind_ms") or (
        pic.current.wind_speed_ms if pic.current else None
    )


def _temp(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_temp_c") or (
        pic.current.temperature_c if pic.current else None
    )


def _hum(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_hum_pct") or (
        pic.current.humidity_pct if pic.current else None
    )


def _rain_rate(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_rain_rate_mmh")


def _dewpoint(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_dewpoint_c")


def _wind_dir(pic: WeatherPicture) -> float | None:
    return _station_val(pic, "last_wind_dir_deg") or (
        pic.current.wind_direction_deg if pic.current else None
    )


def _forecast_gust_6h(pic: WeatherPicture) -> float | None:
    if not pic.forecast or not pic.forecast.horizons:
        return None
    h6 = next((h for h in pic.forecast.horizons if h.hours == 6), None)
    return h6.gust_speed_ms if h6 else None


def _forecast_temp_24h(pic: WeatherPicture) -> tuple[float | None, float | None]:
    """Gibt (temp_now, temp_24h) zurück für Tauwetter-Anstieg."""
    if not pic.forecast or not pic.forecast.horizons:
        return None, None
    h0 = next((h for h in pic.forecast.horizons if h.hours == 6), None)
    h24 = next((h for h in pic.forecast.horizons if h.hours == 24), None)
    t_now = h0.temperature_c if h0 else None
    t_24h = h24.temperature_c if h24 else None
    return t_now, t_24h


def _forecast_precip_6h(pic: WeatherPicture) -> float | None:
    if not pic.forecast or not pic.forecast.horizons:
        return None
    h6 = next((h for h in pic.forecast.horizons if h.hours == 6), None)
    return h6.precipitation_acc_mm if h6 else None


def _forecast_temp_6h(pic: WeatherPicture) -> float | None:
    if not pic.forecast or not pic.forecast.horizons:
        return None
    h6 = next((h for h in pic.forecast.horizons if h.hours == 6), None)
    return h6.temperature_c if h6 else None


def _nowcast_peak(pic: WeatherPicture) -> float | None:
    if not pic.nowcast:
        return None
    return pic.nowcast.peak_mm


# ── Regeln ────────────────────────────────────────────────────────────────────

def _eval_sturm(rule, pic: WeatherPicture) -> RuleResult:
    akut_ms      = _p(rule, "akut_gust_ms") or 25.0
    vorwarn_ms   = _p(rule, "vorwarn_gust_ms") or 17.0
    gust         = _gust(pic)
    forecast_gust = _forecast_gust_6h(pic)

    if gust is not None and gust >= akut_ms:
        return RuleResult("akut", f"Böe {gust:.1f} m/s ≥ Schwelle {akut_ms} m/s",
                          {"gust_ms": gust, "threshold_ms": akut_ms})
    if forecast_gust is not None and forecast_gust >= vorwarn_ms:
        return RuleResult("vorwarnung", f"Prognose-Böe {forecast_gust:.1f} m/s ≥ {vorwarn_ms} m/s in ≤6 h",
                          {"forecast_gust_ms": forecast_gust, "threshold_ms": vorwarn_ms})
    return RuleResult("none", "", {})


def _eval_starkregen(rule, pic: WeatherPicture) -> RuleResult:
    akut_mmh     = _p(rule, "akut_mmh") or 25.0
    vorwarn_mmh  = _p(rule, "vorwarn_mmh") or 15.0
    rain_rate    = _rain_rate(pic)
    nowcast_peak = _nowcast_peak(pic)

    if rain_rate is not None and rain_rate >= akut_mmh:
        return RuleResult("akut", f"Niederschlagsrate {rain_rate:.1f} mm/h ≥ {akut_mmh} mm/h",
                          {"rain_rate_mmh": rain_rate})
    if nowcast_peak is not None and nowcast_peak >= vorwarn_mmh:
        return RuleResult("vorwarnung", f"Nowcast-Peak {nowcast_peak:.1f} mm/h ≥ {vorwarn_mmh} mm/h in ≤60 min",
                          {"nowcast_peak_mmh": nowcast_peak})
    return RuleResult("none", "", {})


def _eval_schneefall(rule, pic: WeatherPicture) -> RuleResult:
    temp_max     = _p(rule, "temp_max_c") or 1.0
    akut_mmh     = _p(rule, "akut_mmh") or 5.0
    vorwarn_mmh  = _p(rule, "vorwarn_mmh") or 3.0
    temp         = _temp(pic)
    rain_rate    = _rain_rate(pic)
    f_temp       = _forecast_temp_6h(pic)
    f_precip     = _forecast_precip_6h(pic)

    if temp is not None and temp <= temp_max and rain_rate is not None and rain_rate >= akut_mmh:
        return RuleResult("akut", f"Schneefall: {rain_rate:.1f} mm/h bei {temp:.1f} °C",
                          {"rain_rate_mmh": rain_rate, "temp_c": temp})
    if (f_temp is not None and f_temp <= temp_max
            and f_precip is not None and f_precip >= vorwarn_mmh):
        return RuleResult("vorwarnung", f"Prognose: {f_precip:.1f} mm Niederschlag bei {f_temp:.1f} °C in ≤6 h",
                          {"forecast_precip_mm": f_precip, "forecast_temp_c": f_temp})
    return RuleResult("none", "", {})


def _eval_glatteis(rule, pic: WeatherPicture) -> RuleResult:
    temp_max       = _p(rule, "temp_max_c") or 1.0
    temp_min_reif  = _p(rule, "temp_min_reif_c") or -6.0
    spread_max     = _p(rule, "spread_max_k") or 0.5
    temp           = _temp(pic)
    dew            = _dewpoint(pic)
    rain_rate      = _rain_rate(pic)
    f_temp         = _forecast_temp_6h(pic)
    f_precip       = _forecast_precip_6h(pic)

    if temp is not None and temp_min_reif <= temp <= temp_max:
        reifglaette = (dew is not None and temp < 0 and (temp - dew) <= spread_max)
        gefrierregen = rain_rate is not None and rain_rate > 0
        if gefrierregen or reifglaette:
            reason = "Gefrierregen" if gefrierregen else "Reifglätte (Taupunkt-Spread ≤ 0.5 K)"
            return RuleResult("akut", f"Glatteisgefahr: {reason} bei {temp:.1f} °C",
                              {"temp_c": temp, "rain_rate_mmh": rain_rate, "dew_c": dew})
    if f_temp is not None and f_temp <= temp_max and f_precip is not None and f_precip > 0:
        return RuleResult("vorwarnung",
                          f"Gefrierregen-Risiko: {f_precip:.1f} mm Prognose bei {f_temp:.1f} °C",
                          {"forecast_temp_c": f_temp, "forecast_precip_mm": f_precip})
    return RuleResult("none", "", {})


def _eval_gewitter(rule, pic: WeatherPicture) -> RuleResult:
    min_level = int(_p(rule, "min_level") or 2)
    THUNDER_TYPES = {"THUNDERSTORM", "THUNDER", "GEWITTER"}

    active = [w for w in pic.warnings
              if w.event_type.upper() in THUNDER_TYPES and w.level >= min_level]
    if active:
        w = active[0]
        return RuleResult("akut", f"Amtliche Gewitter-Warnung Level {w.level}: {w.text[:100]}",
                          {"level": w.level, "event_type": w.event_type},
                          payload_hash=_warning_hash(w))

    # Vorwarnung über niedrige Warnstufe oder erhöhten Nowcast
    prev = [w for w in pic.warnings
            if w.event_type.upper() in THUNDER_TYPES and w.level >= 1]
    if prev:
        w = prev[0]
        return RuleResult("vorwarnung",
                          f"Gewitter-Warnung Level {w.level} – {w.text[:100]}",
                          {"level": w.level}, payload_hash=_warning_hash(w))
    return RuleResult("none", "", {})


def _eval_lake_effekt(rule, pic: WeatherPicture) -> RuleResult:
    temp_max    = _p(rule, "temp_max_c") or 1.0
    delta_t_min = _p(rule, "delta_t_min") or 12.0
    dir_min     = _p(rule, "dir_min") or 260.0
    dir_max     = _p(rule, "dir_max") or 330.0
    v_min       = _p(rule, "v_min") or 2.0
    v_max       = _p(rule, "v_max") or 14.0
    rh_min      = _p(rule, "rh_min") or 80.0

    temp        = _temp(pic)
    wind        = _wind(pic)
    wind_dir    = _wind_dir(pic)
    hum         = _hum(pic)
    rain_rate   = _rain_rate(pic)
    bodensee    = pic.bodensee_temp_c

    if temp is None or wind is None or wind_dir is None or bodensee is None:
        return RuleResult("none", "", {})

    # Windrichtung im Sektor? (zirkularer Check)
    in_sector = dir_min <= wind_dir <= dir_max
    # Alle Heuristik-Bedingungen
    heuristik = (
        temp <= temp_max
        and (bodensee - temp) >= delta_t_min
        and in_sector
        and v_min <= wind <= v_max
        and (hum is not None and hum >= rh_min or rain_rate is not None and rain_rate > 0)
    )
    if not heuristik:
        return RuleResult("none", "", {})

    values = {
        "temp_c": temp, "bodensee_c": bodensee, "delta_t_k": round(bodensee - temp, 1),
        "wind_ms": wind, "wind_dir_deg": wind_dir, "hum_pct": hum,
    }
    # Akut: Heuristik erfüllt UND Schneefall gemessen
    if rain_rate is not None and rain_rate > 0 and temp <= temp_max:
        return RuleResult("akut", "Lake-Effekt aktiv: Schneeschauer von Bodensee",  values)
    return RuleResult("vorwarnung", "Lake-Effekt-Risiko: erhöhte Wahrscheinlichkeit für Schneeschauer aus NW/W", values)


def _eval_amtlich(rule, pic: WeatherPicture) -> RuleResult:
    min_level = int(_p(rule, "min_level") or 2)
    nur_typen: list = _p(rule, "nur_typen") or []

    matching = [
        w for w in pic.warnings
        if w.level >= min_level
        and (not nur_typen or w.event_type.upper() in [t.upper() for t in nur_typen])
    ]
    if not matching:
        return RuleResult("none", "", {})

    # Alle Warnungen zu einem Hash zusammenfassen (Dedup)
    combined_hash = hashlib.sha256(
        "|".join(_warning_hash(w) or "" for w in matching).encode()
    ).hexdigest()[:16]

    labels = ", ".join(f"{w.event_type} L{w.level}" for w in matching[:3])
    return RuleResult("akut",
                      f"Amtliche Warnung(en): {labels}",
                      {"count": len(matching), "types": [w.event_type for w in matching]},
                      payload_hash=combined_hash)


def _eval_foehn(rule, pic: WeatherPicture) -> RuleResult:
    dir_min      = _p(rule, "dir_min") or 150.0
    dir_max      = _p(rule, "dir_max") or 210.0
    akut_gust    = _p(rule, "akut_gust_ms") or 15.0
    vorwarn_gust = _p(rule, "vorwarn_gust_ms") or 13.0
    rh_max       = _p(rule, "rh_max_pct") or 40.0

    gust     = _gust(pic)
    wind_dir = _wind_dir(pic)
    hum      = _hum(pic)
    f_gust   = _forecast_gust_6h(pic)

    if (gust is not None and gust >= akut_gust
            and wind_dir is not None and dir_min <= wind_dir <= dir_max
            and hum is not None and hum <= rh_max):
        return RuleResult("akut",
                          f"Föhn: Böe {gust:.1f} m/s, Richtung {wind_dir:.0f}°, Feuchte {hum:.0f} %",
                          {"gust_ms": gust, "wind_dir_deg": wind_dir, "hum_pct": hum})
    if f_gust is not None and f_gust >= vorwarn_gust:
        return RuleResult("vorwarnung",
                          f"Föhn-Prognose: Böe {f_gust:.1f} m/s in ≤6 h",
                          {"forecast_gust_ms": f_gust})
    return RuleResult("none", "", {})


def _eval_waldbrand(rule, pic: WeatherPicture) -> RuleResult:
    max_nieder   = _p(rule, "max_nieder_mm") or 1.0
    temp_min     = _p(rule, "temp_min_c") or 25.0
    rh_max       = _p(rule, "rh_max_pct") or 35.0
    wind_min     = _p(rule, "wind_min_ms") or 3.0

    if pic.precip_sum_5d_mm is None:
        return RuleResult("none", "", {})  # keine Zeitreihe verfügbar

    temp  = _temp(pic)
    hum   = _hum(pic)
    wind  = _wind(pic)

    if (pic.precip_sum_5d_mm <= max_nieder
            and temp is not None and temp >= temp_min
            and hum is not None and hum <= rh_max
            and wind is not None and wind >= wind_min):
        return RuleResult("akut",
                          f"Waldbrand-Bereitschaft: 5-Tage-Nieder. {pic.precip_sum_5d_mm:.1f} mm,"
                          f" T {temp:.1f} °C, Feuchte {hum:.0f} %, Wind {wind:.1f} m/s",
                          {"precip_5d_mm": pic.precip_sum_5d_mm, "temp_c": temp,
                           "hum_pct": hum, "wind_ms": wind})
    return RuleResult("none", "", {})


def _eval_tauwetter(rule, pic: WeatherPicture) -> RuleResult:
    anstieg_k   = _p(rule, "temp_anstieg_k") or 8.0
    schwelle_c  = _p(rule, "temp_schwelle_c") or 2.0

    temp = _temp(pic)
    t_now, t_24h = _forecast_temp_24h(pic)

    if (temp is not None and temp >= schwelle_c
            and pic.pegel_trend == "steigend"):
        return RuleResult("akut",
                          f"Tauwetter: T {temp:.1f} °C, Pegel steigend",
                          {"temp_c": temp, "pegel_trend": pic.pegel_trend})
    if t_now is not None and t_24h is not None and (t_24h - t_now) >= anstieg_k and t_24h > 0:
        return RuleResult("vorwarnung",
                          f"Tauwetter-Prognose: T-Anstieg {t_24h - t_now:.1f} K in ≤24 h",
                          {"temp_now_c": t_now, "temp_24h_c": t_24h,
                           "anstieg_k": round(t_24h - t_now, 1)})
    return RuleResult("none", "", {})


def _eval_downburst(rule, pic: WeatherPicture) -> RuleResult:
    min_level   = int(_p(rule, "min_level") or 3)
    HEAVY_TYPES = {"THUNDERSTORM", "THUNDER", "WIND", "STORM"}

    active = [w for w in pic.warnings
              if w.event_type.upper() in HEAVY_TYPES and w.level >= min_level]
    if active:
        w = active[0]
        return RuleResult("akut",
                          f"Schwergewitter/Downburst: Level {w.level} – {w.text[:100]}",
                          {"level": w.level}, payload_hash=_warning_hash(w))

    # Vorwarnung: niedrigere Level
    prev = [w for w in pic.warnings
            if w.event_type.upper() in HEAVY_TYPES and w.level >= min_level - 1]
    if prev:
        w = prev[0]
        return RuleResult("vorwarnung",
                          f"Schwergewitter-Warnung Level {w.level}",
                          {"level": w.level}, payload_hash=_warning_hash(w))
    return RuleResult("none", "", {})


_EVALUATORS = {
    "sturm":       _eval_sturm,
    "starkregen":  _eval_starkregen,
    "schneefall":  _eval_schneefall,
    "glatteis":    _eval_glatteis,
    "gewitter":    _eval_gewitter,
    "lake_effekt": _eval_lake_effekt,
    "amtlich":     _eval_amtlich,
    "foehn":       _eval_foehn,
    "waldbrand":   _eval_waldbrand,
    "tauwetter":   _eval_tauwetter,
    "downburst":   _eval_downburst,
}


def evaluate_rule(rule, pic: WeatherPicture) -> RuleResult:
    """Bewertet eine einzelne Regel anhand des Wetterbilds."""
    fn = _EVALUATORS.get(rule.key)
    if fn is None:
        return RuleResult("none", "", {})
    try:
        return fn(rule, pic)
    except Exception:
        logger.exception("evaluate_rule: Fehler bei Regel %s", rule.key)
        return RuleResult("none", "", {})


def _warning_hash(w) -> str:
    raw = f"{w.event_type}|{w.valid_from}|{w.valid_to}|{w.level}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Zustandsmaschine ──────────────────────────────────────────────────────────

_HYSTERESE_CYCLES = 2  # Zyklen unter Schwelle, bis Akut verlassen wird


def apply_state_machine(rule, result: RuleResult, state_row) -> Decision:
    """Bestimmt ob eine Benachrichtigung ausgelöst wird und den neuen Zustand.

    Berücksichtigt Hysterese, Cooldown und Eskalation.
    """
    now = datetime.now(UTC)
    old_state = state_row.state if state_row else "none"
    new_state  = result.state

    # Hysterese: 'akut' erst verlassen wenn 2 Zyklen unter Schwelle
    if old_state == "akut" and new_state != "akut":
        cycles = (state_row.below_threshold_cycles or 0) + 1
        if cycles < _HYSTERESE_CYCLES:
            # Noch nicht wechseln
            state_row.below_threshold_cycles = cycles
            return Decision(False, "akut", result.detail_de, result.values, result.payload_hash)
    elif new_state == "akut":
        if state_row:
            state_row.below_threshold_cycles = 0

    # Amtliche Warnungen deduplizieren
    if result.payload_hash and state_row and state_row.last_payload_hash == result.payload_hash:
        return Decision(False, new_state, result.detail_de, result.values, result.payload_hash)

    # Eskalation vorwarnung → akut: immer senden (ignoriert Cooldown)
    eskalation = old_state == "vorwarnung" and new_state == "akut" and rule.eskalation

    # Cooldown: gleicher Zustand
    if not eskalation and old_state == new_state and new_state != "none" and state_row:
        last = state_row.last_notified_at
        if last is not None:
            last_utc = last.replace(tzinfo=UTC) if last.tzinfo is None else last
            cooldown_s = (rule.cooldown_min or 60) * 60
            if (now - last_utc).total_seconds() < cooldown_s:
                return Decision(False, new_state, result.detail_de, result.values,
                                result.payload_hash)

    notify = new_state != "none" and (new_state != old_state or eskalation)
    # Bei Neu-Eintritt in Vorwarnung: auch senden
    if new_state == "vorwarnung" and old_state == "none" and rule.vorwarnung:
        notify = True

    return Decision(notify, new_state, result.detail_de, result.values, result.payload_hash)


# ── Seeding ───────────────────────────────────────────────────────────────────

def ensure_rules(org_id: int, db) -> None:
    """Legt fehlende WeatherAlertRule-Einträge mit Defaults an (idempotent)."""
    from datetime import UTC, datetime

    from app.models.weather_alert import WeatherAlertRule

    existing_keys = {
        r.key for r in db.query(WeatherAlertRule)
        .filter(WeatherAlertRule.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .all()
    }
    for key in RULE_KEYS:
        if key not in existing_keys:
            db.add(WeatherAlertRule(
                org_id=org_id,
                key=key,
                enabled=False,
                params=RULE_DEFAULTS.get(key, {}),
                updated_at=datetime.now(UTC),
            ))
    db.commit()
