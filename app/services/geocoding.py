"""Nominatim-Geocoding (OSM).

Nur Forward-Geocoding (Adresse → Koordinaten).
Policy: max. 1 Request/Sekunde an nominatim.openstreetmap.org.
User-Agent muss gesetzt sein (OSM-Pflicht).
"""
import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.geocoding")

# 1-req/sec rate-drossel für die öffentliche Nominatim-Instanz
_lock = asyncio.Lock()


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

    async with _lock:
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
        finally:
            # Sicherstellen, dass zwischen zwei Requests mind. 1 Sek. liegt
            await asyncio.sleep(1.1)

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
