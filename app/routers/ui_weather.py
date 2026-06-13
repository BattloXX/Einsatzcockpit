"""UI-Router: Wetter-Panel (HTMX-Partials für GSL-Board und Einzeleinsatz)."""
import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import MajorIncident
from app.services import weather_service
from app.services.weather_focus import resolve_weather_focus
from app.services.weather_service import analyze_weather

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


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org(user, org_id: int) -> None:
    if not same_org_or_system_admin(user, org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff")


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


def _peak_label(now: "weather_service.NowcastResult") -> str | None:
    """Returns human-readable time to peak, or None if no precipitation."""
    if now.peak_mm < 0.05 or now.peak_at is None:
        return None
    delta_min = int((now.peak_at - datetime.now(UTC)).total_seconds() / 60)
    delta_min = max(delta_min, 0)
    if delta_min < 5:
        return "jetzt"
    return f"in ca. {delta_min} min"


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
    if not settings.WEATHER_ENABLED:
        return HTMLResponse("")

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

    # Resolve reference coordinates
    focus = resolve_weather_focus(lage)
    lat: float | None = None
    lng: float | None = None
    focus_label = "Schwerpunkt"

    if focus:
        lat, lng = focus.lat, focus.lng
        focus_label = focus.label
    else:
        # Fallback: org location
        org = lage.org_id and db.get(
            __import__("app.models.master", fromlist=["FireDept"]).FireDept,
            lage.org_id,
        )
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
            },
        )

    # Parallel fetch: nowcast, current conditions, forecast, warnings
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

    # Pre-compute display data
    nowcast_bars = _build_nowcast_bars(nowcast) if nowcast else []
    peak_label = _peak_label(nowcast) if nowcast else None
    trend_de = _TREND_DE.get(nowcast.trend, nowcast.trend) if nowcast else None
    top_warning = warnings[0] if warnings else None
    warn_color = weather_service._WARN_LEVEL_COLORS.get(
        top_warning.level, "#6b7280"
    ) if top_warning else None
    scenarios = analyze_weather(current, forecast, nowcast)

    return templates.TemplateResponse(
        request,
        "incident_major/_weather_panel.html",
        {
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
            "top_warning": top_warning,
            "warn_color": warn_color,
            "scenarios": scenarios,
            "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
            "lage_id": lage_id,
        },
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
    if not settings.WEATHER_ENABLED:
        raise HTTPException(status_code=404)

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

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
    if not settings.WEATHER_ENABLED:
        return JSONResponse({"type": "FeatureCollection", "features": []},
                            media_type="application/geo+json")

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage.org_id)

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
