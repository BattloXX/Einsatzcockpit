"""Ermittelt eine pruefpflichtige Sperrengeometrie aus Adressabschnitten."""

from __future__ import annotations

import math
import re

from app.services.geocoding import geocode_address
from app.services.routing_service import strassen_route

_HOUSE_NUMBER = re.compile(r"\b(\d+[A-Za-z]?)\b")
_NOT_FOUND = "Adresse nicht gefunden – bitte Abschnitt auf der Karte einzeichnen."


def _house_number(value: str | None) -> str | None:
    match = _HOUSE_NUMBER.search(value or "")
    return match.group(1) if match else None


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Luftlinienentfernung mit der Haversine-Formel."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*first, *second))
    d_lat, d_lng = lat2 - lat1, lng2 - lng1
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _line_length_m(coords: list[list[float]]) -> float:
    length = 0.0
    for first, second in zip(coords, coords[1:]):
        length += _distance_m((first[0], first[1]), (second[0], second[1]))
    return length


async def section_from_address(
    street: str, from_text: str | None, to_text: str | None, city: str | None
) -> dict:
    """Ermittelt eine Geometrie, die vor dem Speichern manuell geprueft werden muss."""
    from_number, to_number = _house_number(from_text), _house_number(to_text)
    if not from_number and not to_number:
        raise ValueError("Hausnummern nicht erkannt – bitte Abschnitt auf der Karte einzeichnen.")

    first = await geocode_address(street, from_number, city) if from_number else None
    second = await geocode_address(street, to_number, city) if to_number else None
    if (from_number and first is None) or (to_number and second is None):
        raise ValueError(_NOT_FOUND)

    if first is not None and second is None:
        return {
            "geometry": {"type": "Point", "coordinates": [first.lng, first.lat]},
            "geometry_status": "needs_review",
            "hinweis": "Bitte Abschnitt auf der Karte prüfen und ggf. korrigieren.",
        }
    if second is not None and first is None:
        return {
            "geometry": {"type": "Point", "coordinates": [second.lng, second.lat]},
            "geometry_status": "needs_review",
            "hinweis": "Bitte Abschnitt auf der Karte prüfen und ggf. korrigieren.",
        }

    assert first is not None and second is not None
    endpoints = [(first.lat, first.lng), (second.lat, second.lng)]
    route = await strassen_route(endpoints)
    coords = route.get("coords") if route else None
    if not coords:
        coords = [[first.lat, first.lng], [second.lat, second.lng]]
    geometry = {"type": "LineString", "coordinates": [[lng, lat] for lat, lng in coords]}
    route_length = float(route.get("laenge_m") or _line_length_m(coords)) if route else _line_length_m(coords)
    direct_length = _distance_m(*endpoints)
    hint = "Bitte Abschnitt auf der Karte prüfen und ggf. korrigieren."
    if route_length > direct_length * 3 + 200:
        hint = "Route weicht stark ab – bitte Abschnitt einzeichnen"
    return {"geometry": geometry, "geometry_status": "needs_review", "hinweis": hint}
