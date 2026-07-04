"""Umrechnung von LIS-Fahrzeugkoordinaten (OperationUnit.LocationX/LocationY) nach WGS84.

`GetOperationUnits` liefert für Einheiten mit Status "S5 - am Einsatzort" (siehe
LIS_IPR_Koordinaten_Dokumentation.md) ein Koordinatenpaar in einem großen,
ganzzahligen projizierten System — vermutlich MGI/Austria GK West (EPSG:31254),
wie im GMSC-Kartenteil der Schnittstelle. Ein Abgleich mit zwei aus einem Mitschnitt
bekannten Fahrzeugpositionen (Wolfurt RLF-A/LFB-C, beide am Feuerwehrhaus Wolfurt,
LocationX/Y ≈ 105300/260800) zeigt: gegenüber der offiziellen EPSG:31254-Definition
(x_0=0) sind die rohen LocationX-Werte um einen festen Versatz von ca. 150000 m nach
Osten verschoben — mit Versatz ergibt sich lat/lon ≈ 47.48/9.74 (plausibel für
Wolfurt/Vorarlberg), ohne Versatz käme lon ≈ 11.73 heraus (das läge in Tirol, nicht
in Vorarlberg). Dieser empirisch ermittelte Versatz ist NICHT durch eine offizielle
Quelle bestätigt (nur 2 Datenpunkte) — bei Gelegenheit gegen eine echte
Adresse/Koordinate desselben Einsatzes verifizieren.
"""
from __future__ import annotations

import logging

from pyproj import Transformer

logger = logging.getLogger("einsatzleiter.lis.geo")

_X_OFFSET = 150_000.0
_transformer = Transformer.from_crs("EPSG:31254", "EPSG:4326", always_xy=True)

# Grobe Plausibilitätsgrenzen Vorarlberg/Tirol-West — verwirft eindeutig falsch
# transformierte Werte, statt sie stillschweigend als Position zu übernehmen.
_LAT_MIN, _LAT_MAX = 46.5, 47.8
_LON_MIN, _LON_MAX = 9.3, 10.6


def lis_unit_coords_to_wgs84(location_x: float, location_y: float) -> tuple[float, float] | None:
    """Gibt (lat, lon) zurück, oder None bei Transformationsfehler/unplausiblem Ergebnis.

    Kein Fallback auf (0, 0) — fehlende oder unplausible Koordinaten bleiben None,
    damit sie im UI nie fälschlich als geortete Position erscheinen.
    """
    try:
        lon, lat = _transformer.transform(location_x - _X_OFFSET, location_y)
    except Exception:
        logger.exception("LIS-Koordinatentransformation fehlgeschlagen (x=%s, y=%s)", location_x, location_y)
        return None

    if not (_LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX):
        logger.warning(
            "LIS-Fahrzeugkoordinate außerhalb des plausiblen Bereichs verworfen "
            "(x=%s, y=%s -> lat=%.5f, lon=%.5f)", location_x, location_y, lat, lon,
        )
        return None
    return lat, lon
