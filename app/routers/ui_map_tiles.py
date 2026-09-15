"""Same-origin-Proxy und LRU-Cache fuer OSM-Kartenkacheln.

Kiosk-Browser unterdruecken teils trotz Referrer-Policy den Referer.  Damit OSM
die Kartenkacheln dennoch policy-konform sieht, ruft ausschliesslich dieser
Server die externe Tile-URL mit identifizierendem User-Agent und Referer ab.
Der kleine Prozess-Cache verhindert wiederholte Abrufe gleicher Kacheln. Bei
sehr vielen Organisationen mit unterschiedlichen Kartenausschnitten ist er
kein Ersatz fuer ein verteiltes Cache-/Rate-Limiting-System.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core.map_config import OSM_TILE_REFERER, OSM_TILE_URL, OSM_TILE_USER_AGENT

logger = logging.getLogger("einsatzleiter.osm_tiles")

router = APIRouter()

_CACHE_MAXSIZE = 2000
_FETCH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _CachedTile:
    content: bytes
    content_type: str
    cache_control: str | None
    expires: str | None


_tile_cache: OrderedDict[tuple[int, int, int], _CachedTile] = OrderedDict()


def _cache_response(tile: _CachedTile) -> Response:
    headers = {}
    if tile.cache_control:
        headers["Cache-Control"] = tile.cache_control
    if tile.expires:
        headers["Expires"] = tile.expires
    return Response(content=tile.content, media_type=tile.content_type, headers=headers)


def _validate_coordinates(z: int, x: int, y: int) -> None:
    if not 0 <= z <= 19:
        raise HTTPException(status_code=400, detail="Ungueltige Karten-Zoomstufe.")
    tile_count = 1 << z
    if not 0 <= x < tile_count or not 0 <= y < tile_count:
        raise HTTPException(status_code=400, detail="Ungueltige Kartenkachel-Koordinaten.")


@router.get("/karten/osm-tile/{z}/{x}/{y}.png", include_in_schema=False)
async def osm_tile(z: int, x: int, y: int) -> Response:
    """Liefert eine gecachte OSM-PNG-Kachel ueber die eigene Origin aus."""
    _validate_coordinates(z, x, y)
    key = (z, x, y)
    cached = _tile_cache.get(key)
    if cached is not None:
        _tile_cache.move_to_end(key)
        return _cache_response(cached)

    url = OSM_TILE_URL.format(z=z, x=x, y=y)
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
            upstream = await client.get(
                url,
                headers={"User-Agent": OSM_TILE_USER_AGENT, "Referer": OSM_TILE_REFERER},
            )
            upstream.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("OSM-Kachel konnte nicht geladen werden (%s): %s", url, exc)
        raise HTTPException(status_code=502, detail="OSM-Kachel nicht verfuegbar.") from exc

    tile = _CachedTile(
        content=upstream.content,
        content_type=upstream.headers.get("content-type", "image/png"),
        cache_control=upstream.headers.get("cache-control"),
        expires=upstream.headers.get("expires"),
    )
    _tile_cache[key] = tile
    _tile_cache.move_to_end(key)
    if len(_tile_cache) > _CACHE_MAXSIZE:
        _tile_cache.popitem(last=False)
    return _cache_response(tile)
