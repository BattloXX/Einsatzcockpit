"""Geodienste: Point-in-Polygon (shapely), automatische Abschnittszuweisung."""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger("einsatzleiter.geo")


def _shapely_available() -> bool:
    try:
        import shapely  # noqa: F401
        return True
    except ImportError:
        return False


def section_for_point(
    db: Session,
    incident_id: int,
    lat: float,
    lon: float,
):
    """
    Findet den spezifischsten Abschnitt für einen Punkt (kleinste Fläche bei Überlappung).
    Gibt Sector oder None zurück. Fällt ohne shapely auf None zurück.
    """
    from app.models.major_incident import Sector

    if not _shapely_available():
        logger.warning("shapely nicht installiert — automatische Abschnittszuweisung deaktiviert")
        return None

    from shapely.geometry import Point, shape

    sectors = (
        db.query(Sector)
        .filter(
            Sector.major_incident_id == incident_id,
            Sector.geometry.isnot(None),
        )
        .all()
    )

    point = Point(lon, lat)  # Leaflet: lat/lng → shapely: lng/lat (x/y)
    candidates: list[tuple[float, Sector]] = []

    for sector in sectors:
        try:
            geom = shape(json.loads(sector.geometry))
            if geom.contains(point):
                candidates.append((geom.area, sector))
        except Exception:
            continue

    if not candidates:
        return None

    # Kleinste Fläche = spezifischster Abschnitt
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def auto_assign_section(db: Session, site, force: bool = False) -> bool:
    """
    Weist der Einsatzstelle automatisch einen Abschnitt zu (nur wenn mode != 'manual').
    Mit force=True wird die Manual-Sperre ignoriert (für Geometrie-Änderungen).
    Gibt True zurück wenn eine Änderung stattgefunden hat.
    """
    if not force and site.section_assigned_mode == "manual":
        return False
    if site.lat is None or site.lng is None:
        return False

    sector = section_for_point(db, site.major_incident_id, site.lat, site.lng)
    new_sector_id = sector.id if sector else None

    if site.sector_id == new_sector_id:
        return False

    site.sector_id = new_sector_id
    return True


def bulk_reassign_section(
    db: Session,
    incident_id: int,
    changed_sector_id: int | None = None,
    include_manual: bool = False,
) -> int:
    """
    Neu-Zuweisung aller Einsatzstellen eines Vorgangs nach Polygon-Änderung.
    include_manual=True ignoriert die Manual-Sperre (für Geometrie-Änderungen).
    Gibt Anzahl geänderter Stellen zurück.
    """
    from app.models.major_incident import IncidentSite

    q = db.query(IncidentSite).filter(IncidentSite.major_incident_id == incident_id)
    if not include_manual:
        q = q.filter(IncidentSite.section_assigned_mode != "manual")
    sites = q.all()

    changed = 0
    for site in sites:
        if auto_assign_section(db, site, force=include_manual):
            changed += 1

    return changed


def validate_geojson_polygon(data: dict) -> str | None:
    """
    Validiert ein GeoJSON-Polygon. Gibt None zurück wenn OK, sonst Fehlermeldung.
    """
    try:
        if data.get("type") != "Polygon":
            return "Nur Polygon-Geometrien erlaubt"
        coords = data.get("coordinates", [])
        if not coords or not isinstance(coords[0], list):
            return "Ungültige Koordinaten"
        ring = coords[0]
        if len(ring) < 4:
            return "Polygon benötigt mindestens 4 Punkte"
        if len(ring) > 500:
            return "Polygon hat zu viele Stützpunkte (max. 500)"
        # Erstes und letztes Punkt müssen identisch sein (geschlossener Ring)
        if ring[0] != ring[-1]:
            return "Polygon-Ring ist nicht geschlossen"
        return None
    except Exception as e:
        return f"Ungültiges GeoJSON: {e}"
