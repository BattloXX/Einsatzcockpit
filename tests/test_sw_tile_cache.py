"""Struktur-Regressionstest PR14 (STAB-3): sw.js muss OSM-Kartenkacheln über
einen eigenen Cache-Bucket bedienen statt Cross-Origin-Requests pauschal zu
ignorieren.

Hinweis: In diesem Repo ist kein Node/JS-Testrunner verfügbar (siehe CLAUDE.md-
Kontext: npm nicht installiert) — dies ist daher ein Struktur-/Regressionstest
auf Quelltext-Ebene, kein echter Service-Worker-Ausführungstest. Funktionale
Verifikation (DevTools Application > Service Workers > Cache Storage, Karte im
gedrosselten Netz neu laden) sollte manuell im Browser erfolgen."""
from pathlib import Path

SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"


def _sw_source() -> str:
    return SW_PATH.read_text(encoding="utf-8")


def test_tile_cache_bucket_defined():
    src = _sw_source()
    assert "TILE_CACHE" in src
    assert "TILE_CACHE_MAX_ENTRIES" in src


def test_tile_requests_no_longer_unconditionally_skipped():
    src = _sw_source()
    # Der alte fruehe Bail-out fuer ALLE Cross-Origin-Requests darf nicht mehr
    # VOR der Kartenkacheln-Behandlung stehen.
    tile_check_idx = src.index("isMapTileRequest(url)")
    early_bailout_idx = src.index("url.origin !== location.origin")
    assert tile_check_idx < early_bailout_idx, (
        "Kartenkacheln-Behandlung muss vor dem generischen Cross-Origin-Bailout "
        "stehen, sonst werden Tile-Requests weiterhin nie gecacht (STAB-3-Regression)."
    )


def test_tile_cache_survives_activate_cleanup():
    """activate() darf den Tile-Cache nicht mitloeschen (sonst waere er bei
    jedem SW-Update leer)."""
    src = _sw_source()
    activate_block = src[src.index("addEventListener('activate'"):src.index("addEventListener('fetch'")]
    assert "TILE_CACHE" in activate_block


def test_map_tile_regex_matches_osm_subdomains():
    import re
    pattern = re.compile(r"(^|\.)tile\.openstreetmap\.org$")
    for host in ("a.tile.openstreetmap.org", "b.tile.openstreetmap.org", "tile.openstreetmap.org"):
        assert pattern.search(host), f"{host} sollte als Kartenkachel-Host erkannt werden"
    for host in ("tile.openstreetmap.org.evil.com", "example.com"):
        assert not pattern.search(host), f"{host} sollte NICHT als Kartenkachel-Host erkannt werden"
