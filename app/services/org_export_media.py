"""Medien-Referenzen fuer den Org-Export: (arcname, absoluter Pfad) je Tabelle.

Die Verzeichnis-/Pfadlogik spiegelt die jeweiligen Media-Services wider
(media_service, objekt_dokument_service, lage_media_service, verleih_service).
Nicht auffindbare Dateien werden beim Export uebersprungen (best-effort).

arcname-Schema: "<tabelle>/<pk>/<spalte>/<dateiname>" — traegt Tabelle, Zeile und
Spalte, sodass der Restore (PR 4) die Datei eindeutig der Zielzeile/-spalte zuordnen
und an den dort neu berechneten Pfad zuruecklegen kann.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("einsatzleiter.org_media")


def _sicher(root: Path, value: str) -> Path | None:
    """Auch Symlinks duerfen den Medien-Wurzelpfad nicht verlassen."""
    try:
        rel = Path(value)
        ziel = (root / rel).resolve()
        if rel.is_absolute() or ".." in rel.parts or "\\" in value:
            return None
        if not ziel.is_relative_to(root.resolve()) or ziel == root.resolve():
            return None
        return ziel
    except (OSError, ValueError, RuntimeError):
        return None


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


def _relativ(root_fn: Callable[[], Path], cols: list[str]) -> Callable[[dict], list[tuple[str, Path | None]]]:
    """Spalte enthaelt einen zum Basisverzeichnis relativen Pfad (root/value)."""
    def resolve(row: dict) -> list[tuple[str, Path | None]]:
        root = root_fn()
        out: list[tuple[str, Path | None]] = []
        for c in cols:
            v = row.get(c)
            if v:
                out.append((c, _sicher(root, str(v))))
        return out
    return resolve


def _zusammengesetzt(dir_const: str, parent_col: str,
                    cols: list[str]) -> Callable[[dict], list[tuple[str, Path | None]]]:
    """Pfad = {dir}/{org_id}/{parent_id}/{dateiname} (GSL-/Verleih-Medien)."""
    def resolve(row: dict) -> list[tuple[str, Path | None]]:
        oid, pid = row.get("org_id"), row.get(parent_col)
        if oid is None or pid is None:
            return []
        root = Path(dir_const)
        base = Path(str(oid)) / str(pid)
        out: list[tuple[str, Path | None]] = []
        for c in cols:
            v = row.get(c)
            if v:
                name = Path(str(v)).name
                valid = name == str(v) and name not in {".", ".."} and "\\" not in name
                out.append((c, _sicher(root, str(base / name)) if valid else None))
        return out
    return resolve


_RESOLVERS: dict[str, Callable[[dict], list[tuple[str, Path | None]]]] = {
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


def medien_referenzen(tabelle: str, zeilen: list[dict],
                      skipped: list[str] | None = None) -> list[tuple[str, Path]]:
    """Liefert (arcname, absoluter Pfad) fuer alle Mediendateien der Zeilen."""
    resolve = _RESOLVERS.get(tabelle)
    if resolve is None:
        return []
    referenzen: list[tuple[str, Path]] = []
    for row in zeilen:
        pk = row.get("id")
        for col, abs_path in resolve(row):
            if abs_path is None:
                logger.warning("Unzulaessiger Medienpfad uebersprungen (Tabelle %s, Spalte %s)", tabelle, col)
                if skipped is not None:
                    skipped.append(col)
                continue
            arcname = f"{tabelle}/{pk}/{col}/{abs_path.name}"
            referenzen.append((arcname, abs_path))
    return referenzen
