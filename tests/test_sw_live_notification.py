"""Strukturtests fuer Live-Benachrichtigungen im Service Worker."""

from pathlib import Path

SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"


def _sw_source() -> str:
    return SW_PATH.read_text(encoding="utf-8")


def test_live_notification_payload_handling():
    src = _sw_source()
    assert "const CACHE = 'ec-v12';" in src
    assert "einsatz_live" in src
    assert "einsatz_live_end" in src
    assert "gsl_live" in src
    assert "gsl_live_end" in src

    push_handler = src[src.index("self.addEventListener('push'"):]
    assert "silent" in push_handler
    assert "renotify" in push_handler
    assert src.index("showNotification") < src.index("getNotifications")


def test_stats_assets_are_precached_with_versioned_request_fallback():
    src = _sw_source()
    for path in (
        "/static/css/leaflet.min.css",
        "/static/js/leaflet.min.js",
        "/static/js/chart.umd.min.js",
        "/static/js/stats_karte.js",
    ):
        assert f"'{path}'" in src
    assert "cache.match(e.request, { ignoreSearch: true })" in src


def test_precache_is_best_effort_and_separates_core_from_optional_assets():
    src = _sw_source()
    install_handler = src[
        src.index("self.addEventListener('install'"):
        src.index("self.addEventListener('activate'")
    ]
    assert "CORE_PRECACHE" in src
    assert "OPTIONAL_PRECACHE" in src
    assert "Promise.allSettled" in src
    assert ".addAll(" not in install_handler
    assert "CORE_READY_KEY" in install_handler


def test_static_handler_always_handles_network_failure():
    src = _sw_source()
    static_handler = src[src.index("// Static assets"):src.index("// Everything else")]
    assert "await caches.match(e.request, { ignoreSearch: true })" in static_handler
    assert "void fetchPromise.catch" in static_handler
    assert "return new Response('Static asset unavailable'" in static_handler


def test_notification_click_reuses_open_window():
    src = _sw_source()
    click_handler = src[src.index("self.addEventListener('notificationclick'"):]
    assert "clients.matchAll" in click_handler
    assert ".focus(" in click_handler
