"""Taeglicher Sync des Gefahrgut-Datensatzes (BAM/ADR, dl-de).

Laedt die vollstaendige, ;-getrennte Gefahrgut-CSV von einer konfigurierten Quelle
(settings.NACHSCHLAGEWERK_GEFAHRGUT_URL) und legt sie atomar unter
NACHSCHLAGEWERK_DATA_DIR/bam_gefahrgut.csv ab. gefahrgut_service bevorzugt diese
Datei vor dem gebuendelten Seed (siehe gefahrgut_service._csv_pfad()), sodass die
Offline-Nutzung erhalten bleibt.

Kein DB-Zugriff -> reine Datei-/Netzarbeit, daher kein asyncio.to_thread noetig.
Best-effort: Fehler werden geloggt, der bestehende Datenstand bleibt unangetastet.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.config import settings
from app.services import gefahrgut_service
from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.nachschlagewerk")

_SYNC_HOUR = 3
_SYNC_MINUTE = 0
# Plausibilitaets-Untergrenze: weniger Zeilen -> Quelle vermutlich kaputt, nicht uebernehmen.
_MIN_ROWS = 50
_HTTP_TIMEOUT_S = 30.0

try:
    from zoneinfo import ZoneInfo
    _VIENNA_TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover
    _VIENNA_TZ = UTC  # type: ignore[assignment]


def _ziel_pfad() -> Path:
    return Path(settings.NACHSCHLAGEWERK_DATA_DIR) / "bam_gefahrgut.csv"


def _seconds_until_next(hour: int, minute: int) -> float:
    """Sekunden bis zum naechsten Zeitpunkt hour:minute in Europe/Vienna."""
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _valide_csv(text: str) -> int:
    """Prueft die heruntergeladene CSV; gibt die Zahl gueltiger UN-Zeilen zurueck (0 = ungueltig)."""
    if not text or ";" not in text:
        return 0
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader, [])
    if gefahrgut_service._finde_spalte(header, "un") < 0:
        return 0
    idx_un = gefahrgut_service._finde_spalte(header, "un")
    n = 0
    for row in reader:
        if idx_un < len(row) and gefahrgut_service._norm_un(row[idx_un]):
            n += 1
    return n


def _decode(rohdaten: bytes) -> str:
    """Dekodiert CSV-Bytes tolerant (BAM liefert je nach Datei UTF-8 oder Windows-1252)."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return rohdaten.decode(enc)
        except UnicodeDecodeError:
            continue
    return rohdaten.decode("utf-8", errors="ignore")


def _ist_zip(rohdaten: bytes) -> bool:
    """ZIP-Magic-Bytes (PK\\x03\\x04, auch leere/gespannte Archive PK\\x05/\\x07)."""
    return rohdaten[:2] == b"PK" and len(rohdaten) > 4


def _extrahiere_csv(rohdaten: bytes) -> str | None:
    """Liefert den CSV-Text aus der Antwort.

    - Direkte CSV -> dekodieren.
    - ZIP (z. B. BAM-Download mit BAM-Gefahrgutdaten.csv + -status.csv) -> das
      .csv-Member mit den MEISTEN gueltigen UN-Zeilen waehlen (das ist die
      Gefahrgutdaten-Datei, nicht die Status-Datei).
    """
    if not _ist_zip(rohdaten):
        return _decode(rohdaten)
    try:
        with zipfile.ZipFile(io.BytesIO(rohdaten)) as zf:
            beste_text: str | None = None
            beste_zahl = 0
            for name in zf.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                try:
                    text = _decode(zf.read(name))
                except Exception:
                    continue
                zahl = _valide_csv(text)
                if zahl > beste_zahl:
                    beste_zahl = zahl
                    beste_text = text
            if beste_text is None:
                logger.warning("Gefahrgut-Sync: ZIP enthaelt keine gueltige Gefahrgut-CSV.")
            return beste_text
    except zipfile.BadZipFile:
        logger.warning("Gefahrgut-Sync: Antwort sieht wie ZIP aus, ist aber kaputt.")
        return None


def _atomar_schreiben(text: str) -> Path:
    """Schreibt text atomar nach _ziel_pfad() (tmp -> os.replace im selben Verzeichnis)."""
    ziel = _ziel_pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ziel.parent), prefix=".bam_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, ziel)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return ziel


async def sync_gefahrgut() -> bool:
    """Laedt die Gefahrgut-CSV (oder ZIP) und ersetzt den lokalen Datensatz atomar.

    Direkte `;`-CSV wird unveraendert uebernommen; ein ZIP (z. B. der BAM-Download
    mit BAM-Gefahrgutdaten.csv) wird entpackt und das passende CSV-Member gewaehlt.
    Rueckgabe True bei erfolgreicher Uebernahme, sonst False (Datenstand bleibt).
    """
    url = (settings.NACHSCHLAGEWERK_GEFAHRGUT_URL or "").strip()
    if not url:
        logger.info("Gefahrgut-Sync: keine Quell-URL konfiguriert - es bleibt beim Seed.")
        return False
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Einsatzcockpit/2.x (+https://einsatzcockpit.com)"},
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            rohdaten = resp.content
    except httpx.TimeoutException:
        logger.warning("Gefahrgut-Sync: Timeout beim Abruf von %s", url)
        return False
    except httpx.HTTPStatusError as exc:
        logger.warning("Gefahrgut-Sync: HTTP %s von %s", exc.response.status_code, url)
        return False
    except Exception:
        logger.exception("Gefahrgut-Sync: Abruf fehlgeschlagen (%s)", url)
        return False

    text = _extrahiere_csv(rohdaten)
    if text is None:
        logger.warning("Gefahrgut-Sync: keine verwertbare CSV in der Antwort (%s).", url)
        return False

    n = _valide_csv(text)
    if n < _MIN_ROWS:
        logger.warning(
            "Gefahrgut-Sync: Antwort unplausibel (%d gueltige Zeilen < %d) - nicht uebernommen.",
            n, _MIN_ROWS)
        return False

    try:
        ziel = _atomar_schreiben(text)
    except Exception:
        logger.exception("Gefahrgut-Sync: Schreiben nach %s fehlgeschlagen", _ziel_pfad())
        return False

    gefahrgut_service.invalidate_cache()
    logger.info("Gefahrgut-Sync: %d Stoffe aktualisiert (%s).", n, ziel)
    return True


async def nachschlagewerk_sync_loop() -> None:
    """Taeglicher Loop (03:00 Europe/Vienna): synchronisiert den Gefahrgut-Datensatz."""
    if not settings.NACHSCHLAGEWERK_SYNC_ENABLED:
        logger.info("Nachschlagewerk-Sync deaktiviert (NACHSCHLAGEWERK_SYNC_ENABLED=false).")
        return
    if not (settings.NACHSCHLAGEWERK_GEFAHRGUT_URL or "").strip():
        logger.info("Nachschlagewerk-Sync: keine Quell-URL - Loop startet nicht.")
        return
    # Beim Start einmal synchronisieren (frischer Datenstand ohne bis 03:00 zu warten).
    try:
        await sync_gefahrgut()
    except Exception:
        logger.exception("Nachschlagewerk-Sync: initialer Lauf fehlgeschlagen")
    while True:
        try:
            await asyncio.sleep(_seconds_until_next(_SYNC_HOUR, _SYNC_MINUTE))
            with iteration_watch(logger, "nachschlagewerk_sync_loop", 3600):
                await sync_gefahrgut()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Nachschlagewerk-Sync: Iteration fehlgeschlagen")
