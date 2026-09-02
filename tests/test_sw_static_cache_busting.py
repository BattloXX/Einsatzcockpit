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


def test_static_cache_version_is_v13():
    source = SW_PATH.read_text(encoding="utf-8")
    assert "const CACHE = 'ec-v13';" in source
