"""Validierung und Markerpositionen fuer die Verortung von Planelementen."""

from shapely.geometry import shape

from app.services.katplan_typen import ELEMENT_TYPEN
from app.services.road_closure_geo_service import validate_geometry


def pruefe_geometrie(typ: str, roh: str | None) -> dict | None:
    if not roh or not roh.strip():
        return None
    erlaubt = ELEMENT_TYPEN[typ].geometrie
    if erlaubt == "keine":
        raise ValueError("Dieser Elementtyp wird nicht verortet.")
    geom = validate_geometry(roh)
    if erlaubt == "punkt" and geom["type"] != "Point":
        raise ValueError("Dieser Elementtyp erlaubt nur einen Punkt.")
    return geom


def ableite_punkt(geom: dict) -> tuple[float, float]:
    punkt = shape(geom)
    if geom["type"] != "Point":
        punkt = punkt.representative_point()
    return float(punkt.y), float(punkt.x)
