"""Oeffentlicher Infoscreen fuer aktuelle Straßensperren."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter as _limiter
from app.core.templating import templates
from app.db import get_db
from app.routers.ui_infoscreen_alarm import _token_org
from app.routers.ui_road_closure import _color, _feature, _status_data
from app.services import road_closure_service
from app.services.road_closure_flags import strassensperren_effective_enabled
from app.services.road_closure_public_service import public_closure_dict
from app.services.road_closure_token_service import TokenUngueltig, resolve_detail

router = APIRouter(tags=["strassensperren-infoscreen"])


_UNGUELTIG_HTML = (
    '<!doctype html><html lang="de"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
    "<title>Link ungültig</title></head><body style=\"font-family:system-ui;padding:2rem\">"
    "<h1>Link ungültig oder abgelaufen</h1><p>Diese Straßensperre ist nicht (mehr) öffentlich verfügbar.</p>"
    '<p><a href="/login?next=/strassensperren">Zur Anmeldung</a></p></body></html>'
)


def _public_headers(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def _screen_data(db: Session, token: str):
    """Prueft den Alarm-Infoscreen-Token und liefert ausschliesslich dessen Org-Daten."""
    _token, org = _token_org(db, token)
    if not strassensperren_effective_enabled(org.id, db):
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return org, _status_data(db, org.id)


@router.get("/infoscreen/strassensperren/{token}", response_class=HTMLResponse)
def infoscreen(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        org, (closures, active, planned) = _screen_data(db, token)
    except HTTPException as exc:
        # Dieser Token-Endpunkt ist bewusst anonym; ein ungültiger Token darf nicht
        # in den allgemeinen Login-Redirect der HTML-Fehlerbehandlung geraten.
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return _public_headers(templates.TemplateResponse(
        request,
        "road_closure/status.html",
        {
            # Die lokalen Datumsfilter lesen die Org aus user.org; der Infoscreen
            # hat keinen eingeloggten User, aber muss dennoch die Org-Zeitzone nutzen.
            "user": SimpleNamespace(org=org),
            "org": org,
            "closures": closures,
            "active_count": active,
            "planned_count": planned,
            "color": _color,
            "closure_status": road_closure_service.compute_status,
            "public": True,
            "geojson_url": f"/infoscreen/strassensperren/{token}/karte.json",
        },
    ))


@router.get("/infoscreen/strassensperren/{token}/karte.json")
def karte(token: str, db: Session = Depends(get_db)):
    try:
        _org, (closures, _active, _planned) = _screen_data(db, token)
    except HTTPException as exc:
        return _public_headers(JSONResponse({"detail": exc.detail}, status_code=exc.status_code))
    features = [feature for closure in closures if (feature := _feature(closure, public=True)) is not None]
    return _public_headers(JSONResponse({"type": "FeatureCollection", "features": features}))


@router.get("/oeffentlich/strassensperre/{token}", response_class=HTMLResponse)
@(_limiter.limit(settings.STRASSENSPERREN_PUBLIC_RATELIMIT) if _limiter else lambda f: f)
def public_detail(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        _access, org, closure, beendet = resolve_detail(db, token)
    except TokenUngueltig:
        return _public_headers(HTMLResponse(_UNGUELTIG_HTML, status_code=404))
    db.commit()  # last_used_at
    data = public_closure_dict(closure, org)
    response = templates.TemplateResponse(
        request, "road_closure/public_detail.html",
        {
            "data": data, "org_name": org.name, "org_logo": getattr(org, "logo_path", None),
            "token": token, "beendet": beendet,
        },
    )
    return _public_headers(response)


@router.get("/oeffentlich/strassensperre/{token}/geometrie.json")
@(_limiter.limit(settings.STRASSENSPERREN_PUBLIC_RATELIMIT) if _limiter else lambda f: f)
def public_geometry(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        _access, org, closure, _beendet = resolve_detail(db, token)
    except TokenUngueltig:
        return _public_headers(JSONResponse({"detail": "Nicht gefunden"}, status_code=404))
    data = public_closure_dict(closure, org)
    feature = {"type": "Feature", "geometry": data.pop("geometry"), "properties": data}
    payload = {"type": "FeatureCollection", "features": [feature] if feature["geometry"] else []}
    return _public_headers(JSONResponse(payload))
