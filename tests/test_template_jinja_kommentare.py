"""Regression: CSS-ID-Selektoren direkt nach '{' öffnen in Jinja einen Kommentar.

Vorfall 2026-10-12: `@media (...){#siteDetailModal{...}` in incident_major/board.html
hat ab `{#` alles bis zum nächsten `#}` verschluckt – inklusive `</style>`. Der
Druck-Dialog landete als Text im <style>, `ecpgTgtStation` war null.
"""
import re
from pathlib import Path

from app.core.security import sign_session
from tests.test_einheit_api import _daten as _api_daten
from tests.test_gsl_fuehrung_einheiten import _als, _mit_rolle

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def test_jinja_kommentare_beginnen_mit_leerzeichen():
    # Echte Kommentare schreiben wir `{# ... #}` bzw. `{#- ... #}`; alles andere ist
    # fast immer CSS (`{#id`) oder JS (`{#privat`) und frisst Markup.
    verdaechtig = re.compile(r"\{#(?![\s-])")
    treffer = [
        f"{pfad.relative_to(TEMPLATES)}:{text[:m.start()].count(chr(10)) + 1}"
        for pfad in TEMPLATES.rglob("*.html")
        for text in [pfad.read_text(encoding="utf-8")]
        for m in verdaechtig.finditer(text)
    ]
    assert not treffer, treffer


def test_gsl_board_rendert_druckdialog_ausserhalb_von_style(client):
    data = _api_daten()
    _als(client, sign_session(_mit_rolle(data["admin"], "incident_leader")))
    html = client.get(f"/lage/{data['lage']}").text
    assert 'id="ecpgTgtStation"' in html
    for style in re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S):
        assert "<dialog" not in style and "ecpgTgtStation" not in style
