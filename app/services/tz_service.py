"""Taktische-Zeichen-Katalog: liest app/static/tz/tz-manifest.json (44 SVGs, HOAK/vfdb-Stil).

Wiederverwendet für die Lageführung-Symbolpalette (Phase 2), das Fahrzeugtyp→Zeichen-Mapping
und die PDF-Legende. Gleiche Quelle wie der bestehende GSL-Bildannotations-Editor
(app/static/js/annotate.js), dort aber clientseitig geladen — hier serverseitig für Admin-UI
und PDF-Export.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "static" / "tz" / "tz-manifest.json"


@lru_cache(maxsize=1)
def load_tz_manifest() -> dict:
    """Lädt den Zeichen-Katalog. Leeres Manifest bei Fehler (Palette bleibt dann leer, kein Crash)."""
    try:
        with open(_MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"symbole": [], "flaechen": []}


def list_tz_symbole(kat: set[str] | None = None) -> list[dict]:
    """Symbole aus dem Katalog, optional auf bestimmte Kategorien gefiltert."""
    symbole = load_tz_manifest().get("symbole", [])
    if kat is None:
        return symbole
    return [s for s in symbole if s.get("kat") in kat]


def tz_symbol_name(zeichen_key: str | None) -> str | None:
    """Anzeigename eines Zeichens (für Legenden/PDF), None wenn unbekannt/leer."""
    if not zeichen_key:
        return None
    for s in load_tz_manifest().get("symbole", []):
        if s.get("id") == zeichen_key:
            return s.get("name")
    return None
