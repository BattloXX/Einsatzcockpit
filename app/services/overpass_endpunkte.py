"""Overpass-Ausweichendpunkte mit Zeitbudget und gemeinsamem Fehlerschutz."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import socket
import ssl
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.services import geodienst_statistik, hydrant_service

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
    def __init__(self, letzter_fehler: Exception | None, versuchte: int, versuche: list[dict] | None = None):
        super().__init__(f"Overpass derzeit nicht erreichbar ({versuchte} Endpunkte versucht)")
        self.letzter_fehler = letzter_fehler
        self.versuchte = versuchte
        self.versuche = versuche or []
        self.code = "OVERPASS_ALL_ENDPOINTS_FAILED"


class OverpassAbfrageFehler(httpx.HTTPStatusError):
    code = "OVERPASS_HTTP_4XX"


def fehlercode(exc: BaseException) -> str:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "OVERPASS_TIMEOUT"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "OVERPASS_RATE_LIMIT"
        return "OVERPASS_HTTP_5XX" if 500 <= status <= 599 else "OVERPASS_HTTP_4XX"
    kette = [exc]
    gesehen: set[int] = set()
    while kette:
        ursache = kette.pop()
        if id(ursache) in gesehen:
            continue
        gesehen.add(id(ursache))
        if isinstance(ursache, ssl.SSLError):
            return "OVERPASS_TLS_ERROR"
        if isinstance(exc, httpx.ConnectError) and (
            isinstance(ursache, socket.gaierror)
            or any(text in str(ursache).lower() for text in (
                "name or service not known", "temporary failure in name resolution",
            ))
        ):
            return "OVERPASS_DNS_ERROR"
        kette.extend(u for u in (ursache.__cause__, ursache.__context__) if u is not None)
    if isinstance(exc, httpx.TransportError):
        return "OVERPASS_CONNECTION_ERROR"
    return "OVERPASS_INVALID_RESPONSE"


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
            schluessel, {"fehler": fehler, "offen_bis": offen_bis, "code": fehlercode(exc)},
            math.ceil(max(settings.OVERPASS_ENDPUNKT_FENSTER_SECONDS, offen_bis - jetzt, 1)),
        )


async def abfragen(
    query: str, *, primaer: str, timeout_je_versuch: float, budget_s: float, zweck: str,
) -> dict:
    start = time.monotonic()
    letzter_fehler: Exception | None = None
    versuchte = 0
    versuche: list[dict] = []
    try:
        async with asyncio.timeout(max(0, budget_s)):
            for nummer, url in enumerate(endpunkte(primaer)):
                schluessel = "overpass-endpunkt:" + hashlib.sha256(url.encode()).hexdigest()
                zustand = await hydrant_service._lesen(schluessel, redis_zuerst=True) or {}
                versuch = {"host": urlsplit(url).hostname, "code": zustand.get("code", "OVERPASS_CONNECTION_ERROR"),
                           "http_status": None, "dauer_ms": 0, "uebersprungen": True}
                if zustand.get("offen_bis", 0) > time.time():
                    versuche.append(versuch)
                    continue
                rest = budget_s - (time.monotonic() - start)
                if rest < 1:
                    break
                if letzter_fehler is not None:
                    await geodienst_statistik.zaehlen("overpass", "retry_count")
                versuchte += 1
                versuch_start = time.monotonic()
                http_status = None
                try:
                    zeitlimit = min(timeout_je_versuch, rest)
                    async with asyncio.timeout(zeitlimit):
                        async with httpx.AsyncClient(
                            headers={"User-Agent": settings.HYDRANT_USER_AGENT}, timeout=zeitlimit,
                        ) as client:
                            antwort = await client.post(url, data={"data": query})
                            http_status = antwort.status_code
                            antwort.raise_for_status()
                            daten = antwort.json()
                            if not isinstance(daten, dict):
                                raise ValueError("Ungueltige Overpass-Antwort")
                            bemerkung = str(daten.get("remark", "")).lower()
                            if "timeout" in bemerkung or "runtime error" in bemerkung:
                                raise ValueError("Overpass-Zeitlimit erreicht")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429 and not 500 <= exc.response.status_code <= 599:
                        await geodienst_statistik.fehler(
                            "overpass", url, fehlercode(exc), http_status,
                            round((time.monotonic() - versuch_start) * 1000),
                        )
                        raise OverpassAbfrageFehler(str(exc), request=exc.request, response=exc.response) from exc
                    letzter_fehler = exc
                except (httpx.TransportError, TimeoutError, ValueError) as exc:
                    letzter_fehler = exc
                else:
                    await geodienst_statistik.erfolg(
                        "overpass", url, round((time.monotonic() - versuch_start) * 1000),
                    )
                    async with hydrant_service._sperre(schluessel):
                        await hydrant_service._setzen(
                            schluessel, {"fehler": [], "offen_bis": 0}, settings.OVERPASS_ENDPUNKT_FENSTER_SECONDS,
                        )
                    if nummer:
                        logger.info("Overpass-Fallback: zweck=%s host=%s", zweck, urlsplit(url).hostname)
                    return daten
                versuch.update(code=fehlercode(letzter_fehler),
                               http_status=http_status,
                               dauer_ms=round((time.monotonic() - versuch_start) * 1000), uebersprungen=False)
                versuche.append(versuch)
                await geodienst_statistik.fehler(
                    "overpass", url, versuch["code"], http_status, versuch["dauer_ms"],
                )
                await _fehler(schluessel, letzter_fehler)
    except TimeoutError as exc:
        letzter_fehler = exc
        if versuchte > sum(not v["uebersprungen"] for v in versuche):
            versuch.update(code="OVERPASS_TIMEOUT", dauer_ms=round((time.monotonic() - versuch_start) * 1000),
                           uebersprungen=False)
            versuche.append(versuch)
            await geodienst_statistik.fehler("overpass", url, "OVERPASS_TIMEOUT", http_status, versuch["dauer_ms"])
    raise OverpassNichtVerfuegbar(letzter_fehler, versuchte, versuche)
