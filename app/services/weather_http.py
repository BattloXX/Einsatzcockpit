"""Begrenzte Wiederholungen für Wetter-GETs; keine URLs in Fehlerprotokollen."""
import asyncio
from typing import Any

import httpx

from app.config import settings


async def get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    retries = max(0, min(2, settings.WEATHER_HTTP_RETRIES))
    timeout = max(0.01, float(settings.WEATHER_HTTP_TIMEOUT))
    backoff = max(0.0, min(2.0, settings.WEATHER_RETRY_BACKOFF_S))
    # Harte Obergrenze umfasst Connect, Pool, Lesen und Backoff aller Versuche.
    async with asyncio.timeout((retries + 1) * timeout + retries * backoff):
        for attempt in range(retries + 1):
            try:
                async with asyncio.timeout(timeout):
                    response = await client.get(url, **kwargs)
                    response.raise_for_status()
                    return response
            except httpx.HTTPStatusError as exc:
                if not 500 <= exc.response.status_code < 600 or attempt == retries:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
                if attempt == retries:
                    raise
            await asyncio.sleep(backoff)
    raise RuntimeError("Wetter-HTTP-Budget erschöpft")
