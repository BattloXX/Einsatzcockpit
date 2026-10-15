"""Jedes Modul im Menü *Module* braucht auch eine Kachel auf der Übersicht."""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
_HREF = re.compile(r'href="(/[^"]*)"')


def _abschnitt(text: str, start: str, ende: str) -> str:
    beginn = text.index(start)
    return text[beginn : text.index(ende, beginn)]


def test_jedes_modul_hat_eine_kachel():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    index = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    menue = set(_HREF.findall(_abschnitt(base, 'class="topnav__doku-panel"', "</div>")))
    kacheln = set(_HREF.findall(_abschnitt(index, '<div class="module-grid">', "</section>")))

    assert menue, "Modul-Menü in base.html nicht gefunden"
    fehlend = sorted(menue - kacheln)
    assert not fehlend, f"Module ohne Kachel in index.html: {fehlend}"
