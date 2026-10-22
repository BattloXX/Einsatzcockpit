"""APP-01/APP-02: Web-Layer der Android-App (Pull-to-Refresh, Verbindungsstatus)."""
from __future__ import annotations

from pathlib import Path


def _text(pfad: str) -> str:
    return Path(pfad).read_text(encoding="utf-8")


def test_base_html_laedt_verbindungsstatus_und_sw_cached_ihn():
    assert "/static/js/conn-status.js" in _text("app/templates/base.html")
    assert "/static/js/conn-status.js" in _text("app/static/sw.js")


def test_native_bridge_stellt_pull_to_refresh_bereit():
    js = _text("app/static/js/native-bridge.js")
    assert "refresh," in js and "ptrBlocked," in js
    assert "window.ECPtr" in js and "setBlocked" in js
    assert "'ec:resync'" in js and "source: 'pull'" in js


def test_board_lage_und_einheit_abonnieren_resync_und_melden_handled():
    for pfad in ("app/static/js/app.js", "app/static/js/lage_board.js", "app/static/js/einheit_modus.js"):
        js = _text(pfad)
        assert "detail.handled = true" in js, pfad


def test_globale_websocket_hat_je_verbindung_genau_ein_ping_intervall():
    js = _text("app/static/js/app.js")
    assert "let pingTimer = null;" in js
    assert "clearInterval(pingTimer);" in js
    assert "setInterval(() => ws.readyState === 1 && ws.send('ping'), 30000);" in js
    assert "pingTimer = setInterval(" in js
    assert "ec:ws-state" in js


def test_einheit_reagiert_auf_pull_und_wiederverbindung_ohne_doppelte_ladevorgaenge():
    js = _text("app/static/js/einheit_modus.js")
    assert 'quelle !== "pull" && quelle !== "ws-reconnect"' in js
