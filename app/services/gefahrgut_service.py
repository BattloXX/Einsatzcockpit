"""Gefahrgut-Anreicherung per UN-Nummer aus offener Fachdatenbank (BAM, dl-de/by-2.0).

Offline-Lookup: die gebündelte CSV (`app/data/bam_gefahrgut.csv`) wird einmal lazy in ein
dict geladen. Kein Runtime-Fremddienst (Prod erreicht externe Dienste nicht immer). Zusätzlich
werden Deep-Links auf öffentliche Nachschlagewerke generiert (BAM DGG-Info, GESTIS).

Quelle der Daten: „Datenbank GEFAHRGUT, BAM" (dl-de/by-2.0). Siehe app/data/LIZENZ.md.
GESTIS erlaubt nur Verlinkung (keine Massenübernahme) → nur als generierter Link.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger("einsatzleiter.gefahrgut")

_CSV_PFAD = Path(__file__).resolve().parent.parent / "data" / "bam_gefahrgut.csv"
_DATEN: dict[str, dict] | None = None


def _norm_un(un: str | None) -> str:
    """Normiert eine UN-Nummer: nur Ziffern, führende Nullen entfernt."""
    if not un:
        return ""
    ziffern = re.sub(r"\D", "", str(un))
    return ziffern.lstrip("0") or ziffern


def _finde_spalte(header: list[str], *schluessel: str) -> int:
    """Erste Spalte, deren Kopf einen der Schlüssel enthält (case-insensitive)."""
    for i, h in enumerate(header):
        hl = h.strip().lower()
        for s in schluessel:
            if s in hl:
                return i
    return -1


def _lade_daten() -> dict[str, dict]:
    """Lädt die CSV lazy in ein dict {norm_un: felder}. Robust gegen fehlende Datei."""
    global _DATEN
    if _DATEN is not None:
        return _DATEN
    daten: dict[str, dict] = {}
    if not _CSV_PFAD.exists():
        logger.info("Gefahrgut-CSV nicht vorhanden (%s) — Anreicherung deaktiviert", _CSV_PFAD)
        _DATEN = daten
        return daten
    try:
        with _CSV_PFAD.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter=";")
            header = next(reader, [])
            idx_un = _finde_spalte(header, "un")
            idx_name = _finde_spalte(header, "benennung", "stoff", "bezeichnung", "name")
            idx_klasse = _finde_spalte(header, "klasse")
            idx_code = _finde_spalte(header, "klassifiz", "code")
            idx_gnr = _finde_spalte(header, "gefahrnummer", "kemler", "gefahrnr")
            idx_vg = _finde_spalte(header, "verpack")
            if idx_un < 0:
                logger.warning("Gefahrgut-CSV ohne UN-Spalte — Anreicherung deaktiviert")
                _DATEN = daten
                return daten

            def _get(row: list[str], i: int) -> str | None:
                if 0 <= i < len(row):
                    wert = row[i].strip()
                    return wert or None
                return None

            for row in reader:
                key = _norm_un(_get(row, idx_un))
                if not key:
                    continue
                daten[key] = {
                    "un_nummer": (_get(row, idx_un) or "").strip(),
                    "stoffname": _get(row, idx_name),
                    "klasse": _get(row, idx_klasse),
                    "klassifizierungscode": _get(row, idx_code),
                    "gefahrnummer": _get(row, idx_gnr),
                    "verpackungsgruppe": _get(row, idx_vg),
                }
    except Exception:
        logger.exception("Gefahrgut-CSV konnte nicht gelesen werden")
    _DATEN = daten
    return daten


def lookup_un(un: str | None) -> dict | None:
    """Stoffinfos zu einer UN-Nummer oder None."""
    key = _norm_un(un)
    if not key:
        return None
    return _lade_daten().get(key)


def generierte_links(un: str | None, stoffname: str | None = None) -> list[dict]:
    """Deep-Links auf öffentliche Nachschlagewerke (nicht gespeichert, immer erzeugbar)."""
    links: list[dict] = []
    key = _norm_un(un)
    if key:
        links.append({
            "label": f"BAM Gefahrgut UN {key}",
            "url": f"https://www.dgg.bam.de/dgginfo/search/query?value=UN{key}&partialWord=false",
        })
    if stoffname:
        links.append({
            "label": f"GESTIS: {stoffname}",
            "url": f"https://gestis.dguv.de/search?query={quote(stoffname)}",
        })
    return links
