"""Struktur-Regressionstest: Kartenkacheln (tile.openstreetmap.org) duerfen im
Service Worker NICHT per fetch() abgefangen/erneut abgesetzt werden.

Hintergrund (2026-07-06): PR14/STAB-3 hatte genau das eingefuehrt (eigener
TILE_CACHE-Bucket, cache-first + stale-while-revalidate ueber fetch(e.request)
im SW), um Kartenkacheln offline/bei langsamem Netz verfuegbar zu machen. Live
verifiziert brach das die Kartendarstellung komplett: ein per fetch() aus dem
Service-Worker-Kontext erneut abgesetzter Tile-Request hat Sec-Fetch-Dest auf
"empty" statt "image" (wie beim nativen <img>-Laden), was OSMs Fastly-Edge als
Scraping/Bot-Traffic wertet und mit HTTP 503 blockt. Mit abgemeldetem Service
Worker luden dieselben Kacheln sofort wieder normal.

Hinweis: Kein Node/JS-Testrunner verfuegbar (siehe CLAUDE.md: npm nicht
installiert) — dies ist ein Struktur-/Regressionstest auf Quelltext-Ebene,
kein echter Service-Worker-Ausführungstest."""
from pathlib import Path

SW_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "sw.js"


def _sw_source() -> str:
    return SW_PATH.read_text(encoding="utf-8")


def test_tile_requests_not_proxied_through_fetch():
    src = _sw_source()
    assert "tile.openstreetmap.org" not in src, (
        "Kartenkacheln duerfen im Service Worker nicht mehr per Hostname erkannt "
        "und ueber fetch()/respondWith() proxied werden (bricht Sec-Fetch-Dest, "
        "OSM blockt dann mit HTTP 503 — Vorfall 2026-07-06)."
    )


def test_cross_origin_requests_pass_through_untouched():
    src = _sw_source()
    # Der fruehe Bail-out fuer Cross-Origin-Requests (kein respondWith, Browser
    # laedt direkt) muss VOR allen spezifischeren respondWith()-Zweigen stehen.
    bailout_idx = src.index("url.origin !== location.origin")
    first_respond_with_idx = src.index("e.respondWith(")
    assert bailout_idx < first_respond_with_idx, (
        "Cross-Origin-Requests (u.a. Kartenkacheln) muessen vor jedem "
        "respondWith()-Zweig durchgereicht werden, damit der Browser sie nativ "
        "(inkl. korrektem Sec-Fetch-Dest) laedt."
    )
