"""Import globaler Gebaeude-Ways; Multipolygon-Relationen bleiben bewusst aussen vor."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import math
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
import osmium
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.config import settings
from app.db import SessionLocal
from app.models.osm import OsmGebaeude, OsmImportLauf
from app.services.loop_utils import iteration_watch

logger = logging.getLogger(__name__)
_BATCH = 5000


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _gueltig(name: str, url: str) -> bool:
    try:
        teile = urlsplit(url)
        return bool(re.fullmatch(r"[a-z0-9_-]{1,40}", name) and len(url) <= 500
                    and teile.scheme == "https" and teile.hostname and not teile.username and not teile.password)
    except ValueError:
        return False


def quellen() -> list[tuple[str, str]]:
    ergebnis = {}
    for eintrag in settings.OSM_GEBAEUDE_QUELLEN.split(","):
        name, _, url = eintrag.strip().partition("=")
        if not _gueltig(name, url) or name in ergebnis:
            logger.warning("OSM-Quellenkonfiguration: ungueltiger oder doppelter Eintrag ignoriert")
            continue
        ergebnis[name] = url
    return list(ergebnis.items())


@contextmanager
def _quellen_sperre(name: str):
    # CLI und Leader verwenden dieselbe Sperre, auch beim allerersten Import.
    verzeichnis = Path(settings.OSM_GEBAEUDE_DATA_DIR)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    with (verzeichnis / f"{name}.lock").open("a") as datei:
        fcntl.flock(datei, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(datei, fcntl.LOCK_UN)


class _ImportFehler(Exception):
    def __init__(self, code: str, text: str):
        super().__init__(text)
        self.code = code


def _http_datum(text: str | None) -> datetime | None:
    if text:
        try:
            return parsedate_to_datetime(text).astimezone(UTC).replace(tzinfo=None)
        except (ValueError, TypeError, OverflowError):
            logger.warning("OSM-Download: ungueltiges Last-Modified ignoriert")
    return None


def _download(name: str, url: str, datenstand: datetime | None) -> tuple[Path | None, datetime | None]:
    ziel = Path(settings.OSM_GEBAEUDE_DATA_DIR) / f"{name}.osm.pbf"
    temporaer = ziel.with_suffix(".pbf.tmp")
    headers = {"User-Agent": settings.HYDRANT_USER_AGENT}
    if datenstand is not None:
        headers["If-Modified-Since"] = format_datetime(datenstand.replace(tzinfo=UTC), usegmt=True)
    grenze = settings.OSM_GEBAEUDE_MAX_DOWNLOAD_MB * 1024 * 1024
    try:
        with httpx.Client(headers=headers, timeout=120, follow_redirects=False) as client:
            for _ in range(6):
                with client.stream("GET", url) as antwort:
                    if antwort.status_code in (301, 302, 303, 307, 308):
                        ziel_url = urljoin(url, antwort.headers.get("location", ""))
                        if not _gueltig(name, ziel_url) or ziel_url == url:
                            raise _ImportFehler("OSM_DOWNLOAD_HTTP", "Ungueltige HTTPS-Weiterleitung")
                        url = ziel_url
                        continue
                    if antwort.status_code == 304:
                        return None, datenstand
                    antwort.raise_for_status()
                    if int(antwort.headers.get("content-length", "0")) > grenze:
                        raise _ImportFehler("OSM_DOWNLOAD_ZU_GROSS", "OSM-Download ueberschreitet Groessenlimit")
                    groesse = 0
                    with temporaer.open("wb") as datei:
                        for teil in antwort.iter_bytes():
                            groesse += len(teil)
                            if groesse > grenze:
                                raise _ImportFehler(
                                    "OSM_DOWNLOAD_ZU_GROSS", "OSM-Download ueberschreitet Groessenlimit",
                                )
                            datei.write(teil)
                    temporaer.replace(ziel)
                    return ziel, _http_datum(antwort.headers.get("last-modified"))
            raise _ImportFehler("OSM_DOWNLOAD_HTTP", "Zu viele HTTPS-Weiterleitungen")
    except httpx.TimeoutException as exc:
        raise _ImportFehler("OSM_DOWNLOAD_TIMEOUT", "Zeitlimit beim OSM-Download") from exc
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise _ImportFehler("OSM_DOWNLOAD_HTTP", "OSM-Download fehlgeschlagen") from exc
    finally:
        temporaer.unlink(missing_ok=True)


def _aktiver(db: Session, name: str) -> OsmImportLauf | None:
    # Kein FOR UPDATE: Alle Schreiber einer Quelle laufen unter _quellen_sperre (Dateisperre).
    return db.scalar(select(OsmImportLauf).where(
        OsmImportLauf.quelle == name, OsmImportLauf.status == "aktiv",
    ))


def _zeilen_loeschen(db: Session, lauf_id: int) -> None:
    while True:
        ids = list(db.scalars(select(OsmGebaeude.id).where(OsmGebaeude.lauf_id == lauf_id).limit(_BATCH)))
        if not ids:
            break
        db.execute(delete(OsmGebaeude).where(OsmGebaeude.lauf_id == lauf_id, OsmGebaeude.id.in_(ids)))
        db.commit()


def _aufraeumen(db: Session, name: str) -> None:
    ersetzt = list(db.scalars(select(OsmImportLauf).where(
        OsmImportLauf.quelle == name, OsmImportLauf.status == "ersetzt",
    ).order_by(OsmImportLauf.id.desc())))
    for i, lauf in enumerate(ersetzt):
        _zeilen_loeschen(db, lauf.id)
        if i:
            db.delete(lauf)
    # Nur der neueste Lauf in Pruefung behaelt seine Zeilen; sonst legt jeder
    # taegliche Versuch bei grosser Aenderung einen weiteren Vollbestand an.
    pruefung = list(db.scalars(select(OsmImportLauf).where(
        OsmImportLauf.quelle == name, OsmImportLauf.status == "pruefung",
    ).order_by(OsmImportLauf.id.desc())))
    for lauf in pruefung[1:]:
        _zeilen_loeschen(db, lauf.id)
        lauf.status = "verworfen"
    db.commit()


def _umschalten(db: Session, lauf: OsmImportLauf) -> None:
    alt = _aktiver(db, lauf.quelle)
    if alt is not None:
        alt.status = "ersetzt"
    lauf.status = "aktiv"
    db.commit()  # Beide Statuswechsel sind eine Transaktion.


def _einlesen(db: Session, lauf: OsmImportLauf, pfad: Path) -> None:
    with osmium.io.Reader(str(pfad), osmium.osm.NOTHING) as leser:
        datum = leser.header().get("osmosis_replication_timestamp")
    if datum:
        lauf.datenstand = datetime.fromisoformat(datum.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    prozessor = (osmium.FileProcessor(str(pfad)).with_locations("flex_mem")
                 .with_filter(osmium.filter.KeyFilter("building")))
    batch = []
    for way in prozessor:
        if not isinstance(way, osmium.osm.Way) or not way.is_closed():
            continue
        try:
            ring = [[round(n.lat, 7), round(n.lon, 7)] for n in way.nodes]
        except osmium.InvalidLocationError:
            continue
        if (len(ring) < 4 or ring[0] != ring[-1] or len({tuple(p) for p in ring[:-1]}) < 3
                or not all(math.isfinite(x) for p in ring for x in p)):
            continue
        ecken = ring[:-1]
        text = json.dumps(ecken, separators=(",", ":"))
        zeile: dict = {"lauf_id": lauf.id, "osm_way_id": way.id, "ecken_json": text,
                 "ecken_hash": hashlib.sha256(text.encode()).hexdigest(),
                 "min_lat": min(p[0] for p in ecken), "max_lat": max(p[0] for p in ecken),
                 "min_lng": min(p[1] for p in ecken), "max_lng": max(p[1] for p in ecken)}
        for feld in ("min_lat", "max_lat", "min_lng", "max_lng"):
            bisher = getattr(lauf, feld)
            wert = zeile[feld]
            wert = wert if bisher is None else (min(bisher, wert) if feld.startswith("min") else max(bisher, wert))
            setattr(lauf, feld, wert)
        lauf.anzahl += 1
        batch.append(zeile)
        if len(batch) >= _BATCH:
            db.execute(insert(OsmGebaeude), batch)
            db.commit()
            batch.clear()
    if batch:
        db.execute(insert(OsmGebaeude), batch)
    db.commit()


def _vergleichen(db: Session, lauf: OsmImportLauf, alt: OsmImportLauf | None) -> None:
    if alt is None:
        lauf.neu = lauf.anzahl
        return
    a, b = aliased(OsmGebaeude), aliased(OsmGebaeude)
    gemeinsam = select(func.count()).select_from(a).join(
        b, (a.osm_way_id == b.osm_way_id) & (b.lauf_id == alt.id),
    ).where(a.lauf_id == lauf.id)
    zahl = db.scalar(gemeinsam) or 0
    lauf.neu = lauf.anzahl - zahl
    lauf.entfernt = alt.anzahl - zahl
    lauf.geaendert = db.scalar(gemeinsam.where(a.ecken_hash != b.ecken_hash)) or 0


def importieren(name: str, url: str, *, erzwingen: bool = False) -> OsmImportLauf:
    if not _gueltig(name, url):
        raise ValueError("Ungueltige OSM-Quelle")
    with _quellen_sperre(name), SessionLocal() as db:
        db.expire_on_commit = False
        alt = _aktiver(db, name)
        if (not erzwingen and alt is not None and alt.beendet_am is not None
                and _jetzt() - alt.beendet_am < timedelta(days=settings.OSM_GEBAEUDE_AKTUALISIERUNG_TAGE)):
            alt.__dict__["ergebnis"] = "uebersprungen"
            return alt
        db.commit()  # Keine DB-Transaktion waehrend des Downloads offen halten.
        lauf = None
        lauf_id = None
        gestartet = _jetzt()
        code = "OSM_DOWNLOAD_HTTP"
        try:
            pfad, datum = _download(name, url, alt.datenstand if alt else None)
            if pfad is None:
                if alt is None:
                    raise _ImportFehler("OSM_DOWNLOAD_HTTP", "304 ohne aktiven Bestand")
                alt.__dict__["ergebnis"] = "unveraendert"
                return alt
            lauf = OsmImportLauf(quelle=name, url=url, status="laeuft", gestartet_am=gestartet, datenstand=datum,
                                 anzahl=0, neu=0, geaendert=0, entfernt=0)
            db.add(lauf)
            db.commit()
            lauf_id = lauf.id
            code = "OSM_PARSE_FEHLER"
            _einlesen(db, lauf, pfad)
            code = "OSM_DB_FEHLER"
            _vergleichen(db, lauf, alt)
            lauf.beendet_am = _jetzt()
            grenze = settings.OSM_GEBAEUDE_MAX_AENDERUNG_ANTEIL
            if alt is not None and ((lauf.entfernt + lauf.geaendert) / max(1, alt.anzahl) > grenze
                                    or (alt.anzahl - lauf.anzahl) / max(1, alt.anzahl) > grenze):
                lauf.status = "pruefung"
                db.commit()
                logger.warning("OSM-Import %s: Lauf %s braucht manuelle Pruefung", name, lauf.id)
            else:
                _umschalten(db, lauf)
        except Exception as exc:
            db.rollback()
            if isinstance(exc, _ImportFehler):
                code = exc.code
            elif isinstance(exc, SQLAlchemyError):
                code = "OSM_DB_FEHLER"
            lauf = db.get(OsmImportLauf, lauf_id) if lauf_id is not None else None
            if lauf is None:
                lauf = OsmImportLauf(quelle=name, url=url, gestartet_am=gestartet,
                                     anzahl=0, neu=0, geaendert=0, entfernt=0)
                db.add(lauf)
            lauf.status = "fehler"
            lauf.beendet_am = _jetzt()
            lauf.fehler_code = code
            lauf.fehler_text = f"OSM-Import fehlgeschlagen ({code})"
            db.commit()
            _zeilen_loeschen(db, lauf.id)
            logger.warning("OSM-Import %s fehlgeschlagen: %s", name, code)
        # Aufraeumfehler duerfen einen bereits aktivierten Lauf nicht auf fehler setzen.
        try:
            _aufraeumen(db, name)
        except SQLAlchemyError:
            db.rollback()
            logger.exception("OSM-Import %s: Aufraeumen fehlgeschlagen", name)
        return lauf


def aktivieren(lauf_id: int) -> OsmImportLauf:
    with SessionLocal() as db:
        db.expire_on_commit = False
        lauf = db.get(OsmImportLauf, lauf_id)
        if lauf is None:
            raise ValueError("OSM-Importlauf nicht gefunden")
        db.commit()  # Vor dem Warten keinen alten MariaDB-Snapshot festhalten.
        with _quellen_sperre(lauf.quelle):
            db.refresh(lauf)  # Frischer Snapshot nach commit; serialisiert durch die Dateisperre.
            if lauf.status != "pruefung":
                raise ValueError("Nur OSM-Importlaeufe in Pruefung koennen aktiviert werden")
            _umschalten(db, lauf)
            _aufraeumen(db, lauf.quelle)
        return lauf


def lauf_info(lauf: OsmImportLauf) -> dict:
    datum = lauf.datenstand
    return {"id": lauf.id, "status": lauf.status, "anzahl": lauf.anzahl,
            "neu": lauf.neu, "geaendert": lauf.geaendert, "entfernt": lauf.entfernt,
            "datenstand": datum.isoformat() + "Z" if datum else None,
            "alter_tage": max(0, (_jetzt() - datum).days) if datum else None,
            "fehler_code": lauf.fehler_code, "fehler_text": lauf.fehler_text}


def status() -> list[dict]:
    with SessionLocal() as db:
        laeufe = list(db.scalars(select(OsmImportLauf).order_by(OsmImportLauf.id.desc())))
        namen = dict.fromkeys([name for name, _ in quellen()] + [lauf.quelle for lauf in laeufe])
        return [{"quelle": name,
                 "aktiv": next((lauf_info(lauf) for lauf in laeufe
                                if lauf.quelle == name and lauf.status == "aktiv"), None),
                 "letzter_fehler": next((lauf_info(lauf) for lauf in laeufe
                                          if lauf.quelle == name and lauf.status == "fehler"), None),
                 "pruefung": [lauf_info(lauf) for lauf in laeufe if lauf.quelle == name and lauf.status == "pruefung"]}
                for name in namen]


def _sekunden_bis_0330() -> float:
    jetzt = datetime.now(ZoneInfo("Europe/Vienna"))
    ziel = jetzt.replace(hour=3, minute=30, second=0, microsecond=0)
    if ziel <= jetzt:
        ziel += timedelta(days=1)
    return (ziel.astimezone(UTC) - jetzt.astimezone(UTC)).total_seconds()


async def osm_gebaeude_loop() -> None:
    if not settings.OSM_GEBAEUDE_IMPORT_ENABLED or not quellen():
        return
    await asyncio.sleep(120)
    while True:
        with iteration_watch(logger, "osm_gebaeude_loop", 86400):
            for name, url in quellen():
                try:
                    await asyncio.to_thread(importieren, name, url)
                except Exception:
                    logger.exception("OSM-Import %s: Hintergrundlauf fehlgeschlagen", name)
        await asyncio.sleep(_sekunden_bis_0330())
