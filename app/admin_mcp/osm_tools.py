"""Lesende OSM-Betriebsdiagnose ohne Fachobjekte oder Probe-Schreibzugriffe."""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.admin_mcp.masking import mask_text
from app.config import settings
from app.services import geodienst_statistik as statistik
from app.services import hydrant_service, osm_gebaeude_import, overpass_endpunkte


def _host(url: str) -> str:
    return mask_text(statistik.hostname(url))


def _lauf(wert: dict | None) -> dict | None:
    if wert is None:
        return None
    return {key: wert.get(key) for key in ("id", "status", "anzahl", "datenstand", "alter_tage", "fehler_code")}


async def _probe(client: httpx.AsyncClient, url: str, *, pbf: bool = False) -> dict:
    start = time.monotonic()
    ergebnis: dict = {"host": _host(url), "status": "fehler", "http_code": None,
                      "code": None, "latenz_ms": 0}
    if pbf:
        ergebnis["last_modified"] = None
    try:
        async with asyncio.timeout(5):
            antwort = await client.request("HEAD" if pbf else "GET", url)
        ergebnis["http_code"] = antwort.status_code
        antwort.raise_for_status()
        ergebnis["status"] = "ok"
        if pbf and antwort.headers.get("last-modified"):
            try:
                datum = parsedate_to_datetime(antwort.headers["last-modified"])
                ergebnis["last_modified"] = datum.astimezone(UTC).isoformat().replace("+00:00", "Z")
            except (ValueError, TypeError, OverflowError):
                ergebnis["last_modified"] = None
    except (httpx.HTTPError, TimeoutError) as exc:
        ergebnis["code"] = overpass_endpunkte.fehlercode(exc)
    ergebnis["latenz_ms"] = round((time.monotonic() - start) * 1000)
    return ergebnis


async def _fehlerschutz(key: str) -> dict:
    wert = await hydrant_service._lesen(key, redis_zuerst=True) or {}
    bis = wert.get("offen_bis", 0)
    return {"status": "offen" if bis > time.time() else "geschlossen",
            "offen_bis": datetime.fromtimestamp(bis, UTC).isoformat().replace("+00:00", "Z") if bis else None}


async def _sammeln(live: bool, quellen: list[tuple[str, str]]) -> dict:
    urls = overpass_endpunkte.endpunkte(settings.HYDRANT_OVERPASS_URL)
    overpass, gebaeude, hydranten, *schutz = await asyncio.gather(
        statistik.zusammenfassung("overpass"), statistik.zusammenfassung("gebaeude"),
        _fehlerschutz("hydranten-breaker:" + hashlib.sha256(settings.HYDRANT_OVERPASS_URL.encode()).hexdigest()),
        *(_fehlerschutz("overpass-endpunkt:" + hashlib.sha256(url.encode()).hexdigest()) for url in urls),
    )
    # Nur die in diesem Check benoetigten, bereits hostbereinigten Daten ausgeben.
    for daten in (overpass, gebaeude):
        for host in daten["hosts"]:
            host["host"] = _host(host["host"])
    ergebnis = {"statistik": {"overpass": overpass, "gebaeude": gebaeude},
                "circuit_breaker_status": {"hydranten": hydranten, "endpunkte": [
                    {"host": _host(url), **wert} for url, wert in zip(urls, schutz, strict=True)]}}
    if live:
        async with httpx.AsyncClient(timeout=5, headers={"User-Agent": settings.HYDRANT_USER_AGENT},
                                     follow_redirects=False) as client:
            status_urls = []
            for url in urls:
                teile = urlsplit(url)
                pfad = teile.path.removesuffix("/api/interpreter").rstrip("/") + "/api/status"
                status_urls.append(urlunsplit((teile.scheme, teile.netloc, pfad, "", "")))
            # Alle GET-/HEAD-Proben parallel, keine Overpass-Abfrage und keine Wiederholung.
            async with asyncio.timeout(6):
                proben = await asyncio.gather(*(_probe(client, url) for url in status_urls),
                                             *(_probe(client, url, pbf=True) for _, url in quellen))
        ergebnis["endpoints"] = [probe | {"fehlerschutz": wert}
                                 for probe, wert in zip(proben[:len(urls)], schutz, strict=True)]
        ergebnis["quellen_proben"] = [{"quelle": mask_text(name), **probe}
                                     for (name, _), probe in zip(quellen, proben[len(urls):], strict=True)]
    return ergebnis


def collect_osm(*, live: bool = True) -> dict:
    quellen = osm_gebaeude_import.quellen()
    rohdaten = osm_gebaeude_import.status()
    quellurls = dict(quellen)
    bestand: list[dict] = [{"quelle": mask_text(zeile["quelle"]),
                "host": _host(quellurls[zeile["quelle"]]) if zeile["quelle"] in quellurls else None,
                "aktiv": _lauf(zeile["aktiv"]),
                "pruefung": [_lauf(lauf) for lauf in zeile["pruefung"]],
                "letzter_fehler": _lauf(zeile["letzter_fehler"])} for zeile in rohdaten]
    # collect wird von server._execute in asyncio.to_thread ausgefuehrt: hier
    # existiert kein laufender Loop. asyncio.run erzeugt einen eigenen Thread-Loop.
    async def sammeln() -> dict:
        async with asyncio.timeout(12):
            return await _sammeln(live, quellen)
    evidenz = asyncio.run(sammeln())
    evidenz["lokaler_bestand"] = bestand
    fehlt = any(not next((z["aktiv"] for z in rohdaten if z["quelle"] == name), None) for name, _ in quellen)
    veraltet = any(z["aktiv"] and (z["aktiv"]["alter_tage"] is None
                   or z["aktiv"]["alter_tage"] > settings.OSM_GEBAEUDE_MAX_ALTER_TAGE) for z in bestand)
    pruefung = any(z["pruefung"] for z in bestand)
    importfehler = any(z["letzter_fehler"] for z in bestand)
    letzte = evidenz["statistik"]["overpass"]
    grenze = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    fehler_24h = bool(letzte["last_failure"] and letzte["last_failure"] >= grenze)
    erreichbar = any(p["status"] == "ok" for p in evidenz.get("endpoints", [])) if live else (
        bool(letzte["last_success"] and (not letzte["last_failure"] or letzte["last_success"] > letzte["last_failure"]))
        if letzte["last_success"] or letzte["last_failure"] else None)
    status = "fail" if fehlt and erreichbar is False else (
        "warn" if fehlt or veraltet or pruefung or importfehler or fehler_24h or erreichbar is False else "ok")
    empfehlungen = []
    if fehlt:
        empfehlungen.append("Erstimport ausfuehren: python -m app.cli osm-gebaeude-import --erzwingen")
    if pruefung:
        empfehlungen.append("Lauf in Pruefung sichten: osm-gebaeude-status / osm-gebaeude-aktivieren <id>")
    if veraltet:
        empfehlungen.append("Bestand veraltet: Import pruefen")
    if importfehler:
        empfehlungen.append("Letzten Importfehler pruefen: python -m app.cli osm-gebaeude-status")
    if erreichbar is False or fehler_24h:
        empfehlungen.append("Overpass oeffentlich nicht nutzbar - nur Rueckfall; lokale Quelle fuer Region ergaenzen")
    evidenz.update(overall_status=status, recommendations=empfehlungen)
    return {"status": status, "evidence": evidenz}
