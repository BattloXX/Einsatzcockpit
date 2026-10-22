"""Gemeinsame Darstellung ohne zusätzliche Wetterabrufe."""
from datetime import UTC, datetime

from app.services.weather_indicators import build_indicators
from app.services.weather_service import CurrentWeather, ForecastResult, NowcastResult, WarningsResult

SOURCE_LABELS = {
    "kachelmann": "Kachelmann Wetter", "geosphere": "GeoSphere Austria (ZAMG)",
    "geosphere_nwp": "GeoSphere Austria (ZAMG)", "openmeteo": "Open-Meteo",
    "open-meteo-geosphere": "Open-Meteo / GeoSphere Austria", "station": "Lokale Wetterstation",
}


def presentation(current: CurrentWeather | None, forecast: ForecastResult | None,
                 nowcast: NowcastResult | None, warnings: WarningsResult) -> dict:
    """Metadaten und vollständige Fenster; Null ist ein gültiger Messwert."""
    indicators = build_indicators(current, forecast, None, warnings, None)
    now = datetime.now(UTC)
    datenstand = []
    for label, obj in (("Aktuell", current), ("Stundenprognose", forecast), ("Nowcast", nowcast)):
        meta = obj.meta if obj else None
        if meta:
            fetched = meta.fetched_at.replace(tzinfo=UTC) if meta.fetched_at.tzinfo is None else meta.fetched_at
            datenstand.append({
                "label": label, "provider": SOURCE_LABELS.get(meta.provider, meta.provider),
                "model": meta.model_or_station, "age_minutes": max(0, int((now - fetched).total_seconds() / 60)),
                "freshness": meta.freshness, "is_fallback": meta.is_fallback, "kind": meta.kind,
            })
    datenstand.append({
        "label": "Warnungen", "provider": "GeoSphere Austria",
        "model": None, "age_minutes": max(0, int((now - (
            warnings.fetched_at.replace(tzinfo=UTC) if warnings.fetched_at.tzinfo is None
            else warnings.fetched_at)).total_seconds() / 60)) if warnings.fetched_at else None,
        "freshness": warnings.status, "is_fallback": False, "kind": "official",
    })
    horizons = {h.hours: h for h in forecast.horizons} if forecast else {}
    short = []
    for rain in indicators.heavy_rain.forecast:
        h = horizons.get(rain.hours)
        complete = bool(rain.complete and h and h.gust_max_ms is not None and h.gust_max_at is not None)
        short.append({
            "hours": rain.hours, "precipitation_mm": rain.mm,
            "temperature_c": h.temperature_c if h else None,
            "wind_speed_ms": h.wind_speed_ms if h else None,
            "gust_max_ms": h.gust_max_ms if h else None,
            "gust_max_kmh": round(h.gust_max_ms * 3.6, 1) if h and h.gust_max_ms is not None else None,
            "gust_max_at": h.gust_max_at if h else None,
            "complete": complete,
        })
    hints = []
    for label, assessment in (("Starkregen", indicators.heavy_rain), ("Sturm", indicators.storm),
                              ("Schnee", indicators.snow), ("Glätte", indicators.ice),
                              ("Gewitter", indicators.thunder)):
        if assessment.rating:
            hints.append({"label": label, "rating": assessment.rating})
    frost = bool(current and current.temperature_c is not None and current.temperature_c < 0
                 and (current.meta is None or current.meta.freshness != "stale"))
    return {"frost": frost, "ice": bool(indicators.ice.rating), "indicators": indicators,
            "datenstand": datenstand, "prognose_kurz": short,
            "internal_hints": hints, "forecast": forecast}
