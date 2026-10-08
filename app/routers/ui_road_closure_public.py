"""Oeffentlicher Infoscreen fuer aktuelle Straßensperren."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter as _limiter
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept
from app.routers.ui_infoscreen_alarm import _token_org
from app.routers.ui_road_closure import _feature, _infoscreen_payload, _org_names, _status_data
from app.services.road_closure_flags import strassensperren_effective_enabled
from app.services.road_closure_public_service import public_closure_dict, public_closures_q
from app.services.road_closure_token_service import TokenUngueltig, resolve, resolve_detail
from app.services.staticmap_service import render_road_closure_map_png

logger = logging.getLogger("einsatzleiter.road_closure_public")

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


def _token_screen_data(db: Session, raw: str, art: str) -> tuple[FireDept, dict, dict]:
    """Status-/Infoscreen-Token prüfen; liefert ausschließlich öffentliche Sperren der Token-Org."""
    access, org = resolve(db, raw, art)
    try:
        permissions = json.loads(access.berechtigungen_json or "{}")
    except json.JSONDecodeError:
        permissions = {}
    closures = public_closures_q(db, org.id, include_planned=permissions.get("zeige_geplante", True)).all()
    data = _infoscreen_payload(
        db, org, closures, scope="public", refresh_sec=permissions.get("refresh_sec", 60),
        rotation_sec=permissions.get("rotation_sec", 0), permissions=permissions,
    )
    return org, data, permissions


def _legacy_screen_data(db: Session, token: str) -> tuple[FireDept, dict]:
    """Alarmmonitor-Token (intern): alle aktuellen Sperren inkl. freigegebener Nachbarsperren."""
    org, (closures, _active, _planned) = _screen_data(db, token)
    return org, _infoscreen_payload(db, org, closures, scope="all", owner_names=_org_names(db, closures))


def _invalid_html():
    return _public_headers(HTMLResponse(_UNGUELTIG_HTML, status_code=404))


def _invalid_json():
    return _public_headers(JSONResponse({"detail": "Nicht gefunden"}, status_code=404))


def _screen(request: Request, org: FireDept, data: dict, *, modus: str, daten_url: str, zeige_karte: bool):
    return _public_headers(templates.TemplateResponse(
        request,
        "road_closure/infoscreen.html",
        {
            # Die |local*-Filter lesen user.org; ohne Login muss trotzdem die Org-Zeitzone gelten.
            "user": SimpleNamespace(org=org), "org": org, "modus": modus, "external": True, "daten": data,
            "daten_url": daten_url, "zeige_karte": zeige_karte, "filter_query": "",
        },
    ))


@router.get("/oeffentlich/strassensperren/{token}", response_class=HTMLResponse)
@(_limiter.limit(settings.STRASSENSPERREN_PUBLIC_RATELIMIT) if _limiter else lambda f: f)
def public_status(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        org, data, permissions = _token_screen_data(db, token, "status")
    except TokenUngueltig:
        return _invalid_html()
    db.commit()  # last_used_at
    return _screen(
        request, org, data, modus="status", daten_url=f"/oeffentlich/strassensperren/{token}/daten",
        zeige_karte=permissions.get("zeige_karte", True),
    )


@router.get("/oeffentlich/strassensperren/{token}/daten")
@(_limiter.limit(settings.STRASSENSPERREN_PUBLIC_RATELIMIT) if _limiter else lambda f: f)
def public_status_daten(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        _org, data, _permissions = _token_screen_data(db, token, "status")
    except TokenUngueltig:
        return _invalid_json()
    db.commit()
    return _public_headers(JSONResponse(data))


@router.get("/infoscreen/strassensperren/{token}", response_class=HTMLResponse)
def infoscreen(token: str, request: Request, db: Session = Depends(get_db)):
    daten_url = f"/infoscreen/strassensperren/{token}/daten"
    if token.startswith("rci_"):
        try:
            org, data, permissions = _token_screen_data(db, token, "infoscreen")
        except TokenUngueltig:
            return _invalid_html()
        db.commit()
        return _screen(
            request, org, data, modus="infoscreen", daten_url=daten_url,
            zeige_karte=permissions.get("zeige_karte", True),
        )
    try:
        org, data = _legacy_screen_data(db, token)
    except HTTPException as exc:
        # Dieser Token-Endpunkt ist bewusst anonym; ein ungültiger Token darf nicht
        # in den allgemeinen Login-Redirect der HTML-Fehlerbehandlung geraten.
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return _screen(request, org, data, modus="infoscreen", daten_url=daten_url, zeige_karte=True)


@router.get("/infoscreen/strassensperren/{token}/daten")
def infoscreen_daten(token: str, db: Session = Depends(get_db)):
    if token.startswith("rci_"):
        try:
            _org, data, _permissions = _token_screen_data(db, token, "infoscreen")
        except TokenUngueltig:
            return _invalid_json()
        db.commit()
        return _public_headers(JSONResponse(data))
    try:
        _org, data = _legacy_screen_data(db, token)
    except HTTPException as exc:
        return _public_headers(JSONResponse({"detail": exc.detail}, status_code=exc.status_code))
    return _public_headers(JSONResponse(data))


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


@router.get("/oeffentlich/strassensperre/{token}/karte.png")
@(_limiter.limit(settings.STRASSENSPERREN_PUBLIC_RATELIMIT) if _limiter else lambda f: f)
async def public_map_png(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        _access, org, closure, _beendet = resolve_detail(db, token)
        geometry = public_closure_dict(closure, org).get("geometry")
        if not geometry:
            raise TokenUngueltig()
        png = await asyncio.to_thread(render_road_closure_map_png, geometry)
    except TokenUngueltig:
        return Response(status_code=404)
    except Exception:
        logger.warning("Could not render public road closure map", exc_info=True)
        return Response(status_code=404)
    return Response(png, media_type="image/png", headers={
        "Cache-Control": "public, max-age=300", "X-Robots-Tag": "noindex",
    })
