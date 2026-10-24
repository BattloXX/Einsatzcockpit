"""Berechtigte Zielhinweise für die bestehende Leaflet-Einheitenkarte."""
from __future__ import annotations

import logging
import math

from sqlalchemy.orm import Session

from app.models.major_incident import IncidentSite

logger = logging.getLogger(__name__)
NAVIGATION_HINWEIS = "Externe Navigation berücksichtigt ECP-Straßensperren nicht automatisch. Zufahrt prüfen."


def hat_zielkoordinaten(site: IncidentSite) -> bool:
    return (site.lat is not None and site.lng is not None and math.isfinite(site.lat) and math.isfinite(site.lng)
            and -90 <= site.lat <= 90 and -180 <= site.lng <= 180)


def ziel_sperren(db: Session, org_id: int, site: IncidentSite) -> dict:
    """Gleicher 500-m-Zielbereich, Freigaben und Aktivitätsprüfung wie bisher im Detail."""
    result: dict = {"strassensperren": [], "strassensperren_status": "nicht_verfuegbar",
                    "navigation_hinweis": NAVIGATION_HINWEIS}
    if site.lat is None or site.lng is None or not hat_zielkoordinaten(site):
        result["strassensperren_status"] = "keine_koordinaten"
        return result
    try:
        from app.services.road_closure_flags import strassensperren_effective_enabled
        from app.services.road_closure_geo_service import validate_geometry
        from app.services.road_closure_incident_service import relevant_closures

        if not strassensperren_effective_enabled(org_id, db):
            result["strassensperren_status"] = "inaktiv"
            return result
        rows, _ = relevant_closures(db, org_id, None, (site.lat, site.lng))
        for row in rows:
            try:
                geometry = validate_geometry(row.geometry_geojson) if row.geometry_geojson else None
            except (ValueError, TypeError):
                geometry = None  # Der bekannte Warntext bleibt auch ohne darstellbare Geometrie erhalten.
            result["strassensperren"].append({
                "id": row.id, "title": row.title, "restriction_label": row.restriction_label,
                "geometry": geometry,
            })
        result["strassensperren_status"] = "verfuegbar"
    except Exception:
        logger.exception("Sperren am Einheitenziel konnten nicht geladen werden", extra={"site_id": site.id})
    return result
