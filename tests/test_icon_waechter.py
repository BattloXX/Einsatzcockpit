"""Wächter für das Icon-System: keine neuen UI-Emojis, keine fremden Icon-Bibliotheken.

Plan: docs/plans/icon-system-plan.md, Abschnitt 5. Zählung und Zuordnung stammen aus
scripts/icon_inventar.py; die Baseline tests/data/icon_emoji_baseline.txt darf nur schrumpfen.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("icon_inventar", ROOT / "scripts" / "icon_inventar.py")
assert _SPEC and _SPEC.loader
inventar = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventar)

REGEN = ".venv/bin/python scripts/icon_inventar.py --baseline"

FREMDE_BIBLIOTHEKEN = re.compile(
    r"material-symbols|material-icons|font-awesome|\bfa-(?:solid|regular|brands)\b"
    r"|bootstrap-icons|lucide|heroicons|ionicons|iconify",
    re.IGNORECASE,
)
# Nur nachgeladene Ressourcen (Skripte, Stylesheets, Fonts) – normale Links sind erlaubt.
CDN_URL = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']https?://|<link\b[^>]*\bhref\s*=\s*["']https?://"""
    r"""|url\(\s*["']?https?://|@import\s+(?:url\()?["']?https?://""",
    re.IGNORECASE,
)
# Bewusste Ausnahmen (Datei, Muster) mit Begründung.
CDN_AUSNAHMEN = {
    # Captcha-Dienst, funktioniert nur online
    ("app/templates/public/_pub_contact.html", "challenges.cloudflare.com/turnstile"),
}
ICON_ONLY = re.compile(
    r"<(button|a)\b([^>]*)>\s*\{\{\s*icon\(([^)]*)\)\s*\}\}\s*</\1>",
    re.DOTALL,
)


def _baseline() -> dict[str, int]:
    eintraege: dict[str, int] = {}
    for zeile in inventar.BASELINE.read_text(encoding="utf-8").splitlines():
        if zeile and not zeile.startswith("#"):
            pfad, anzahl = zeile.split("#", 1)[0].strip().rsplit(" ", 1)
            eintraege[pfad] = int(anzahl)
    return eintraege


def test_jede_ausnahme_ist_begruendet() -> None:
    ohne = sorted(set(_baseline()) - set(inventar.AUSNAHME_GRUENDE))
    assert not ohne, (
        "Verbliebene Emojis ohne Begründung – migrieren oder in AUSNAHME_GRUENDE "
        "(scripts/icon_inventar.py) begründen: " + ", ".join(ohne)
    )


def _eigene_frontend_dateien() -> list[Path]:
    dateien = list((ROOT / "app" / "templates").rglob("*.html"))
    for p in (ROOT / "app" / "static" / "js").glob("*.js"):
        if p.name not in inventar.VENDOR and not p.name.endswith(".min.js"):
            dateien.append(p)
    for p in (ROOT / "app" / "static" / "css").glob("*.css"):
        if p.name not in inventar.VENDOR and not p.name.endswith(".min.css") and p.name not in {
            "leaflet.min.css", "MarkerCluster.Default.css", "quill.snow.css",
        }:
            dateien.append(p)
    return sorted(dateien)


def test_alle_zeichen_sind_zugeordnet() -> None:
    fehlend = sorted(ch for ch in inventar.sammeln() if ch not in inventar.ZUORDNUNG)
    assert not fehlend, (
        "Neue Emoji-/Symbolzeichen ohne Zuordnung in scripts/icon_inventar.py (ZUORDNUNG): "
        + " ".join(fehlend)
    )


def test_keine_neuen_ui_emojis() -> None:
    ist = inventar.migrierbar_je_datei()
    soll = _baseline()
    mehr = {f: (n, soll.get(f, 0)) for f, n in ist.items() if n > soll.get(f, 0)}
    assert not mehr, (
        "Neue UI-Emojis – bitte {{ icon(...) }} / ecpIcon(...) verwenden "
        "(siehe CLAUDE.md, Icon-System): "
        + ", ".join(f"{f}: {n} statt {b}" for f, (n, b) in sorted(mehr.items()))
    )


def test_baseline_ist_aktuell() -> None:
    ist = inventar.migrierbar_je_datei()
    soll = _baseline()
    weniger = {f: (ist.get(f, 0), n) for f, n in soll.items() if ist.get(f, 0) < n}
    assert not weniger, (
        f"Emojis wurden migriert – Baseline verkleinern mit `{REGEN}`: "
        + ", ".join(f"{f}: {n} statt {b}" for f, (n, b) in sorted(weniger.items()))
    )


def test_keine_fremden_icon_bibliotheken_oder_cdns() -> None:
    treffer = []
    for p in _eigene_frontend_dateien():
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            rel = p.relative_to(ROOT).as_posix()
            if any(rel == d and m in zeile for d, m in CDN_AUSNAHMEN):
                continue
            if FREMDE_BIBLIOTHEKEN.search(zeile) or CDN_URL.search(zeile):
                treffer.append(f"{rel}:{nr}: {zeile.strip()[:120]}")
    assert not treffer, "Fremde Icon-Bibliothek oder externe Ressource:\n" + "\n".join(treffer)


def test_icon_buttons_haben_beschriftung() -> None:
    ohne = []
    for p in (ROOT / "app" / "templates").rglob("*.html"):
        for m in ICON_ONLY.finditer(p.read_text(encoding="utf-8")):
            attrs, args = m.group(2), m.group(3)
            if "aria-label" not in attrs and "label=" not in args:
                ohne.append(f"{p.relative_to(ROOT)}: {m.group(0)[:120]}")
    assert not ohne, (
        "Reine Icon-Buttons brauchen aria-label am Element oder label= am Icon:\n"
        + "\n".join(ohne)
    )
