"""Kleine, fehlertolerante OSM-Strassennetz-Abfrage fuer Sperrenabschnitte."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.models.master import OrgSettings
from app.services.geocoding import geocode_address

logger = logging.getLogger(__name__)
_CACHE: dict[tuple[str, str, float, float, int], tuple[float, OsmNetwork]] = {}
_TTL = 600
RETRY_DELAY_SECONDS = 2.0


def normalize_street_name(name: str) -> str:
    value = (name or "").casefold().replace("ß", "ss")
    value = value.replace("ä", "a").replace("ö", "o").replace("ü", "u")
    value = re.sub(r"\bstr[.]?(?=\s|$)", "strasse", value)
    value = re.sub(r"[.\-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class OsmWay:
    id: int
    name: str
    highway: str
    coords: list[tuple[float, float]]
    node_ids: list[int]


@dataclass(frozen=True)
class OsmNetwork:
    ways: list[OsmWay]


def _query(regex: str, lat: float, lng: float, radius: int, city: str | None = None) -> str:
    if city:
        return (
            "[out:json][timeout:15];"
            f'area["boundary"="administrative"]["name"="{city}"]->.a;'
            f'way["highway"]["name"~"{regex}",i](area.a)->.s;'
            "node(w.s)->.n;way(bn.n)[\"highway\"][\"name\"]->.x;(.s;.x;);out body geom;"
        )
    return (
        "[out:json][timeout:15];"
        f'way["highway"]["name"~"{regex}",i](around:{radius},{lat},{lng})->.s;'
        "node(w.s)->.n;way(bn.n)[\"highway\"][\"name\"]->.x;(.s;.x;);out body geom;"
    )


def _parse(data: dict) -> OsmNetwork:
    ways: list[OsmWay] = []
    for item in data.get("elements", []):
        if not isinstance(item, dict) or item.get("type") != "way":
            continue
        tags = item.get("tags") or {}
        geometry, nodes = item.get("geometry") or [], item.get("nodes") or []
        coords = [(float(p["lat"]), float(p["lon"])) for p in geometry if "lat" in p and "lon" in p]
        if len(coords) < 2 or not tags.get("name"):
            continue
        ways.append(OsmWay(int(item.get("id", 0)), str(tags["name"]), str(tags.get("highway", "")), coords,
                           [int(node) for node in nodes]))
    return OsmNetwork(ways)


async def fetch_street_network(
    name: str, center_lat: float, center_lng: float, radius_m: int | None = None, city: str | None = None
) -> OsmNetwork | None:
    radius = int(radius_m or settings.STRASSEN_SUCHRADIUS_M)
    normalized = normalize_street_name(name)
    normalized_city = normalize_street_name(city or "")
    key = (normalized, normalized_city, round(center_lat, 3), round(center_lng, 3), radius)
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    escaped = "".join("." if char in r".^$*+?()[]{}|\\" else char for char in name.strip())
    # Exakter Gebietsname: eine Regex auf Gebietsnamen ist bei Overpass zu langsam (Timeout).
    city_safe = (city or "").strip().replace('"', "").replace("\\", "")

    async def request(client, query: str):
        for attempt in range(2):
            try:
                response = await client.post(settings.STRASSEN_OVERPASS_URL, data={"data": query})
                if response.status_code in {429, 502, 503, 504}:
                    if attempt == 0:
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                    return None
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                return None
        return None

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.HYDRANT_USER_AGENT}, timeout=settings.STRASSEN_OVERPASS_TIMEOUT_SECONDS
        ) as client:
            async def lookup(area_city: str | None):
                response = await request(client, _query(f"^{escaped}$", center_lat, center_lng, radius, area_city))
                if response is None:
                    return None
                network = _parse(response.json())
                if not any(normalize_street_name(way.name) == normalized for way in network.ways):
                    response = await request(client, _query(escaped, center_lat, center_lng, radius, area_city))
                    if response is None:
                        return None
                    network = _parse(response.json())
                return network

            network = await lookup(city_safe or None)
            if network is None:
                return None
            if city_safe and not network.ways:
                network = await lookup(None)
                if network is None:
                    return None
    except Exception as exc:
        logger.warning("Overpass-Strassenabfrage fehlgeschlagen: %s", exc)
        return None
    if len(_CACHE) >= 128:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.time(), network)
    return network


async def search_center(org, org_settings: OrgSettings | None, city: str | None) -> tuple[float, float] | None:
    if city:
        point = await geocode_address(None, None, city)
        if point is not None:
            return point.lat, point.lng
    if getattr(org, "fallback_lat", None) is not None and getattr(org, "fallback_lng", None) is not None:
        return org.fallback_lat, org.fallback_lng
    if org_settings and org_settings.routing_start_lat is not None and org_settings.routing_start_lng is not None:
        return org_settings.routing_start_lat, org_settings.routing_start_lng
    return None
