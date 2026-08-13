"""Strukturtests fuer Live-Benachrichtigungen im Service Worker."""

from pathlib import Path

SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"


def _sw_source() -> str:
    return SW_PATH.read_text(encoding="utf-8")


def test_live_notification_payload_handling():
    src = _sw_source()
    assert "const CACHE = 'ec-v10';" in src
    assert "einsatz_live" in src
    assert "einsatz_live_end" in src

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


def test_notification_click_reuses_open_window():
    src = _sw_source()
    click_handler = src[src.index("self.addEventListener('notificationclick'"):]
    assert "clients.matchAll" in click_handler
    assert ".focus(" in click_handler
