"""Nachschlagewerke PR 7: Ausbreitungs-Plume (Geometrie + Endpoint + JS)."""
from pathlib import Path

from starlette.routing import Match

from app.services import ausbreitung_service as ab
from tests.conftest import flatten_routes

JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "lagefuehrung.js"


def _match_endpoint(path: str, method: str = "GET") -> str | None:
    import app.main as m
    scope = {"type": "http", "method": method, "path": path}
    for r in flatten_routes(m.app.router.routes):
        matches = getattr(r, "matches", None)
        if matches is None:
            continue
        match, _ = matches(scope)
        if match == Match.FULL:
            return getattr(getattr(r, "endpoint", None), "__name__", None)
    return None


# ── Windrichtung ──────────────────────────────────────────────────────────────

def test_ausbreitungsrichtung_gegenrichtung():
    assert ab.ausbreitungsrichtung(0) == 180.0     # Wind aus Nord -> blaest nach Sued
    assert ab.ausbreitungsrichtung(270) == 90.0    # aus West -> nach Ost
    assert ab.ausbreitungsrichtung(None) == 0.0


# ── Polygon-Geometrie ─────────────────────────────────────────────────────────

def test_plume_ist_geschlossenes_polygon():
    geo = ab.plume_polygon(47.0, 9.7, 180.0, 300.0)
    assert geo["type"] == "Polygon"
    ring = geo["coordinates"][0]
    assert ring[0] == ring[-1]          # geschlossen
    assert ring[0] == [9.7, 47.0]       # Spitze = Quelle
    assert len(ring) >= 5


def test_plume_zeigt_in_ausbreitungsrichtung_sued():
    # richtung 180 (nach Sueden) -> Frontpunkte liegen suedlich (kleinere lat)
    geo = ab.plume_polygon(47.0, 9.7, 180.0, 500.0, halbwinkel_deg=20, segmente=6)
    ring = geo["coordinates"][0]
    front = ring[1:-1]  # ohne Spitze/Schluss
    assert all(pt[1] < 47.0 for pt in front)
    # Mittelpunkt der Front ca. 500 m suedlich
    mitte = front[len(front) // 2]
    dlat_m = (47.0 - mitte[1]) * 111320.0
    assert abs(dlat_m - 500.0) < 60.0


def test_plume_ost():
    geo = ab.plume_polygon(47.0, 9.7, 90.0, 400.0, halbwinkel_deg=15, segmente=4)
    front = geo["coordinates"][0][1:-1]
    assert all(pt[0] > 9.7 for pt in front)  # oestlich (groessere lng)


def test_plume_laenge_min_geschuetzt():
    geo = ab.plume_polygon(47.0, 9.7, 0.0, 0)  # 0 -> auf min angehoben
    assert len(geo["coordinates"][0]) >= 5


# ── Endpoint + JS ─────────────────────────────────────────────────────────────

def test_ausbreitung_endpoint_registriert():
    assert _match_endpoint("/einsatz/5/lagefuehrung/ausbreitung.json") == "lagefuehrung_ausbreitung"


def test_js_ausbreitung_rendering_und_tool():
    src = JS.read_text(encoding="utf-8")
    assert 'f.typ === "ausbreitung"' in src
    assert 'p.kind === "ausbreitung"' in src
    assert "ausbreitung.json" in src
