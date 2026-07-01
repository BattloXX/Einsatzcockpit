"""Nominatim-Geocoding (OSM).

Nur Forward-Geocoding (Adresse → Koordinaten).
Policy: max. 1 Request/Sekunde an nominatim.openstreetmap.org.
User-Agent muss gesetzt sein (OSM-Pflicht).
"""
import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.geocoding")

# 1-req/sec rate-drossel für die öffentliche Nominatim-Instanz. Der Lock wird
# NUR für die Drossel-Wartezeit gehalten (STAB-7) — nicht mehr für die gesamte
# HTTP-Requestdauer, sonst serialisiert ein langsamer Nominatim-Call alle
# gleichzeitig wartenden Geocode-Anfragen unnötig hintereinander.
_lock = asyncio.Lock()
_MIN_INTERVAL_S = 1.1
_last_request_ts: float = 0.0


async def _throttle() -> None:
    """Stellt sicher, dass zwei Request-Starts mind. _MIN_INTERVAL_S auseinanderliegen."""
    global _last_request_ts
    async with _lock:
        now = time.monotonic()
        wait = _last_request_ts + _MIN_INTERVAL_S - now
        if wait > 0:
            await asyncio.sleep(wait)
            now = time.monotonic()
        _last_request_ts = now


@dataclass
class GeocodeResult:
    lat: float
    lng: float
    display_name: str


async def geocode_address(
    street: str | None,
    house_number: str | None,
    city: str | None,
) -> GeocodeResult | None:
    """Geocodiert eine Adresse via Nominatim. Gibt None zurück bei Fehler oder keinem Treffer."""
    parts = [p for p in [street, house_number, city] if p]
    if not parts:
        return None

    query = " ".join(parts)

    await _throttle()
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            timeout=settings.NOMINATIM_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": "1",
                    "addressdetails": "0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Nominatim-Anfrage fehlgeschlagen: %s", exc)
        return None

    if not data:
        return None

    first = data[0]
    try:
        return GeocodeResult(
            lat=float(first["lat"]),
            lng=float(first["lon"]),
            display_name=first.get("display_name", query),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Nominatim-Antwort konnte nicht geparst werden: %s", exc)
        return None
