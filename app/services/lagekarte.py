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


def _live_position(
    db: "Session", vehicle_master_id: int, min_ts: "datetime | None" = None
) -> "tuple[float, float] | None":
    """Gibt die zuletzt gemeldete GPS-Position zurück, wenn sie frisch genug ist.

    Frisch = jünger als 5 Minuten UND jünger als `min_ts` (i.d.R. Alarmzeit des Einsatzes).
    Ältere Positionen werden ignoriert, damit veraltete Standortdaten nicht auftauchen.
    """
    from datetime import UTC, datetime, timedelta
    from app.models.user import DeviceToken
    freshness = datetime.now(UTC) - timedelta(minutes=5)
    # Daten müssen sowohl innerhalb der letzten 5 min als auch nach der Alarmzeit liegen
    threshold = max(freshness, min_ts) if min_ts else freshness
    # min_ts kann naive sein (DB ohne Timezone), normalisieren
    if threshold.tzinfo is None:
        threshold = threshold.replace(tzinfo=UTC)
    dt = (
        db.query(DeviceToken)
        .filter(
            DeviceToken.vehicle_master_id == vehicle_master_id,
            DeviceToken.revoked_at.is_(None),
            DeviceToken.last_lat.isnot(None),
            DeviceToken.last_lng.isnot(None),
            DeviceToken.last_location_at >= threshold,
        )
        .order_by(DeviceToken.last_location_at.desc())
        .first()
    )
    if dt and dt.last_lat is not None and dt.last_lng is not None:
        return dt.last_lat, dt.last_lng
    return None


def vehicle_features(db: "Session", incident: "Incident") -> list[dict]:
    """Baut GeoJSON-Features nur für Fahrzeuge mit live GPS-Position.

    Nur Fahrzeuge, deren Gerät nach der Alarmzeit des Einsatzes eine Position
    übermittelt hat, erscheinen im Feed. Fahrzeuge ohne aktive Geräteposition
    werden nicht aufgenommen — kein Fallback auf Einsatz-Koordinaten.
    """
    from datetime import UTC
    active_vehicles = [v for v in incident.vehicles if v.removed_at is None]
    features = []

    # Alarmzeit als Mindest-Zeitstempel: veraltete Pre-Alarm-Positionen ausschließen
    alarm_ts = incident.started_at
    if alarm_ts is not None and alarm_ts.tzinfo is None:
        alarm_ts = alarm_ts.replace(tzinfo=UTC)

    for iv in active_vehicles:
        vm = iv.vehicle_master
        live = _live_position(db, vm.id, min_ts=alarm_ts) if vm else None
        if not live:
            continue  # kein Fallback: Fahrzeug nur mit echter GPS-Position anzeigen
        lat, lng = live

        open_tasks = iv.open_task_count
        info = f"{open_tasks} offene Aufgabe{'n' if open_tasks != 1 else ''}" if open_tasks else ""

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lng, lat],  # GeoJSON: [lng, lat]
            },
            "properties": {
                "name": vm.display_label if vm else "",
                "typ": (vm.type or vm.name) if vm else "",
                "status": iv.unit_status,
                "info": info,
                "einsatz_id": incident.id,
                "fahrzeug_id": iv.id,
            },
        }
        features.append(feature)

    return features
