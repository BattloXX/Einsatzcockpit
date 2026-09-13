"""Regressionstests für die regelkonforme OSM-Standardkarten-Einbindung."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = [
    path for path in (ROOT / "app").rglob("*")
    if path.suffix in {".html", ".js", ".py"} and ".min." not in path.name
]


def test_no_legacy_osm_subdomain_urls_or_options_remain():
    legacy_patterns = (
        "https://{s}.tile.openstreetmap.org",
        "https://a.tile.openstreetmap.org",
        "https://b.tile.openstreetmap.org",
        "https://c.tile.openstreetmap.org",
        "subdomains: 'abc'",
        'subdomains: "abc"',
    )
    matches = []
    for path in SOURCE_FILES:
        content = path.read_text(encoding="utf-8-sig")
        for pattern in legacy_patterns:
            if pattern in content:
                matches.append(f"{path.relative_to(ROOT)}: {pattern}")
    assert not matches, "Veraltete OSM-Tile-Konfiguration gefunden:\n" + "\n".join(matches)


def test_osm_browser_config_uses_canonical_url_and_attribution():
    content = (ROOT / "app/static/js/map-config.js").read_text()
    assert 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' in content
    assert 'https://www.openstreetmap.org/copyright' in content
    assert "OpenStreetMap contributors" in content
    assert "tileerror" in content
    assert "Kartenhintergrund derzeit nicht verfügbar" in content


def test_statistics_map_uses_the_central_osm_configuration():
    content = (ROOT / "app/static/js/stats_karte.js").read_text()
    assert "EinsatzcockpitMapConfig.addOsmTileLayer(activeMap)" in content
    assert "tile.openstreetmap.org" not in content
