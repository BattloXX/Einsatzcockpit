"""Oeffentlicher Infoscreen fuer aktuelle Straßensperren."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.templating import templates
from app.db import get_db
from app.routers.ui_infoscreen_alarm import _token_org
from app.routers.ui_road_closure import _color, _feature, _status_data
from app.services import road_closure_service
from app.services.road_closure_flags import strassensperren_effective_enabled

router = APIRouter(tags=["strassensperren-infoscreen"])


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
    return templates.TemplateResponse(
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
    )


@router.get("/infoscreen/strassensperren/{token}/karte.json")
def karte(token: str, db: Session = Depends(get_db)):
    try:
        _org, (closures, _active, _planned) = _screen_data(db, token)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    features = [feature for closure in closures if (feature := _feature(closure, public=True)) is not None]
    return JSONResponse({"type": "FeatureCollection", "features": features})
