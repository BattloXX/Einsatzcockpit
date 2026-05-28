"""Lagekarte.info Hilfsfunktionen: URL-Erzeugung, GeoJSON-Feature-Bau, Koordinaten-Jitter."""
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.incident import Incident

# Wolfurt als letzter Fallback (wird nur verwendet, wenn weder Einsatz- noch Org-Koordinaten gesetzt)
FALLBACK_LAT = 47.4664
FALLBACK_LNG = 9.7416

# Deterministischer Streuradius für Fahrzeuge ohne eigene Position (~15 m)
_JITTER_RADIUS_DEG = 0.000135


def build_einsatz_url(lat: float, lng: float) -> str:
    return f"https://www.lagekarte.info/?einsatz={lat},{lng}"


def resolve_lagekarte_url(incident: "Incident") -> str | None:
    """Gibt die URL zurück, die im Lagekarte.info-Button verwendet wird.

    Priorität: gespeicherter SHASH/beliebiger Link > generierter Einsatz-Link > None.
    """
    if incident.lagekarte_shash_url:
        return incident.lagekarte_shash_url
    if incident.lat is not None and incident.lng is not None:
        return build_einsatz_url(incident.lat, incident.lng)
    return None


def scatter_coords(base_lat: float, base_lng: float, index: int, count: int) -> tuple[float, float]:
    """Deterministisches Kreisstreuung für Fahrzeuge ohne eigene Position.

    Alle Fahrzeuge werden gleichmäßig auf einem kleinen Kreis um den
    Einsatz-Mittelpunkt verteilt. Bei count=1 liegt der Punkt direkt auf dem
    Mittelpunkt (kein Jitter nötig). Index ist 0-basiert.
    """
    if count <= 1:
        return base_lat, base_lng
    angle = (2 * math.pi * index) / count
    lat = base_lat + _JITTER_RADIUS_DEG * math.cos(angle)
    lng = base_lng + _JITTER_RADIUS_DEG * math.sin(angle) / math.cos(math.radians(base_lat))
    return lat, lng


def vehicle_features(db: "Session", incident: "Incident") -> list[dict]:
    """Baut GeoJSON-Features für alle aktiven Fahrzeuge eines Einsatzes.

    Koordinaten: Einsatz lat/lng mit deterministischem Jitter je Fahrzeug.
    Wenn keine Einsatz-Koordinaten → leere Liste (kein 404, damit
    lagekarte.info-Polling geräuschlos bleibt).
    """
    if incident.lat is None or incident.lng is None:
        return []

    active_vehicles = [v for v in incident.vehicles if v.removed_at is None]
    count = len(active_vehicles)
    features = []

    for idx, iv in enumerate(active_vehicles):
        lat, lng = scatter_coords(incident.lat, incident.lng, idx, count)
        vm = iv.vehicle_master

        open_tasks = iv.open_task_count
        info = f"{open_tasks} offene Aufgabe{'n' if open_tasks != 1 else ''}" if open_tasks else ""

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lng, lat],  # GeoJSON: [lng, lat]
            },
            "properties": {
                "name": vm.code if vm else "",
                "typ": (vm.type or vm.name) if vm else "",
                "status": iv.unit_status,
                "info": info,
                "einsatz_id": incident.id,
                "fahrzeug_id": iv.id,
            },
        }
        features.append(feature)

    return features
