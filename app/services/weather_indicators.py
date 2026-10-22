"""Reine Kennzahlen für interne Hinweise, ohne Alarmierung oder Datenabruf.

Messfenster werden vorab mit observed_precip geladen. Eine Spitzenintensität
kommt ausschließlich aus stündlichen Intervallwerten, nie aus Fenstersummen.
Frische wird vom Aufrufer über DataMeta bzw. ObservedPrecipitation übergeben.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.services.abfluss_service import AbflussMessung
from app.services.weather_service import (
    CurrentWeather,
    DataMeta,
    ForecastHorizon,
    ForecastResult,
    WarningsResult,
    WeatherWarning,
)
from app.services.weather_thresholds import (
    BF8,
    ICE_MIN_RAIN,
    ICE_TEMP_HIGH,
    ICE_TEMP_LOW,
    RAIN_FC6_WARN,
    RAIN_NOWCAST_WARN,
    SNOW_FC6_WARN,
    SNOW_NOWCAST_WARN,
    SNOW_TEMP_MAX,
)
from app.services.weather_timeseries import window_max

HOURS = (1, 3, 6, 12, 24)
Status = Literal["vollständig", "unvollständig"]
Rating = Literal["mögliche Gefahr"] | None


@dataclass(frozen=True)
class ObservedPrecipitation:
    windows: Mapping[int, tuple[float | None, bool]]
    freshness: Literal["fresh", "aging", "stale"] = "fresh"


@dataclass(frozen=True)
class PrecipitationWindow:
    hours: int
    mm: float | None
    complete: bool


@dataclass(frozen=True)
class GustWindow:
    hours: int
    max_ms: float | None
    at: datetime | None
    complete: bool


@dataclass(frozen=True)
class Assessment:
    status: Status
    rating: Rating
    hint: str


@dataclass(frozen=True)
class HeavyRainIndicators(Assessment):
    observed: tuple[PrecipitationWindow, ...]
    forecast: tuple[PrecipitationWindow, ...]
    peak_mmh: float | None
    peak_at: datetime | None


@dataclass(frozen=True)
class StormIndicators(Assessment):
    current_gust_ms: float | None
    forecast: tuple[GustWindow, ...]


@dataclass(frozen=True)
class SnowIceIndicators(Assessment):
    temperature_c: float | None
    snowfall_cm: float | None
    forecast: tuple[ForecastHorizon, ...]


@dataclass(frozen=True)
class ThunderIndicators(Assessment):
    official_warnings: tuple[WeatherWarning, ...]
    precipitation: tuple[PrecipitationWindow, ...]
    gusts: tuple[GustWindow, ...]


@dataclass(frozen=True)
class FloodIndicators(Assessment):
    precipitation: tuple[PrecipitationWindow, ...]
    abfluss: tuple[AbflussMessung, ...]


@dataclass(frozen=True)
class WeatherIndicators:
    heavy_rain: HeavyRainIndicators
    storm: StormIndicators
    snow: SnowIceIndicators
    ice: SnowIceIndicators
    thunder: ThunderIndicators
    flood: FloodIndicators


def _fresh(meta: DataMeta | None) -> bool:
    return meta is None or meta.freshness != "stale"


def _assessment(complete: bool, danger: bool, hint: str = "") -> tuple[Status, Rating, str]:
    if not complete:
        return "unvollständig", None, "Daten unvollständig"
    return "vollständig", "mögliche Gefahr" if danger else None, hint


def build_indicators(
    current: CurrentWeather | None,
    forecast: ForecastResult | None,
    observed: ObservedPrecipitation | Mapping[int, tuple[float | None, bool]] | None,
    warnings_result: WarningsResult | None,
    abfluss: Sequence[AbflussMessung] | None = None,
) -> WeatherIndicators:
    """Kennzahlen je Szenario; fehlende oder veraltete Daten erlauben keine Bewertung.

    Nullwerte sind gültig. Ohne Metadaten wird die Frische durch den Aufrufer
    zugesichert. Abflusswerte dienen nur als Kontext; daraus wird hier keine
    Hochwasserbewertung abgeleitet. Warnungen müssen vom Aufrufer zeitlich auf
    das betrachtete Fenster eingeschränkt werden (keine interne Uhr/I/O).
    """
    obs = observed if isinstance(observed, ObservedPrecipitation) else ObservedPrecipitation(observed or {})
    measured = tuple(PrecipitationWindow(h, *obs.windows.get(h, (None, False))) for h in HOURS)
    horizons = {h.hours: h for h in forecast.horizons} if forecast else {}
    fc_fresh = forecast is not None and _fresh(forecast.meta)
    rain = tuple(PrecipitationWindow(
        h, horizons[h].precipitation_mm if h in horizons else None,
        fc_fresh and h in horizons and horizons[h].complete and horizons[h].precipitation_mm is not None,
    ) for h in HOURS)
    gusts = tuple(GustWindow(
        h, horizons[h].gust_max_ms if h in horizons else None,
        horizons[h].gust_max_at if h in horizons else None,
        fc_fresh and h in horizons and horizons[h].complete
        and horizons[h].gust_max_ms is not None and horizons[h].gust_max_at is not None,
    ) for h in HOURS if h != 1)
    peak, peak_at, peak_ok = (None, None, False)
    if forecast and forecast.reference_time is not None:
        peak, peak_at, peak_ok = window_max(forecast.precipitation_hourly, forecast.reference_time, 24)
    rain_ok = (all(w.complete and w.mm is not None for w in measured)
               and obs.freshness != "stale" and all(w.complete for w in rain) and peak_ok)
    rain_danger = ((measured[1].mm is not None and measured[1].mm >= RAIN_NOWCAST_WARN)
                   or (rain[2].mm is not None and rain[2].mm >= RAIN_FC6_WARN))
    current_ok = current is not None and _fresh(current.meta)
    gust = current.gust_speed_ms if current else None
    storm_ok = current_ok and gust is not None and all(w.complete for w in gusts)
    storm_danger = any(v is not None and v >= BF8 for v in [gust, *(w.max_ms for w in gusts)])
    temp = current.temperature_c if current else None
    snow = current.snowfall_cm if current else None
    temperature_ok = (fc_fresh and all(
        h in horizons and horizons[h].complete and horizons[h].temperature_min_c is not None
        for h in HOURS
    ))
    snow_ok = (current_ok and temp is not None and snow is not None and temperature_ok
               and all(w.complete for w in rain)
               and all(h in horizons and horizons[h].complete and horizons[h].snowfall_cm is not None
                       and horizons[h].temperature_min_c is not None for h in HOURS))
    snow_danger = (
        (temp is not None and temp <= SNOW_TEMP_MAX and snow is not None and snow >= SNOW_NOWCAST_WARN)
        or (6 in horizons and horizons[6].temperature_min_c is not None
            and horizons[6].temperature_min_c <= SNOW_TEMP_MAX
            and rain[2].mm is not None and rain[2].mm >= SNOW_FC6_WARN)
    )
    ice_rain = current.precipitation_1h_mm if current else None
    ice_ok = (current_ok and temp is not None and ice_rain is not None
              and temperature_ok and all(w.complete for w in rain))
    ice_danger = (temp is not None and ICE_TEMP_LOW <= temp <= ICE_TEMP_HIGH
                  and ice_rain is not None and ice_rain >= ICE_MIN_RAIN) or any(
        h.temperature_min_c is not None
        and ICE_TEMP_LOW <= h.temperature_min_c <= ICE_TEMP_HIGH
        and h.precipitation_mm is not None and h.precipitation_mm >= ICE_MIN_RAIN
        for h in horizons.values() if h.hours in HOURS
    )
    official = tuple(w for w in warnings_result.warnings if w.event_type in ("Gewitter", "THUNDERSTORM")) \
        if warnings_result else ()
    thunder_ok = (warnings_result is not None
                  and warnings_result.status in ("ok_no_warnings", "ok_with_warnings")
                  and all(w.complete for w in rain) and all(w.complete for w in gusts))
    snow_horizons = tuple(horizons[h] for h in HOURS if h in horizons)
    return WeatherIndicators(
        heavy_rain=HeavyRainIndicators(*_assessment(rain_ok, rain_danger), measured, rain, peak, peak_at),
        storm=StormIndicators(*_assessment(storm_ok, storm_danger), gust, gusts),
        snow=SnowIceIndicators(*_assessment(snow_ok, snow_danger), temp, snow, snow_horizons),
        ice=SnowIceIndicators(*_assessment(ice_ok, ice_danger), temp, snow, snow_horizons),
        thunder=ThunderIndicators(*_assessment(thunder_ok, bool(official)), official, rain, gusts),
        flood=FloodIndicators(
            "vollständig" if all(w.complete for w in rain) else "unvollständig", None,
            "Niederschlag nur als Kontext. Für Hochwassergefahr Pegel und amtliche Hinweise prüfen.",
            rain, tuple(abfluss or ()),
        ),
    )
