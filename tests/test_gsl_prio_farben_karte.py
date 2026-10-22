"""GSL-02: Lagekarte nutzt dieselben Prioritaetsfarben wie das Board."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.major_incident import (
    SITE_PRIORITY_COLOR,
    SITE_PRIORITY_HEX,
    SitePhase,
    SitePriority,
    site_map_color,
)


def _site(prio, phase=SitePhase.neu if hasattr(SitePhase, "neu") else list(SitePhase)[0]):
    return SimpleNamespace(priority=prio, phase=phase)


@pytest.mark.parametrize("prio,erwartet", [
    (SitePriority.sofort, SITE_PRIORITY_HEX["red"]),
    (SitePriority.dringend, SITE_PRIORITY_HEX["orange"]),
    (SitePriority.normal, SITE_PRIORITY_HEX["yellow"]),
    (SitePriority.aufschiebbar, SITE_PRIORITY_HEX["muted"]),
])
def test_marker_farbe_folgt_prioritaet(prio, erwartet):
    assert site_map_color(_site(prio)) == erwartet


def test_board_und_karte_nutzen_dieselben_hexwerte():
    css = Path("app/static/css/tailwind.input.css").read_text(encoding="utf-8")
    assert "--red:      #d42225" in css and SITE_PRIORITY_HEX["red"] == "#d42225"
    assert "--orange:   #e67e22" in css and SITE_PRIORITY_HEX["orange"] == "#e67e22"
    assert "--yellow:   #f2b02e" in css and SITE_PRIORITY_HEX["yellow"] == "#f2b02e"
    assert set(SITE_PRIORITY_COLOR.values()) == {"red", "orange", "yellow", "muted"}


def test_erledigt_und_fertig_ohne_prioritaet():
    assert site_map_color(_site(None, SitePhase.erledigt)) == "#22c55e"
    assert site_map_color(_site(None, SitePhase.fertig_gemeldet)) == "#0d9488"


def test_router_hat_keine_eigenen_farbkopien_mehr():
    src = Path("app/routers/ui_major_incident.py").read_text(encoding="utf-8")
    # Sektor-Palette (SECTOR_COLORS) bleibt; Prioritaetsfarben nur ueber site_map_color
    assert '"#ef4444" if SITE_PRIORITY_COLOR' not in src
    assert 'return "#ef4444"' not in src and 'return "#f97316"' not in src


def test_karte_aktualisiert_bei_prio_aenderung_und_zeigt_legende():
    html = Path("app/templates/incident_major/karte.html").read_text(encoding="utf-8")
    assert "site_prio_changed" in html
    assert "Sofort" in html and "Aufschiebbar" in html
    assert "msg.type === 'site_created' && msg.site_id" not in html
