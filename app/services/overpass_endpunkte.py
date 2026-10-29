"""Overpass-Ausweichendpunkte mit Zeitbudget und gemeinsamem Fehlerschutz."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.services import hydrant_service

logger = logging.getLogger(__name__)


def endpunkte(primaer: str) -> list[str]:
    # Die konfigurierte Primaer-URL gilt immer (auch eine interne http-Instanz);
    # nur die Ausweichliste ist auf https beschraenkt.
    ergebnis = [primaer.strip()]
    for wert in settings.OVERPASS_FALLBACK_URLS.split(","):
        url = wert.strip()
        if url.startswith("https://") and urlsplit(url).hostname and url not in ergebnis:
            ergebnis.append(url)
    return ergebnis


class OverpassNichtVerfuegbar(Exception):
    def __init__(self, letzter_fehler: Exception | None, versuchte: int):
        super().__init__(f"Overpass derzeit nicht erreichbar ({versuchte} Endpunkte versucht)")
        self.letzter_fehler = letzter_fehler
        self.versuchte = versuchte


def _retry_after(exc: Exception) -> float:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 429:
        return 0
    wert = exc.response.headers.get("Retry-After", "")
    try:
        return max(0, float(wert))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(wert).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


async def _fehler(schluessel: str, exc: Exception) -> None:
    async with hydrant_service._sperre(schluessel):
        jetzt = time.time()
        zustand = await hydrant_service._lesen(schluessel, redis_zuerst=True) or {}
        fehler = [zeit for zeit in zustand.get("fehler", [])
                  if jetzt - zeit <= settings.OVERPASS_ENDPUNKT_FENSTER_SECONDS]
        fehler.append(jetzt)
        pause = _retry_after(exc)
        if len(fehler) >= settings.OVERPASS_ENDPUNKT_FEHLER:
            pause = max(pause, settings.OVERPASS_ENDPUNKT_PAUSE_SECONDS)
        offen_bis = max(zustand.get("offen_bis", 0), jetzt + pause if pause else 0)
        await hydrant_service._setzen(
            schluessel, {"fehler": fehler, "offen_bis": offen_bis},
            math.ceil(max(settings.OVERPASS_ENDPUNKT_FENSTER_SECONDS, offen_bis - jetzt, 1)),
        )


async def abfragen(
    query: str, *, primaer: str, timeout_je_versuch: float, budget_s: float, zweck: str,
) -> dict:
    start = time.monotonic()
    letzter_fehler: Exception | None = None
    versuchte = 0
    try:
        async with asyncio.timeout(max(0, budget_s)):
            for nummer, url in enumerate(endpunkte(primaer)):
                schluessel = "overpass-endpunkt:" + hashlib.sha256(url.encode()).hexdigest()
                zustand = await hydrant_service._lesen(schluessel, redis_zuerst=True) or {}
                if zustand.get("offen_bis", 0) > time.time():
                    continue
                rest = budget_s - (time.monotonic() - start)
                if rest < 1:
                    break
                versuchte += 1
                try:
                    zeitlimit = min(timeout_je_versuch, rest)
                    async with asyncio.timeout(zeitlimit):
                        async with httpx.AsyncClient(
                            headers={"User-Agent": settings.HYDRANT_USER_AGENT}, timeout=zeitlimit,
                        ) as client:
                            antwort = await client.post(url, data={"data": query})
                            antwort.raise_for_status()
                            daten = antwort.json()
                            if not isinstance(daten, dict):
                                raise ValueError("Ungueltige Overpass-Antwort")
                            bemerkung = str(daten.get("remark", "")).lower()
                            if "timeout" in bemerkung or "runtime error" in bemerkung:
                                raise ValueError("Overpass-Zeitlimit erreicht")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {429, 502, 503, 504}:
                        raise
                    letzter_fehler = exc
                except (httpx.TransportError, TimeoutError, ValueError) as exc:
                    letzter_fehler = exc
                else:
                    async with hydrant_service._sperre(schluessel):
                        await hydrant_service._setzen(
                            schluessel, {"fehler": [], "offen_bis": 0}, settings.OVERPASS_ENDPUNKT_FENSTER_SECONDS,
                        )
                    if nummer:
                        logger.info("Overpass-Fallback: zweck=%s host=%s", zweck, urlsplit(url).hostname)
                    return daten
                await _fehler(schluessel, letzter_fehler)
    except TimeoutError as exc:
        letzter_fehler = exc
    raise OverpassNichtVerfuegbar(letzter_fehler, versuchte)
