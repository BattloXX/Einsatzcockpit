"""ECP-03 / GSL-05: Spaltentooltip und Status-eintragen-Button."""
from __future__ import annotations

from pathlib import Path


def _text(pfad: str) -> str:
    return Path(pfad).read_text(encoding="utf-8")


def test_spaltentitel_und_tabs_nutzen_die_tooltip_komponente_nur_bei_kuerzung():
    kopf = _text("app/templates/incident/_lane_header.html")
    assert 'data-tip-bei-kuerzung style="flex:1;min-width:0;display:flex;"><span class="kanban-col__title">' in kopf
    assert "data-tip-bei-kuerzung>{{ col.title }}</button>" in _text("app/templates/incident/board.html")
    assert "/static/js/titel-tooltips.js" in _text("app/templates/base.html")


def test_status_eintragen_ist_ein_grosser_haupt_button_ohne_doppelsendung():
    html = _text("app/templates/incident_major/_site_detail.html")
    zeile = next(z for z in html.splitlines() if "Status eintragen</button>" in z)
    assert 'class="btn btn--primary"' in zeile and "min-height:44px" in zeile
    assert "btn--xs" not in zeile
    assert "hx-disabled-elt=\"find button[type='submit']\"" in html
    assert "addToast('Status eingetragen'" in html
