"""Teams-Alarmierung: öffentliche No-Login-Routen (Alarmübersicht, Kartenbild) sowie
der eingehende Bot-Framework-Webhook (`POST /api/v1/teams/messages`, folgt später).

Auth-Muster für die öffentlichen Routen: wie `lagekarte_api.py` — Query-/Pfad-Token wird
sha256-gehasht gegen `AlarmToken.token_hash` geprüft (siehe app/models/teams_bot.py).
Die Alarmübersicht zeigt bewusst NUR Alarm-Kerndaten (Stichwort, Adresse, Meldung, Karte)
— keine Mannschafts-/Personendaten, da der Link ohne Login erreichbar ist.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import can_access_incident
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.teams_bot import AlarmToken

logger = logging.getLogger("einsatzleiter.teams_bot")

router = APIRouter()


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _resolve_alarm_token(db: Session, plain: str) -> tuple[AlarmToken, Incident]:
    token_hash = _hash_token(plain)
    token = db.query(AlarmToken).filter(AlarmToken.token_hash == token_hash).first()
    if token is None or not token.is_active:
        raise HTTPException(status_code=404, detail="Link ungültig oder abgelaufen")
    incident = db.get(Incident, token.incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
    return token, incident


# ── Öffentliche Alarmübersicht (No-Login, z.B. via QR-Code/Teams-Link) ──────────

@router.get("/alarm/{token}", response_class=HTMLResponse)
def alarm_summary(token: str, request: Request, db: Session = Depends(get_db)):
    _tok, incident = _resolve_alarm_token(db, token)

    # Per SMS/Teams verschickter Link ist derselbe fuer alle Empfaenger (mit und
    # ohne Login). Ist der Aufrufer bereits eingeloggt und fuer diesen Einsatz
    # berechtigt, direkt auf die interne Einsatzinfo weiterleiten statt die
    # oeffentliche No-Login-Ansicht zu zeigen.
    user = getattr(request.state, "user", None)
    if user and can_access_incident(user, incident):
        return RedirectResponse(f"/einsatz/{incident.id}/info")

    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    return templates.TemplateResponse(request, "public/alarm_summary.html", {
        "incident": incident,
        "org": org,
        "token": token,
        "has_coords": incident.lat is not None and incident.lng is not None,
        "gmaps_url": (
            f"https://maps.google.com/?q={incident.lat},{incident.lng}"
            if incident.lat is not None and incident.lng is not None else None
        ),
        "map_png_url": f"/api/v1/teams/map/{token}.png",
    })


# ── Hydranten (No-Login, für die öffentliche Einsatzinfo-Karte) ─────────────────

@router.get("/alarm/{token}/hydranten.json")
async def alarm_hydranten(token: str, db: Session = Depends(get_db)):
    """Löschwasser-Entnahmestellen (OSM/OSMHydrant) um den Einsatzort. Bewusst nur
    öffentliche OSM-Daten — keine Objektdokumente/-kontakte (DSGVO, login-frei)."""
    from app.config import settings
    from app.models.master import OrgSettings
    from app.services.hydrant_service import fetch_osm_hydranten

    _tok, incident = _resolve_alarm_token(db, token)
    org_settings = db.query(OrgSettings).filter(
        OrgSettings.org_id == incident.primary_org_id
    ).first() if incident.primary_org_id else None
    enabled = settings.HYDRANT_ENABLED and (
        org_settings is None or org_settings.hydrant_layer_enabled
    )
    if not enabled or incident.lat is None or incident.lng is None:
        return {"hydranten": [], "stand": None}
    return {"hydranten": await fetch_osm_hydranten(incident.lat, incident.lng), "stand": None}


# ── Kartenbild (No-Login, wird von Teams-Servern per URL geladen) ───────────────

@router.get("/api/v1/teams/map/{token}.png")
async def alarm_map_png(token: str, db: Session = Depends(get_db)):
    _tok, incident = _resolve_alarm_token(db, token)
    if incident.lat is None or incident.lng is None:
        raise HTTPException(status_code=404, detail="Keine Koordinaten für diesen Einsatz")

    import asyncio

    from app.services.staticmap_service import render_incident_map_png
    try:
        png = await asyncio.to_thread(render_incident_map_png, incident.lat, incident.lng)
    except Exception:
        logger.exception("Kartenbild konnte nicht gerendert werden (Einsatz %s)", incident.id)
        raise HTTPException(status_code=502, detail="Kartenbild derzeit nicht verfügbar")

    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ── Schadensfoto (No-Login, wird von Teams-Servern per URL geladen) ─────────────

@router.get("/api/v1/teams/fahrt-foto/{media_id}.jpg")
async def fahrt_foto_jpg(media_id: int, sig: str = "", db: Session = Depends(get_db)):
    """Liefert ein Schadensfoto für die Teams-Adaptive-Card (siehe schaden_service.py::
    _foto_urls). Auth-Muster wie alarm_map_png() oben: signierter Query-Token statt
    Login (Teams' Cloud hat keine Session/Cookie)."""
    from app.core.security import unsign_fahrt_foto_token
    from app.models.fahrtenbuch import FahrtMedia
    from app.services.media_service import absolute_fahrt_media_path

    data = unsign_fahrt_foto_token(sig)
    if data is None:
        raise HTTPException(status_code=403, detail="Ungültige Signatur")
    token_media_id, org_id = data
    if token_media_id != media_id:
        raise HTTPException(status_code=403, detail="Ungültige Signatur")

    media = (
        db.query(FahrtMedia)
        .filter(FahrtMedia.id == media_id, FahrtMedia.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if media is None:
        raise HTTPException(status_code=404, detail="Foto nicht gefunden")

    path = absolute_fahrt_media_path(media)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Foto nicht gefunden")

    from fastapi.responses import FileResponse
    return FileResponse(path, media_type=media.mime_type, headers={"Cache-Control": "no-store"})
