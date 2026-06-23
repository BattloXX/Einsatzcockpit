"""UI-Router: Wetter-Panel (HTMX-Partials für GSL-Board, Einzeleinsatz und /wetter-Seite)."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import can_access_incident, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import MajorIncident
from app.services import weather_service
from app.services.weather_focus import resolve_weather_focus
from app.services.weather_service import analyze_weather, has_cached

router = APIRouter()
logger = logging.getLogger("einsatzleiter.weather")

_TREND_DE = {
    "increasing": "zunehmend",
    "decreasing": "abnehmend",
    "stable":     "gleichbleibend",
}

_WIND_DIR_LABELS = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def _wind_dir_label(deg: float | None) -> str:
    if deg is None:
        return "–"
    return _WIND_DIR_LABELS[round(deg / 45) % 8]


_SOURCE_LABELS = {
    "kachelmann": "Kachelmann Wetter",
    "geosphere": "GeoSphere Austria (ZAMG)",
    "geosphere_nwp": "GeoSphere Austria (ZAMG)",
    "openmeteo": "Open-Meteo",
    "station": "Lokale Wetterstation",
}


def _build_attribution(
    current, forecast, nowcast, warnings, station_current=None
) -> str:
    """Baut die Quellenangabe aus den tatsächlich genutzten Datenquellen.

    Warnungen kommen immer von ZAMG/GeoSphere und werden separat ausgewiesen.
    """
    sources: list[str] = []
    for obj in (station_current, current, forecast, nowcast):
        src = getattr(obj, "source", None)
        if src:
            label = _SOURCE_LABELS.get(src, src)
            if label not in sources:
                sources.append(label)
    parts: list[str] = []
    if sources:
        parts.append(" · ".join(sources))
    if warnings:
        parts.append("Warnungen: ZAMG/GeoSphere")
    # Hinweis: Template stellt "Daten: " bereits voran.
    return " | ".join(parts) if parts else weather_service.GEOSPHERE_ATTRIBUTION


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org(user, org_id: int) -> None:
    if not same_org_or_system_admin(user, org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff")


def _org_weather_enabled(org_id: int | None, db: Session) -> bool:
    """Returns False only if org has explicitly disabled weather in OrgSettings.

    NULL in org_settings.weather_enabled means "use global default" (True).
    """
    if not settings.WEATHER_ENABLED:
        return False
    if org_id is None:
        return True
    from app.models.master import OrgSettings as _OS
    s = db.query(_OS).filter(_OS.org_id == org_id).first()
    if s is None or s.weather_enabled is None:
        return True
    return bool(s.weather_enabled)


def _build_nowcast_bars(now: "weather_service.NowcastResult") -> list[dict]:
    """Pre-computes bar chart data for the Nowcast sparkline in the template."""
    max_rr = max((s.precipitation_mm for s in now.steps), default=0.0) or 0.1
    bars = []
    for i, step in enumerate(now.steps):
        rr = step.precipitation_mm
        h = max(int(rr / max_rr * 34), 2)
        if rr >= 2.0:
            color = "#1d4ed8"
        elif rr >= 0.5:
            color = "#3b82f6"
        elif rr >= 0.1:
            color = "#93c5fd"
        else:
            color = "rgba(255,255,255,.08)"
        offset_min = i * 15
        bars.append({"h": h, "color": color, "label": f"+{offset_min} min: {rr:.1f} mm"})
    return bars


_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _fmt_warn_time(dt: datetime) -> str:
    """Formatiert einen Warnungs-Zeitpunkt in Wiener Lokalzeit: 'Fr 19.06. 14:00'."""
    local = dt.astimezone(weather_service._VIENNA_TZ)
    return f"{_WEEKDAYS_DE[local.weekday()]} {local:%d.%m.} {local:%H:%M}"


def _warn_zeitraum(valid_from: datetime, valid_to: datetime) -> str:
    """Kompakter Gültigkeitszeitraum; bei gleichem Tag wird das Datum nicht doppelt gezeigt."""
    a = valid_from.astimezone(weather_service._VIENNA_TZ)
    b = valid_to.astimezone(weather_service._VIENNA_TZ)
    if a.date() == b.date():
        return f"{_fmt_warn_time(valid_from)}–{b:%H:%M}"
    return f"{_fmt_warn_time(valid_from)} – {_fmt_warn_time(valid_to)}"


def _build_warning_views(warnings: list) -> list[dict]:
    """Baut Anzeige-dicts für ALLE aktiven Warnungen (Stufen-Farbe + Zeitraum)."""
    views: list[dict] = []
    for w in warnings:
        views.append({
            "level": w.level,
            "event_type": w.event_type,
            "text": w.text or "",
            "color": weather_service._WARN_LEVEL_COLORS.get(w.level, "#6b7280"),
            "zeitraum": _warn_zeitraum(w.valid_from, w.valid_to),
        })
    return views


def _peak_label(now: "weather_service.NowcastResult") -> str | None:
    """Returns human-readable time to peak, or None if no precipitation."""
    if now.peak_mm < 0.05 or now.peak_at is None:
        return None
    delta_min = int((now.peak_at - datetime.now(UTC)).total_seconds() / 60)
    delta_min = max(delta_min, 0)
    if delta_min < 5:
        return "jetzt"
    return f"in ca. {delta_min} min"


def _next_hq_label(wert: float | None, hq) -> str | None:
    """Naechstes HQ-Schwellenwert-Label ueber dem aktuellen Wert."""
    if wert is None:
        return None
    for val, name in [
        (hq.hq1, "HQ1"), (hq.hq5, "HQ5"), (hq.hq10, "HQ10"),
        (hq.hq30, "HQ30"), (hq.hq100, "HQ100"), (hq.hq300, "HQ300"), (hq.hq1000, "HQ1000"),
    ]:
        if val is not None and val > wert:
            return f"{name}: {val:,.0f} m³/s".replace(",", ".")
    return None


async def _build_abfluss_views(org_id: int | None, db: Session) -> list[dict]:
    """Pegel-Stationen der Org laden, Daten aktualisieren und template-ready Views bauen."""
    if not org_id:
        return []
    from app.models.master import OrgSettings as _OS
    from app.services import abfluss_service
    from zoneinfo import ZoneInfo

    org_s = db.query(_OS).filter(_OS.org_id == org_id).first()
    if not org_s:
        return []
    stationen = org_s.abfluss_stationen_list
    if not stationen:
        return []

    states = await abfluss_service.refresh_all_for_org(org_id, stationen)
    _vt = ZoneInfo("Europe/Vienna")
    views: list[dict] = []
    for st in states:
        if st.aktuell:
            stufe, label, farbe = abfluss_service.alarm_stufe(st.aktuell.wert_m3s, st.hq_werte)
            wert = st.aktuell.wert_m3s
            ts_str = st.aktuell.zeitstempel.astimezone(_vt).strftime("%d.%m. %H:%M")
        else:
            stufe, label, farbe = 0, "–", "var(--text-muted)"
            wert = None
            ts_str = None
        views.append({
            "hzbnr":             st.hzbnr,
            "name":              st.name,
            "beschreibung":      st.beschreibung,
            "wert":              wert,
            "stufe":             stufe,
            "stufe_label":       label,
            "stufe_farbe":       farbe,
            "sparkline":         abfluss_service.sparkline_data(st),
            "naechste_hq":       _next_hq_label(wert, st.hq_werte),
            "zeitstempel_label": ts_str,
            "fehler":            st.letzter_fehler,
        })
    return views


# Station gilt als "offline", wenn der letzte Push länger als so viele Minuten her ist
# (Davis/Meteobridge pusht alle 5 min ⇒ 15 min = 3 verpasste Intervalle).
_STATION_STALE_MIN = 15


def _seen_label(age_min: int) -> str:
    if age_min < 1:
        return "gerade eben"
    if age_min < 60:
        return f"vor {age_min} min"
    return f"vor {age_min // 60} h"


def _build_station_views(org_id: int | None, db: Session) -> list[dict]:
    """Lädt die aktiven Wetterstationen der Org und baut template-ready Ist-Stand-Views."""
    if not org_id:
        return []
    from app.models.weather import WeatherStation
    stations = (
        db.query(WeatherStation)
        .filter(WeatherStation.org_id == org_id, WeatherStation.active == True)  # noqa: E712
        .order_by(WeatherStation.name)
        .all()
    )
    now = datetime.now(UTC)
    views: list[dict] = []
    for s in stations:
        last_seen = s.last_seen_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        online = False
        seen_label = None
        if last_seen is not None:
            age_min = max(int((now - last_seen).total_seconds() / 60), 0)
            online = age_min <= _STATION_STALE_MIN
            seen_label = _seen_label(age_min)
        views.append({
            "id":        s.id,
            "name":      s.name,
            "online":    online,
            "seen_label": seen_label,
            "temp":      s.last_temp_c,
            "hum":       s.last_hum_pct,
            "wind":      s.last_wind_ms,
            "gust":      s.last_gust_ms,
            "wind_dir":  _wind_dir_label(s.last_wind_dir_deg),
            "pressure":  s.last_pressure_hpa,
            "rain_rate": s.last_rain_rate_mmh,
            "rain_day":  s.last_rain_day_mm,
            "dew":       s.last_dewpoint_c,
            "solar":     s.last_solar_wm2,
            "uv":        s.last_uv,
        })
    return views


def _station_current_weather(
    station_views: list[dict],
) -> weather_service.CurrentWeather | None:
    """Baut ein CurrentWeather-Objekt aus der ersten Online-Station für Szenario-Analyse.

    Wird dem NWP-Modellwert vorgezogen, wenn die Station live ist.
    """
    for sv in station_views:
        if sv.get("online"):
            return weather_service.CurrentWeather(
                temperature_c=sv.get("temp"),
                wind_speed_ms=sv.get("wind"),
                gust_speed_ms=sv.get("gust"),
                wind_direction_deg=None,
                humidity_pct=sv.get("hum"),
                precipitation_1h_mm=sv.get("rain_rate"),
                source="station",
            )
    return None


def _build_sparkline_svg(
    readings: list,
    field: str,
    width: int = 200,
    height: int = 40,
) -> dict | None:
    """Baut SVG-Polyline-Koordinaten aus einer Zeitreihe von WeatherReadings.

    Gibt None zurück, wenn weniger als 2 Punkte vorhanden sind.
    Format wie abfluss_service.sparkline_data – verwendbar mit denselben SVG-Templates.
    """
    pairs = [
        (r.ts.timestamp(), getattr(r, field))
        for r in readings
        if getattr(r, field, None) is not None
    ]
    if len(pairs) < 2:
        return None
    ts_vals = [p[0] for p in pairs]
    y_vals  = [p[1] for p in pairs]
    ts_min, ts_max = min(ts_vals), max(ts_vals)
    y_min, y_max   = min(y_vals), max(y_vals)
    if ts_max == ts_min:
        return None
    y_range = (y_max - y_min) or 1.0
    pts = " ".join(
        f"{(t - ts_min) / (ts_max - ts_min) * width:.1f},"
        f"{height - (v - y_min) / y_range * (height - 4) - 2:.1f}"
        for t, v in pairs
    )
    return {
        "points": pts,
        "width":  width,
        "height": height,
        "min":    y_min,
        "max":    y_max,
        "latest": y_vals[-1],
    }


async def _render_weather_panel(
    request: Request,
    lat: float,
    lng: float,
    focus_label: str,
    extra_ctx: dict,
    abfluss_views: list[dict] | None = None,
    station_views: list[dict] | None = None,
) -> HTMLResponse:
    """Shared rendering logic for all weather panel endpoints."""
    nowcast, current, forecast, warnings = await asyncio.gather(
        weather_service.get_nowcast(lat, lng),
        weather_service.get_current(lat, lng),
        weather_service.get_forecast(lat, lng),
        weather_service.get_warnings(lat, lng),
        return_exceptions=True,
    )
    if isinstance(nowcast, Exception):
        logger.warning("Nowcast-Fehler: %s", nowcast)
        nowcast = None
    if isinstance(current, Exception):
        logger.warning("Current-Fehler: %s", current)
        current = None
    if isinstance(forecast, Exception):
        logger.warning("Forecast-Fehler: %s", forecast)
        forecast = None
    if isinstance(warnings, Exception):
        logger.warning("Warnings-Fehler: %s", warnings)
        warnings = []

    nowcast_bars = _build_nowcast_bars(nowcast) if nowcast else []
    peak_label = _peak_label(nowcast) if nowcast else None
    trend_de = _TREND_DE.get(nowcast.trend, nowcast.trend) if nowcast else None
    top_warning = warnings[0] if warnings else None
    warn_color = weather_service._WARN_LEVEL_COLORS.get(
        top_warning.level, "#6b7280"
    ) if top_warning else None
    station_current = _station_current_weather(station_views or [])
    scenarios = analyze_weather(station_current or current, forecast, nowcast, warnings)
    now_utc = datetime.now(UTC)
    active_warnings = [w for w in warnings if w.valid_from <= now_utc]

    ctx = {
        "no_location": False,
        "focus_label": focus_label,
        "nowcast": nowcast,
        "nowcast_bars": nowcast_bars,
        "peak_label": peak_label,
        "trend_de": trend_de,
        "current": current,
        "wind_dir_label": _wind_dir_label(current.wind_direction_deg if current else None),
        "forecast": forecast,
        "warnings": warnings,
        "warn_views": _build_warning_views(active_warnings),
        "top_warning": top_warning,
        "warn_color": warn_color,
        "scenarios": scenarios,
        "attribution": _build_attribution(current, forecast, nowcast, warnings, station_current),
        "abfluss_views": abfluss_views or [],
        "station_views": station_views or [],
    }
    ctx.update(extra_ctx)
    return templates.TemplateResponse(request, "incident_major/_weather_panel.html", ctx)


# ── GSL Wetter-Panel ──────────────────────────────────────────────────────────

@router.get(
    "/gsl/{lage_id}/wetter/panel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def gsl_wetter_panel(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """HTMX-Partial: Wetter-Panel für das GSL-Board."""
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

    if not _org_weather_enabled(lage.org_id, db):
        return HTMLResponse("")

    abfluss_views = await _build_abfluss_views(lage.org_id, db)
    station_views = _build_station_views(lage.org_id, db)

    focus = resolve_weather_focus(lage)
    lat: float | None = None
    lng: float | None = None
    focus_label = "Schwerpunkt"

    if focus:
        lat, lng = focus.lat, focus.lng
        focus_label = focus.label
    else:
        from app.models.master import FireDept as _FD
        org = db.get(_FD, lage.org_id)
        if org and org.fallback_lat and org.fallback_lng:
            lat, lng = org.fallback_lat, org.fallback_lng
            focus_label = org.city or org.name or "Org-Standort"

    if lat is None or lng is None:
        return templates.TemplateResponse(
            request,
            "incident_major/_weather_panel.html",
            {
                "no_location": True,
                "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
                "abfluss_views": abfluss_views,
                "station_views": station_views,
            },
        )

    return await _render_weather_panel(
        request, lat, lng, focus_label, {"lage_id": lage_id},
        abfluss_views=abfluss_views, station_views=station_views,
    )


# ── Focus-Koordinaten für Karten-JS ──────────────────────────────────────────

@router.get(
    "/gsl/{lage_id}/wetter/focus.json",
    include_in_schema=False,
)
async def gsl_wetter_focus(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """JSON: Schwerpunkt-Koordinaten für den Wetter-Karten-Layer."""
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

    if not _org_weather_enabled(lage.org_id, db):
        raise HTTPException(status_code=404)

    from app.models.master import FireDept as _FD
    focus = resolve_weather_focus(lage)
    lat = lng = None
    label = ""
    if focus:
        lat, lng = focus.lat, focus.lng
        label = focus.label
    else:
        org = db.get(_FD, lage.org_id)
        if org and org.fallback_lat and org.fallback_lng:
            lat, lng = org.fallback_lat, org.fallback_lng
            label = org.city or org.name or "Org-Standort"

    if lat is None or lng is None:
        return JSONResponse({"lat": None, "lng": None, "radius_km": settings.WEATHER_RADIUS_KM, "label": ""})

    return JSONResponse({
        "lat": lat,
        "lng": lng,
        "radius_km": settings.WEATHER_RADIUS_KM,
        "label": label,
    })


# ── Warnzonen als GeoJSON ─────────────────────────────────────────────────────

@router.get(
    "/gsl/{lage_id}/wetter/warnungen.geojson",
    include_in_schema=False,
)
async def gsl_wetter_warnungen_geojson(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """GeoJSON: aktive Warnungen als Punkt-Features (Zentrum = Schwerpunkt)."""
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

    if not _org_weather_enabled(lage.org_id, db):
        return JSONResponse({"type": "FeatureCollection", "features": []},
                            media_type="application/geo+json")

    from app.models.master import FireDept as _FD
    focus = resolve_weather_focus(lage)
    lat = lng = None
    if focus:
        lat, lng = focus.lat, focus.lng
    else:
        org = db.get(_FD, lage.org_id)
        if org and org.fallback_lat and org.fallback_lng:
            lat, lng = org.fallback_lat, org.fallback_lng

    if lat is None or lng is None:
        return JSONResponse({"type": "FeatureCollection", "features": []},
                            media_type="application/geo+json")

    warnings = await weather_service.get_warnings(lat, lng)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "level":       w.level,
                "event_type":  w.event_type,
                "text":        w.text[:120],
                "region":      w.region,
                "level_color": weather_service._WARN_LEVEL_COLORS.get(w.level, "#fbbf24"),
                "valid_from":  w.valid_from.isoformat(),
                "valid_to":    w.valid_to.isoformat(),
            },
        }
        for w in warnings
    ]
    return JSONResponse(
        {"type": "FeatureCollection", "features": features},
        media_type="application/geo+json",
    )


# ── Einzeleinsatz Wetter-Panel ────────────────────────────────────────────────

@router.get(
    "/einsatz/{incident_id}/wetter/panel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def einsatz_wetter_panel(
    request: Request,
    incident_id: int,
    db: Session = Depends(get_db),
):
    """HTMX-Partial: Wetter-Panel für den Einzeleinsatz-Board."""
    from app.models.incident import Incident as _Inc

    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401)

    incident = db.get(_Inc, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    if not can_access_incident(user, incident):
        raise HTTPException(status_code=403, detail="Kein Zugriff")

    org_id = incident.primary_org_id
    if not _org_weather_enabled(org_id, db):
        return HTMLResponse("")

    abfluss_views = await _build_abfluss_views(org_id, db)
    station_views = _build_station_views(org_id, db)

    lat: float | None = incident.lat
    lng: float | None = incident.lng
    focus_label = "Einsatzort"

    if (lat is None or lng is None) and org_id:
        from app.models.master import FireDept as _FD
        org = db.get(_FD, org_id)
        if org and org.fallback_lat and org.fallback_lng:
            lat, lng = org.fallback_lat, org.fallback_lng
            focus_label = org.city or org.name or "Org-Standort"

    if incident.address_street:
        parts = [p for p in [incident.address_street, incident.address_city] if p]
        if parts:
            focus_label = ", ".join(parts)

    if lat is None or lng is None:
        return templates.TemplateResponse(
            request,
            "incident_major/_weather_panel.html",
            {
                "no_location": True,
                "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
                "abfluss_views": abfluss_views,
                "station_views": station_views,
            },
        )

    return await _render_weather_panel(
        request, lat, lng, focus_label, {"incident_id": incident_id},
        abfluss_views=abfluss_views, station_views=station_views,
    )


# ── Globale Wetter-Seite (/wetter) ───────────────────────────────────────────

def _resolve_menu_standort(user, db: Session) -> tuple[float | None, float | None, str]:
    """Returns (lat, lng, label) for the global /wetter page.

    Uses the org's fallback_lat/lng. Cache-first: if nowcast is already
    cached for this location, the panel will skip the fetch.
    """
    if not user or not user.org_id:
        return None, None, "Kein Standort"
    from app.models.master import FireDept as _FD
    org = db.get(_FD, user.org_id)
    if not org or not org.fallback_lat or not org.fallback_lng:
        return None, None, "Kein Standort konfiguriert"
    label = org.city or org.name or "Org-Standort"
    return org.fallback_lat, org.fallback_lng, label


@router.get(
    "/wetter",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def wetter_index(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """Globale Wetter-Übersichtsseite — Org-Standort mit vollständigem Panel + Radar."""
    if not settings.WEATHER_ENABLED:
        raise HTTPException(status_code=404)

    user = request.state.user
    abfluss_views = await _build_abfluss_views(getattr(user, "org_id", None), db)
    station_views = _build_station_views(getattr(user, "org_id", None), db)
    lat, lng, focus_label = _resolve_menu_standort(user, db)

    if lat is None or lng is None:
        return templates.TemplateResponse(
            request,
            "weather/index.html",
            {
                "no_location": True,
                "lat": None,
                "lng": None,
                "focus_label": focus_label,
                "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
                "user": user,
                "scenarios": [],
                "abfluss_views": abfluss_views,
                "station_views": station_views,
            },
        )

    # Trigger cache warm-up in background (panel HTMX will use it)
    _ = has_cached(lat, lng)

    nowcast, current, forecast, warnings = await asyncio.gather(
        weather_service.get_nowcast(lat, lng),
        weather_service.get_current(lat, lng),
        weather_service.get_forecast(lat, lng),
        weather_service.get_warnings(lat, lng),
        return_exceptions=True,
    )
    for name, val in [("nowcast", nowcast), ("current", current),
                      ("forecast", forecast), ("warnings", warnings)]:
        if isinstance(val, Exception):
            logger.warning("Wetter-Seite %s-Fehler: %s", name, val)
    if isinstance(nowcast, Exception):   nowcast = None
    if isinstance(current, Exception):   current = None
    if isinstance(forecast, Exception):  forecast = None
    if isinstance(warnings, Exception):  warnings = []

    nowcast_bars = _build_nowcast_bars(nowcast) if nowcast else []
    peak_label = _peak_label(nowcast) if nowcast else None
    trend_de = _TREND_DE.get(nowcast.trend, nowcast.trend) if nowcast else None
    top_warning = warnings[0] if warnings else None
    warn_color = weather_service._WARN_LEVEL_COLORS.get(
        top_warning.level, "#6b7280"
    ) if top_warning else None
    station_current = _station_current_weather(station_views)
    scenarios = analyze_weather(station_current or current, forecast, nowcast, warnings)

    return templates.TemplateResponse(
        request,
        "weather/index.html",
        {
            "no_location": False,
            "lat": lat,
            "lng": lng,
            "focus_label": focus_label,
            "nowcast": nowcast,
            "nowcast_bars": nowcast_bars,
            "peak_label": peak_label,
            "trend_de": trend_de,
            "current": current,
            "wind_dir_label": _wind_dir_label(current.wind_direction_deg if current else None),
            "forecast": forecast,
            "warnings": warnings,
            "warn_views": _build_warning_views(warnings),
            "top_warning": top_warning,
            "warn_color": warn_color,
            "scenarios": scenarios,
            "attribution": _build_attribution(current, forecast, nowcast, warnings, station_current),
            "user": user,
            "windy_enabled": settings.WEATHER_WINDY_ENABLED,
            "abfluss_views": abfluss_views,
            "station_views": station_views,
        },
    )


@router.get(
    "/wetter/panel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def wetter_panel(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """HTMX-Partial: Wetter-Panel für /wetter-Seite (5-min auto-refresh)."""
    if not settings.WEATHER_ENABLED:
        return HTMLResponse("")

    user = request.state.user
    abfluss_views = await _build_abfluss_views(getattr(user, "org_id", None), db)
    station_views = _build_station_views(getattr(user, "org_id", None), db)
    lat, lng, focus_label = _resolve_menu_standort(user, db)

    if lat is None or lng is None:
        return templates.TemplateResponse(
            request,
            "incident_major/_weather_panel.html",
            {
                "no_location": True,
                "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
                "abfluss_views": abfluss_views,
                "station_views": station_views,
            },
        )

    return await _render_weather_panel(request, lat, lng, focus_label, {},
                                       abfluss_views=abfluss_views, station_views=station_views)


# ── 24-h-Sparkline (lazy HTMX) ───────────────────────────────────────────────

@router.get(
    "/wetter/station/{station_id}/sparkline",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def station_sparkline(
    request: Request,
    station_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    """HTMX-Partial: 24-h-Sparkline einer Wetterstation (lazy, nicht im 5-min-Pfad)."""
    user = request.state.user
    org_id = getattr(user, "org_id", None)
    if not org_id:
        return HTMLResponse("")

    from app.db_weather import get_weather_session, weather_db_enabled
    from app.models.weather import WeatherReading, WeatherStation

    station = (
        db.query(WeatherStation)
        .filter(WeatherStation.id == station_id, WeatherStation.org_id == org_id)
        .first()
    )
    if not station:
        return HTMLResponse("")

    if not weather_db_enabled():
        return HTMLResponse("")

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    session = get_weather_session()
    try:
        readings = (
            session.query(WeatherReading)
            .filter(
                WeatherReading.org_id == org_id,
                WeatherReading.station_id == station_id,
                WeatherReading.ts >= cutoff,
            )
            .order_by(WeatherReading.ts)
            .limit(300)
            .all()
        )
    finally:
        session.close()

    return templates.TemplateResponse(
        request,
        "weather/_station_sparkline.html",
        {
            "temp_svg": _build_sparkline_svg(readings, "temp_c"),
            "wind_svg": _build_sparkline_svg(readings, "wind_ms"),
        },
    )


# ── Oeffentliches Wetter-Infoscreen-Dashboard (Token-Auth, kein Login) ────────

@router.get(
    "/wetter/infoscreen/{token}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def weather_infoscreen(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    """Vollbild-Wetter-Dashboard fuer FullHD-Infoscreens. Zugriff nur mit gueltigem Token."""
    from app.core.security import hash_api_key
    from app.models.master import FireDept, OrgSettings

    token_hash = hash_api_key(token)
    # OrgSettings ist nicht TenantScoped – direkter Lookup ohne Tenant-Bypass noetig
    org_settings = (
        db.query(OrgSettings)
        .execution_options(include_all_tenants=True)
        .filter(OrgSettings.weather_dashboard_token_hash == token_hash)
        .first()
    )
    if not org_settings:
        raise HTTPException(status_code=401, detail="Ungueltiger oder gesperrter Dashboard-Token.")

    org = db.query(FireDept).filter(FireDept.id == org_settings.org_id).first()
    if not org:
        raise HTTPException(status_code=404)

    station_views = _build_station_views(org.id, db)
    abfluss_views = await _build_abfluss_views(org.id, db)

    # Koordinaten fuer externe Wetter-APIs: erst Station, dann Org-Fallback
    lat: float | None = None
    lng: float | None = None
    if station_views:
        from app.models.weather import WeatherStation
        for sv in station_views:
            st = db.get(WeatherStation, sv["id"])
            if st and st.lat is not None and st.lng is not None:
                lat, lng = st.lat, st.lng
                break
    if lat is None and org.fallback_lat and org.fallback_lng:
        lat, lng = org.fallback_lat, org.fallback_lng

    warnings: list = []
    current = None
    nowcast = None
    if lat is not None and lng is not None:
        results = await asyncio.gather(
            weather_service.get_warnings(lat, lng),
            weather_service.get_current(lat, lng),
            weather_service.get_nowcast(lat, lng),
            return_exceptions=True,
        )
        if not isinstance(results[0], Exception):
            warnings = results[0]
        if not isinstance(results[1], Exception):
            current = results[1]
        if not isinstance(results[2], Exception):
            nowcast = results[2]

    now_utc = datetime.now(UTC)
    active_warnings = [w for w in warnings if w.valid_from <= now_utc]
    station_current = _station_current_weather(station_views)
    base_url = (settings.PUBLIC_BASE_URL or settings.APP_BASE_URL).rstrip("/")

    return templates.TemplateResponse(
        request,
        "weather/infoscreen.html",
        {
            "org": org,
            "org_settings": org_settings,
            "station": station_views[0] if station_views else None,
            "station_views": station_views,
            "abfluss_views": abfluss_views,
            "current": current,
            "station_current": station_current,
            "warnings": active_warnings,
            "warn_views": _build_warning_views(active_warnings),
            "top_warning": active_warnings[0] if active_warnings else None,
            "nowcast": nowcast,
            "nowcast_bars": _build_nowcast_bars(nowcast) if nowcast else [],
            "attribution": _build_attribution(current, None, nowcast, active_warnings, station_current),
            "public_base_url": base_url,
            "map_lat": lat,
            "map_lng": lng,
        },
    )
