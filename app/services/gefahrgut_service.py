"""Gefahrgut-Anreicherung per UN-Nummer aus offener Fachdatenbank (BAM, dl-de/by-2.0).

Offline-Lookup: die gebündelte CSV (`app/data/bam_gefahrgut.csv`) wird einmal lazy in ein
dict geladen. Kein Runtime-Fremddienst (Prod erreicht externe Dienste nicht immer). Zusätzlich
werden Deep-Links auf öffentliche Nachschlagewerke generiert (ERICard, BAM DGG-Info).

Quelle der Daten: „Datenbank GEFAHRGUT, BAM" (dl-de/by-2.0). Siehe app/data/LIZENZ.md.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

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


def _un_vierstellig(un: str | None) -> str:
    """UN-Nummer als 4-stellige Ziffernfolge (offizielles Format, z. B. UN 0335)."""
    ziffern = re.sub(r"\D", "", str(un or ""))
    if not ziffern:
        return ""
    return ziffern.zfill(4) if len(ziffern) < 4 else ziffern


def _hin_norm(gefahrnummer: str | None) -> str:
    """Gefahrnummer/Kemler als Hazard Identification Number (Ziffern, optional führendes X)."""
    roh = re.sub(r"[^0-9Xx]", "", str(gefahrnummer or "")).upper()
    # X darf nur am Anfang stehen (z. B. X423 = reagiert gefährlich mit Wasser)
    if roh.startswith("X"):
        return "X" + roh[1:].replace("X", "")
    return roh.replace("X", "")


def ericard_url(un: str | None, gefahrnummer: str | None = None) -> str | None:
    """Deep-Link in die CEFIC-ERICards-Datenbank (deutsche Notfall-Interventionskarte).

    Sprung direkt auf das Suchergebnis per UN-Nummer; die Gefahrnummer (Kemler = HIN)
    präzisiert die Karte, falls eine UN-Nummer mehrere HIN besitzt. ERICards geben
    Einsatzkräften die Sofortmaßnahmen beim Eintreffen (CEFIC, frei zugänglich).
    """
    un4 = _un_vierstellig(un)
    if not un4:
        return None
    url = (
        "https://www.ericards.net/psp/ericards.psp_search_result"
        f"?p_lang=3&lang=3&unnumber={un4}&operators=AND"
    )
    hin = _hin_norm(gefahrnummer)
    if hin:
        url += f"&hin={hin}"
    return url


def generierte_links(
    un: str | None,
    stoffname: str | None = None,
    gefahrnummer: str | None = None,
) -> list[dict]:
    """Deep-Links auf öffentliche Nachschlagewerke (nicht gespeichert, immer erzeugbar)."""
    links: list[dict] = []
    key = _norm_un(un)
    eri = ericard_url(un, gefahrnummer)
    if eri:
        # Zuerst: die einsatztaktisch wichtigste Karte (Sofortmaßnahmen für Einsatzkräfte)
        links.append({"label": f"🚒 ERICard UN {_un_vierstellig(un)}", "url": eri})
    if key:
        links.append({
            "label": f"BAM Gefahrgut UN {key}",
            "url": f"https://www.dgg.bam.de/dgginfo/search/query?value=UN{key}&partialWord=false",
        })
    return links
