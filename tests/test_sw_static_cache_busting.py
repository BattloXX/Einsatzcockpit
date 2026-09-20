"""Strukturtests fuer versionierte Static-Asset-Keys im Service Worker."""
from pathlib import Path


SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"


def test_static_assets_use_exact_versioned_cache_key_before_network():
    source = SW_PATH.read_text(encoding="utf-8")
    static_branch = source[source.index("if (url.pathname.startsWith('/static/'))"):]

    exact_match = static_branch.index("cache.match(e.request);")
    network_fetch = static_branch.index("fetch(e.request)")
    offline_fallback = static_branch.index("cache.match(e.request, { ignoreSearch: true })")

    assert exact_match < network_fetch < offline_fallback
    assert static_branch[:network_fetch].count("ignoreSearch") == 0


def test_static_cache_version_is_v15():
    source = SW_PATH.read_text(encoding="utf-8")
    assert "const CACHE = 'ec-v15';" in source


def test_objekt_and_kontakt_pages_are_cached_for_offline_navigation():
    source = SW_PATH.read_text(encoding="utf-8")

    assert "url.pathname === '/objekte/' || url.pathname === '/kontakte'" in source
    assert "^\\/kontakte\\/\\d+$" in source
    assert "^\\/objekte\\/\\d+(\\/einsatz)?$" in source
    assert "await cache.put(e.request, res.clone());" in source
    assert "caches.match(e.request, { cacheName: OBJEKT_CACHE })" in source
