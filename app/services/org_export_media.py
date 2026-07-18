"""Medien-Referenzen fuer den Org-Export: (arcname, absoluter Pfad) je Tabelle.

Die Verzeichnis-/Pfadlogik spiegelt die jeweiligen Media-Services wider
(media_service, objekt_dokument_service, lage_media_service, verleih_service).
Nicht auffindbare Dateien werden beim Export uebersprungen (best-effort).

arcname-Schema: "<tabelle>/<pk>/<spalte>/<dateiname>" — traegt Tabelle, Zeile und
Spalte, sodass der Restore (PR 4) die Datei eindeutig der Zielzeile/-spalte zuordnen
und an den dort neu berechneten Pfad zuruecklegen kann.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

# Verzeichnis-Konstanten (Quelle: lage_media_service.py / verleih_service.py).
_LAGE_MEDIA_DIR = "app_storage/lage_media"
_JOURNAL_MEDIA_DIR = "app_storage/lage_journal_media"
_CROSS_MEDIA_DIR = "app_storage/cross_marker_media"
_VERLEIH_FOTO_DIR = "app_storage/verleih_fotos"


def _incident_root() -> Path:
    from app.config import settings
    return Path(settings.MEDIA_STORAGE_DIR)


def _objekt_root() -> Path:
    from app.config import settings
    return Path(settings.OBJEKT_MEDIA_DIR)


def _relativ(root_fn: Callable[[], Path], cols: list[str]) -> Callable[[dict], list[tuple[str, Path]]]:
    """Spalte enthaelt einen zum Basisverzeichnis relativen Pfad (root/value)."""
    def resolve(row: dict) -> list[tuple[str, Path]]:
        root = root_fn()
        out: list[tuple[str, Path]] = []
        for c in cols:
            v = row.get(c)
            if v:
                out.append((c, root / str(v)))
        return out
    return resolve


def _zusammengesetzt(dir_const: str, parent_col: str, cols: list[str]) -> Callable[[dict], list[tuple[str, Path]]]:
    """Pfad = {dir}/{org_id}/{parent_id}/{dateiname} (GSL-/Verleih-Medien)."""
    def resolve(row: dict) -> list[tuple[str, Path]]:
        oid, pid = row.get("org_id"), row.get(parent_col)
        if oid is None or pid is None:
            return []
        base = Path(dir_const) / str(oid) / str(pid)
        out: list[tuple[str, Path]] = []
        for c in cols:
            v = row.get(c)
            if v:
                out.append((c, base / str(v)))
        return out
    return resolve


_RESOLVERS: dict[str, Callable[[dict], list[tuple[str, Path]]]] = {
    # MEDIA_STORAGE_DIR-relativ
    "task_media": _relativ(_incident_root, ["storage_path", "thumb_path"]),
    "message_media": _relativ(_incident_root, ["storage_path", "thumb_path"]),
    "person_media": _relativ(_incident_root, ["storage_path", "thumb_path"]),
    "fahrt_media": _relativ(_incident_root, ["storage_path", "thumb_path"]),
    "uas_medien": _relativ(_incident_root, ["dateipfad", "thumb_path"]),
    # OBJEKT_MEDIA_DIR-relativ
    "objekt_symbol": _relativ(_objekt_root, ["bild_pfad"]),
    "objekt_dokument": _relativ(_objekt_root, ["pfad"]),
    "objekt_dokument_seite": _relativ(_objekt_root, ["einzel_pdf_pfad", "bild_pfad", "thumb_pfad"]),
    # Zusammengesetzte Pfade (org_id/parent_id/dateiname)
    "site_media": _zusammengesetzt(_LAGE_MEDIA_DIR, "incident_site_id", ["stored_filename"]),
    "lage_journal_media": _zusammengesetzt(_JOURNAL_MEDIA_DIR, "journal_entry_id", ["stored_filename"]),
    "cross_marker_media": _zusammengesetzt(_CROSS_MEDIA_DIR, "marker_id", ["stored_filename"]),
    "verleih_foto": _zusammengesetzt(_VERLEIH_FOTO_DIR, "ausleihe_id", ["stored_filename"]),
}

# Tabellen mit Mediendateien (fuer den Coverage-/Vollstaendigkeitstest).
MEDIA_TABLES = frozenset(_RESOLVERS)


def medien_referenzen(tabelle: str, zeilen: list[dict]) -> list[tuple[str, Path]]:
    """Liefert (arcname, absoluter Pfad) fuer alle Mediendateien der Zeilen."""
    resolve = _RESOLVERS.get(tabelle)
    if resolve is None:
        return []
    referenzen: list[tuple[str, Path]] = []
    for row in zeilen:
        pk = row.get("id")
        for col, abs_path in resolve(row):
            arcname = f"{tabelle}/{pk}/{col}/{abs_path.name}"
            referenzen.append((arcname, abs_path))
    return referenzen
