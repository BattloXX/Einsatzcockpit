"""Ausbreitungs-Fahne (Phase 1: tabellenbasiert, windbezogen).

Baut aus Quellpunkt + Windrichtung + Ausbreitungslaenge einen Downwind-Kegel
(GeoJSON-Polygon) fuer die Lagekarte. Die meteorologische Windrichtung ist die
Richtung, aus der der Wind KOMMT; die Ausbreitung erfolgt in die Gegenrichtung
(wind_from + 180), analog zum Windrichtungs-Symbol der Lagefuehrungskarte.

Phase 2 (Gauss) baut hierauf auf (siehe PR 8) — die Geometrie-Helfer bleiben.
"""
from __future__ import annotations

import math

# Naeherung fuer kleine Distanzen (aequidistante Zylinderprojektion).
_M_PER_DEG_LAT = 111_320.0


def ausbreitungsrichtung(wind_from_deg: float | None) -> float:
    """Ausbreitungsrichtung (Grad, Richtung in die der Wind blaest) aus der
    meteorologischen Windrichtung (aus der er kommt)."""
    if wind_from_deg is None:
        return 0.0
    return (float(wind_from_deg) + 180.0) % 360.0


def _offset(lat: float, lng: float, bearing_deg: float, dist_m: float) -> list[float]:
    """Punkt in dist_m Entfernung unter bearing_deg (von Nord, im Uhrzeigersinn).

    Rueckgabe als GeoJSON-Koordinate [lng, lat].
    """
    theta = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(theta)) / _M_PER_DEG_LAT
    m_per_deg_lng = _M_PER_DEG_LAT * math.cos(math.radians(lat)) or 1e-9
    dlng = (dist_m * math.sin(theta)) / m_per_deg_lng
    return [lng + dlng, lat + dlat]


def plume_polygon(
    lat: float,
    lng: float,
    richtung_deg: float,
    laenge_m: float,
    halbwinkel_deg: float = 25.0,
    segmente: int = 8,
) -> dict:
    """GeoJSON-Polygon eines Downwind-Kegels ab (lat, lng) in Ausbreitungsrichtung.

    - richtung_deg: Ausbreitungsrichtung (bereits "blaest nach", nicht meteorologisch).
    - laenge_m: Reichweite entlang der Mittelachse.
    - halbwinkel_deg: halber Oeffnungswinkel des Kegels.
    """
    laenge_m = max(1.0, float(laenge_m))
    halbwinkel_deg = min(89.0, max(1.0, float(halbwinkel_deg)))
    segmente = max(2, int(segmente))

    ring: list[list[float]] = [[lng, lat]]  # Spitze = Quelle
    start = richtung_deg - halbwinkel_deg
    schritt = (2.0 * halbwinkel_deg) / segmente
    # Bogen auf der Kegel-Stirnseite (konstante Distanz), damit die Front rund ist.
    for i in range(segmente + 1):
        b = start + i * schritt
        ring.append(_offset(lat, lng, b, laenge_m))
    ring.append([lng, lat])  # Ring schliessen

    return {"type": "Polygon", "coordinates": [ring]}
