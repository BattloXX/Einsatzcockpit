"""Tests für staticmap_service: Kartenbild-Rendering.

Kein echter Netzwerkzugriff auf OSM-Tile-Server — `requests.get` wird auf ein fingiertes
256x256-Kachelbild gemockt (Muster: test_geocoding_throttle.py mockt httpx statt echten
Nominatim-Zugriff)."""
import io

from PIL import Image


def _fake_tile_png() -> bytes:
    img = Image.new("RGB", (256, 256), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeTileResponse:
    def __init__(self, content: bytes):
        self.status_code = 200
        self.content = content


def test_render_incident_map_png_returns_valid_png(monkeypatch):
    tile_bytes = _fake_tile_png()
    monkeypatch.setattr(
        "requests.get", lambda url, **kwargs: _FakeTileResponse(tile_bytes),
    )

    from app.services.staticmap_service import render_incident_map_png
    png = render_incident_map_png(47.4739, 9.7350, zoom=14, size=(300, 200))

    assert isinstance(png, bytes)
    assert len(png) > 100
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG-Signatur


def test_render_incident_map_png_respects_size(monkeypatch):
    tile_bytes = _fake_tile_png()
    monkeypatch.setattr(
        "requests.get", lambda url, **kwargs: _FakeTileResponse(tile_bytes),
    )

    from app.services.staticmap_service import render_incident_map_png
    png = render_incident_map_png(47.4739, 9.7350, zoom=14, size=(320, 180))
    img = Image.open(io.BytesIO(png))
    assert img.size == (320, 180)


def test_identische_koordinaten_laden_tiles_nur_einmal(monkeypatch):
    from app.services import staticmap_service

    staticmap_service._RENDER_CACHE.clear()
    aufrufe = 0
    tile_bytes = _fake_tile_png()

    def fake_get(url, **kwargs):
        nonlocal aufrufe
        aufrufe += 1
        return _FakeTileResponse(tile_bytes)

    monkeypatch.setattr("requests.get", fake_get)
    staticmap_service.render_incident_map_png(47.48, 9.74, zoom=14, size=(300, 200))
    erste_aufrufe = aufrufe
    staticmap_service.render_incident_map_png(47.48, 9.74, zoom=14, size=(300, 200))
    assert erste_aufrufe > 0
    assert aufrufe == erste_aufrufe
