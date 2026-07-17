"""Nachschlagewerke PR 6: Evakuierungsradius (Service-Zonen + Feature-Typ + JS)."""
from pathlib import Path

from app.models.lagefuehrung import LAGEFUEHRUNG_FEATURE_TYPEN
from app.services import evakuierung_service as ev

JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "lagefuehrung.js"


# ── Feature-Typ ───────────────────────────────────────────────────────────────

def test_feature_typ_registriert():
    assert "gefahrenradius" in LAGEFUEHRUNG_FEATURE_TYPEN
    assert "ausbreitung" in LAGEFUEHRUNG_FEATURE_TYPEN


# ── Zonen-Service ─────────────────────────────────────────────────────────────

def test_preset_klein_und_gross():
    z = ev.zonen("klein")
    assert len(z) == 1 and z[0]["radius_m"] == 50 and z[0]["rolle"] == "sperr"
    assert ev.zonen("gross")[0]["radius_m"] == 100


def test_preset_brand_zwei_zonen_groesste_zuerst():
    z = ev.zonen("brand")
    assert [x["radius_m"] for x in z] == [800, 100]  # grosster zuerst (zeichnet unten)
    assert z[0]["rolle"] == "evak" and z[1]["rolle"] == "sperr"
    # Farben gesetzt
    assert z[1]["farbe"] == "#dc2626"


def test_custom_radien():
    z = ev.zonen(sperr_radius_m=120, evak_radius_m=600)
    assert [x["radius_m"] for x in z] == [600, 120]


def test_leer_ohne_input():
    assert ev.zonen() == []
    assert ev.zonen("unbekannt") == []
    assert ev.zonen(sperr_radius_m=0) == []


def test_jede_zone_hat_pflichtfelder():
    for z in ev.zonen("brand"):
        assert set(["rolle", "radius_m", "farbe", "label"]).issubset(z.keys())


# ── JS-Integration (Struktur) ─────────────────────────────────────────────────

def test_js_rendert_gefahrenradius():
    src = JS.read_text(encoding="utf-8")
    assert 'f.typ === "gefahrenradius"' in src
    assert "EVAK_PRESETS" in src
    # In beiden Renderpfaden (live + replay)
    assert src.count('f.typ === "gefahrenradius"') >= 2
