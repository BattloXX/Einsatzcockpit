"""ECP-06: Karten-Detail-Modal darf die Oberflaeche nicht dauerhaft abdunkeln."""
from __future__ import annotations

from pathlib import Path


def _text(pfad: str) -> str:
    return Path(pfad).read_text(encoding="utf-8")


def test_karten_oeffnen_das_detail_modal_nicht_ueber_eigene_after_swap_handler():
    """Vorher oeffnete jeder Swap an/in einer Karte (hx-on::after-swap) das Modal; ein Swap-Ereignis eines
    Kindes oder einer WS-Aktualisierung konnte so ein leeres Modal samt Abdunklung erzeugen. Das Oeffnen
    laeuft nur noch zentral, wenn tatsaechlich in #cardDetailBody getauscht wurde."""
    for pfad in (
        "app/templates/incident/_task_card.html", "app/templates/incident/_message_card.html",
        "app/templates/incident/_person_card.html", "app/templates/incident/_vehicle_card.html",
        "app/templates/incident/_header_alarm.html",
    ):
        assert "cardDetailModal').showModal()" not in _text(pfad), pfad


def test_zentraler_opener_oeffnet_nie_leer_und_nie_doppelt():
    js = _text("app/static/js/app.js")
    assert "if (!modal || modal.open || !e.detail.target.textContent.trim()) return;" in js
    board = _text("app/static/js/incident-board.js")
    assert "if (modal && !modal.open) modal.showModal();" in board


def test_overlay_schutz_ist_eingebunden():
    assert "/static/js/overlay-guard.js" in _text("app/templates/base.html")
    js = _text("app/static/js/overlay-guard.js")
    for baustein in ("visibilitychange", "pageshow", "htmx:afterSettle", "dataset.dirty", "mobileMenuOpen"):
        assert baustein in js
