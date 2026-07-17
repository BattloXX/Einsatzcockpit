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

# Pasquill-Stabilitaetsklassen A (sehr labil) .. F (sehr stabil).
STABILITAETSKLASSEN = ("A", "B", "C", "D", "E", "F")


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


# ── Phase 2: Gausssches Ausbreitungsmodell ────────────────────────────────────

def _sigma_yz(x: float, klasse: str) -> tuple[float, float]:
    """Briggs-Dispersionskoeffizienten sigma_y, sigma_z (laendlich) in Metern.

    Standardformeln (Briggs, rural), x in Metern. Quelle: gaengige
    Ausbreitungs-Lehrbuecher (z. B. EPA/ALOHA-Grundlagen).
    """
    k = klasse.upper() if klasse else "D"
    x = max(1.0, x)
    inv_sqrt = (1.0 + 0.0001 * x) ** -0.5
    tab = {
        "A": (0.22 * x * inv_sqrt, 0.20 * x),
        "B": (0.16 * x * inv_sqrt, 0.12 * x),
        "C": (0.11 * x * inv_sqrt, 0.08 * x * (1.0 + 0.0002 * x) ** -0.5),
        "D": (0.08 * x * inv_sqrt, 0.06 * x * (1.0 + 0.0015 * x) ** -0.5),
        "E": (0.06 * x * inv_sqrt, 0.03 * x * (1.0 + 0.0003 * x) ** -1.0),
        "F": (0.04 * x * inv_sqrt, 0.016 * x * (1.0 + 0.0003 * x) ** -1.0),
    }
    return tab.get(k, tab["D"])


def _punkt(lat: float, lng: float, richtung_deg: float, x_m: float, y_m: float) -> list[float]:
    """GeoJSON-Koordinate [lng, lat] fuer x_m entlang der Achse + y_m quer dazu."""
    theta = math.radians(richtung_deg)
    theta_perp = math.radians(richtung_deg + 90.0)
    north = x_m * math.cos(theta) + y_m * math.cos(theta_perp)
    east = x_m * math.sin(theta) + y_m * math.sin(theta_perp)
    dlat = north / _M_PER_DEG_LAT
    dlng = east / (_M_PER_DEG_LAT * math.cos(math.radians(lat)) or 1e-9)
    return [lng + dlng, lat + dlat]


def gauss_footprint(
    lat: float,
    lng: float,
    richtung_deg: float,
    quellstaerke_g_s: float,
    windgeschw_ms: float,
    stabilitaet: str = "D",
    grenzwert_mg_m3: float = 1.0,
    x_max_m: float = 5000.0,
    schritte: int = 40,
) -> dict:
    """GeoJSON-Polygon der Isokonzentrationsflaeche (Footprint) einer Gauss-Fahne.

    Bodennaher Punktquell-Ansatz mit Bodenreflexion, Zentrallinien-Konzentration
    C0(x) = Q / (pi * u * sigma_y * sigma_z). Der Footprint ist die Flaeche, in der
    C >= grenzwert. Halbbreite je x: y = sigma_y * sqrt(2 ln(C0/grenzwert)).

    Rueckgabe: {"type":"Polygon", ...}; leerer Ring, wenn nichts den Grenzwert erreicht.
    """
    u = max(0.3, float(windgeschw_ms))  # unter 0.3 m/s ist das Modell nicht sinnvoll
    q = max(0.0, float(quellstaerke_g_s))
    grenz_g = max(1e-9, float(grenzwert_mg_m3) / 1000.0)  # mg/m3 -> g/m3
    x_max = max(50.0, float(x_max_m))
    schritte = max(4, int(schritte))

    rechts: list[list[float]] = []
    links: list[list[float]] = []
    dx = x_max / schritte
    for i in range(1, schritte + 1):
        x = i * dx
        sy, sz = _sigma_yz(x, stabilitaet)
        c0 = q / (math.pi * u * sy * sz) if (sy > 0 and sz > 0) else 0.0
        if c0 <= grenz_g:
            continue
        yh = sy * math.sqrt(2.0 * math.log(c0 / grenz_g))
        rechts.append(_punkt(lat, lng, richtung_deg, x, yh))
        links.append(_punkt(lat, lng, richtung_deg, x, -yh))

    if not rechts:
        return {"type": "Polygon", "coordinates": [[]]}

    ring = [[lng, lat]] + rechts + list(reversed(links)) + [[lng, lat]]
    return {"type": "Polygon", "coordinates": [ring]}
