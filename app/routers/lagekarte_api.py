"""Öffentlicher GeoJSON-Endpoint für lagekarte.info.

Authentifizierung via Query-Token ?token=<plain> (LagekarteToken, sha256-gehasht).
CORS für https://www.lagekarte.info ist in main.py konfiguriert.
Rate-Limiting via slowapi (LAGEKARTE_GEOJSON_RATELIMIT aus config).
"""
import hashlib
import logging
from datetime import UTC, datetime
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.incident import Incident, IncidentOrg
from app.models.lagekarte import LagekarteToken
from app.services.lagekarte import vehicle_features

logger = logging.getLogger("einsatzleiter.lagekarte_api")

router = APIRouter(prefix="/api/lagekarte", tags=["lagekarte"])

_CORS_HEADERS = {
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


# ── Auth-Helpers ──────────────────────────────────────────────────────────────

def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _resolve_lagekarte_token(db: Session, plain: str) -> LagekarteToken:
    token_hash = _hash_token(plain)
    token = db.query(LagekarteToken).filter(LagekarteToken.token_hash == token_hash).first()
    if token is None or not token.is_active:
        raise HTTPException(status_code=401, detail="Ungültiger oder gesperrter Lagekarte-Token")
    return token


def _scoped_incident_or_404(db: Session, einsatz_id: int, lk_token: LagekarteToken) -> Incident:
    """Lädt den Einsatz und prüft Org-Zugehörigkeit des Tokens.

    Wenn das Token auf einen bestimmten Einsatz beschränkt ist (einsatz_id gesetzt),
    wird zusätzlich geprüft, ob der angefragte Einsatz passt.
    """
    incident = db.get(Incident, einsatz_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    # Token-Einsatz-Beschränkung prüfen
    if lk_token.einsatz_id is not None and lk_token.einsatz_id != einsatz_id:
        raise HTTPException(status_code=404, detail="Token gilt nicht für diesen Einsatz")

    # Org-Scoping: primäre Org oder kollaborierende Org
    collab_ids = {io.org_id for io in (incident.collaborating_orgs or [])}
    if incident.primary_org_id != lk_token.org_id and lk_token.org_id not in collab_ids:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    return incident


# ── GeoJSON-Endpoint ──────────────────────────────────────────────────────────

@router.get("/einsatz/{einsatz_id}/fahrzeuge.geojson")
async def vehicles_geojson(
    request: Request,
    einsatz_id: int,
    token: str = Query(..., min_length=10),
    db: Session = Depends(get_db),
) -> Response:
    """GeoJSON FeatureCollection aller aktiven Fahrzeuge des Einsatzes.

    Für lagekarte.info unter *Daten importieren → URL* mit Auto-Reload eintragen.
    """
    lk_token = _resolve_lagekarte_token(db, token)
    incident = _scoped_incident_or_404(db, einsatz_id, lk_token)

    features = vehicle_features(db, incident)
    feature_collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    lk_token.last_used_at = datetime.now(UTC)
    db.commit()

    return JSONResponse(
        content=feature_collection,
        headers=_CORS_HEADERS,
        media_type="application/geo+json",
    )


@router.get("/einsatz/{einsatz_id}/fahrzeuge.kml")
async def vehicles_kml(
    request: Request,
    einsatz_id: int,
    token: str = Query(..., min_length=10),
    db: Session = Depends(get_db),
) -> Response:
    """KML-Export aller aktiven Fahrzeuge des Einsatzes (optional)."""
    lk_token = _resolve_lagekarte_token(db, token)
    incident = _scoped_incident_or_404(db, einsatz_id, lk_token)

    features = vehicle_features(db, incident)

    kml = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = SubElement(kml, "Document")
    name_el = SubElement(doc, "name")
    name_el.text = f"Einsatz {incident.id} Fahrzeuge"

    for feat in features:
        props = feat["properties"]
        coords = feat["geometry"]["coordinates"]  # [lng, lat]
        pm = SubElement(doc, "Placemark")
        n = SubElement(pm, "name")
        n.text = props.get("name", "")
        desc = SubElement(pm, "description")
        desc.text = f"{props.get('typ', '')} | {props.get('status', '')} | {props.get('info', '')}"
        pt = SubElement(pm, "Point")
        c = SubElement(pt, "coordinates")
        c.text = f"{coords[0]},{coords[1]},0"

    lk_token.last_used_at = datetime.now(UTC)
    db.commit()

    kml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>' + tostring(kml, encoding="unicode").encode()
    return Response(
        content=kml_bytes,
        media_type="application/vnd.google-earth.kml+xml",
        headers=_CORS_HEADERS,
    )
