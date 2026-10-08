"""Ermittelt eine pruefpflichtige Sperrengeometrie aus Adressabschnitten."""

from __future__ import annotations

import json
import math
import re

from sqlalchemy.orm import Session

from app.models.master import OrgSettings
from app.services.address_autocomplete import suggest_addresses
from app.services.geocoding import geocode_address
from app.services.osm_street_service import fetch_street_network, normalize_street_name, search_center
from app.services.road_closure_section_resolver import SectionResult, parse_endpoint, resolve
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


async def resolve_section(
    db: Session, org, street: str, from_text: str | None, to_text: str | None, city: str | None = None
) -> SectionResult:
    """OSM-first; der bisherige Hausnummern/OSRM-Weg bleibt ein sicherer Fallback."""
    from_ep, to_ep = parse_endpoint(from_text, street), parse_endpoint(to_text, street)
    house_points = {}
    for endpoint in (from_ep, to_ep):
        if endpoint.kind == "house":
            point = await geocode_address(street, endpoint.value, city or getattr(org, "city", None))
            if point is not None:
                house_points[endpoint.value] = (point.lat, point.lng)
    result, network = await _resolve_osm_section(db, org, street, from_ep, to_ep, city, house_points)
    if network is not None:
        assert result is not None
        if result.geometry is not None:
            return result
        problem = "Straße nicht in OSM gefunden"
        fallback_hint = "Straße nicht in OSM gefunden – Näherung über Hausnummern"
    else:
        problem = (
            "OSM-Straßennetz derzeit nicht erreichbar – später mit strassensperre_geometrie_ermitteln "
            "oder „Abschnitt aus Adresse ermitteln“ erneut versuchen"
        )
        fallback_hint = "OSM-Straßennetz nicht erreichbar – Näherung über Hausnummern"
    try:
        fallback = await section_from_address(street, from_text, to_text, city or getattr(org, "city", None))
    except ValueError as exc:
        return SectionResult(hinweise=[problem, str(exc)], osm_erreichbar=network is not None)
    return SectionResult(
        geometry=fallback["geometry"], quality="niedrig", geometry_status="needs_review",
        hinweise=[fallback_hint], osm_erreichbar=network is not None,
    )


async def _resolve_osm_section(db, org, street, from_ep, to_ep, city, house_points):
    """Gemeinsamer OSM-Teil für Geometrieauflösung und Adressprüfung."""
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    resolved_city = city or getattr(org, "city", None)
    center = await search_center(org, settings, resolved_city)
    network = await fetch_street_network(street, *center, city=resolved_city) if center else None
    return (resolve(network, street, from_ep, to_ep, house_points), network) if network is not None else (None, None)


def validation_from_section(result: SectionResult, street: str) -> dict:
    """Macht ein Resolver-Ergebnis als fehlertolerante Adressvalidierung verfügbar."""
    exact = bool(result.osm_name and normalize_street_name(result.osm_name) == normalize_street_name(street))
    status = "ok" if exact else ("abweichend" if result.osm_name else "nicht_gefunden")
    if not result.osm_erreichbar:
        status = "unbekannt"
    endpoints = {item.get("rolle"): item for item in result.endpoints}

    def endpoint(role: str) -> dict:
        item = endpoints.get(role, {})
        text = item.get("text", "")
        method = item.get("methode", "nicht_geprueft" if text else "")
        is_house = parse_endpoint(text, street).kind == "house"
        if is_house:
            method = "nicht_geprueft"
        found = None if not text or method == "nicht_geprueft" else method != "nicht_gefunden"
        return {"text": text, "gefunden": found, "methode": method}

    return {
        "status": status,
        "osm_name": result.osm_name,
        "laenge_m": result.length_m,
        "vorschlaege": [],
        "von": endpoint("von"),
        "bis": endpoint("bis"),
        "qualitaet": result.quality,
    }


async def validate_address(
    db, org, street: str, from_text: str | None, to_text: str | None, city: str | None = None
) -> dict:
    """Prüft Straße und Kreuzungen ausschließlich gegen das OSM-Straßennetz."""
    from_ep, to_ep = parse_endpoint(from_text, street), parse_endpoint(to_text, street)
    if not (street or "").strip():
        return {
            "status": "keine_strasse", "osm_name": None, "laenge_m": None, "vorschlaege": [],
            "von": {"text": from_ep.raw, "gefunden": None, "methode": ""},
            "bis": {"text": to_ep.raw, "gefunden": None, "methode": ""}, "qualitaet": None,
        }
    result, network = await _resolve_osm_section(db, org, street, from_ep, to_ep, city, {})
    if network is None:
        return {
            "status": "unbekannt", "osm_name": None, "laenge_m": None, "vorschlaege": [],
            "von": {"text": from_ep.raw, "gefunden": None, "methode": ""},
            "bis": {"text": to_ep.raw, "gefunden": None, "methode": ""}, "qualitaet": None,
        }
    assert result is not None
    data = validation_from_section(result, street)
    if data["status"] == "abweichend":
        target = normalize_street_name(street)
        names = (way.name for way in network.ways if target in normalize_street_name(way.name))
        data["vorschlaege"] = list(dict.fromkeys(names))[:5]
    elif data["status"] == "nicht_gefunden":
        suggestions = await suggest_addresses(
            db, q=street, field="street", city=city or getattr(org, "city", None), street=None,
            org_id=org.id, limit=5,
        )
        data["vorschlaege"] = list(dict.fromkeys(item.street or item.label for item in suggestions))[:5]
    return data


def section_meta(result: SectionResult, methode: str) -> str:
    return json.dumps({"methode": methode, "osm_name": result.osm_name, "endpoints": result.endpoints,
                       "mehrdeutigkeiten": result.mehrdeutigkeiten, "quality": result.quality}, ensure_ascii=False)
