"""UI-Router: Wetter-Panel (HTMX-Partials für GSL-Board, Einzeleinsatz und /wetter-Seite)."""
import asyncio
import logging
from datetime import UTC, datetime

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


def _peak_label(now: "weather_service.NowcastResult") -> str | None:
    """Returns human-readable time to peak, or None if no precipitation."""
    if now.peak_mm < 0.05 or now.peak_at is None:
        return None
    delta_min = int((now.peak_at - datetime.now(UTC)).total_seconds() / 60)
    delta_min = max(delta_min, 0)
    if delta_min < 5:
        return "jetzt"
    return f"in ca. {delta_min} min"


async def _render_weather_panel(
    request: Request,
    lat: float,
    lng: float,
    focus_label: str,
    extra_ctx: dict,
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
    scenarios = analyze_weather(current, forecast, nowcast)

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
        "top_warning": top_warning,
        "warn_color": warn_color,
        "scenarios": scenarios,
        "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
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
            },
        )

    return await _render_weather_panel(
        request, lat, lng, focus_label, {"lage_id": lage_id}
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
            },
        )

    return await _render_weather_panel(
        request, lat, lng, focus_label, {"incident_id": incident_id}
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
    scenarios = analyze_weather(current, forecast, nowcast)

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
            "top_warning": top_warning,
            "warn_color": warn_color,
            "scenarios": scenarios,
            "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
            "user": user,
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
    lat, lng, focus_label = _resolve_menu_standort(user, db)

    if lat is None or lng is None:
        return templates.TemplateResponse(
            request,
            "incident_major/_weather_panel.html",
            {
                "no_location": True,
                "attribution": weather_service.GEOSPHERE_ATTRIBUTION,
            },
        )

    return await _render_weather_panel(request, lat, lng, focus_label, {})
