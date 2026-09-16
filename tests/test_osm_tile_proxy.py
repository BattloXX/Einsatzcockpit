"""Tests fuer den same-origin OSM-Tile-Proxy ohne echte Netzwerkzugriffe."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.core.map_config import OSM_TILE_REFERER, OSM_TILE_USER_AGENT
from app.routers import ui_map_tiles


def _mock_osm_client():
    response = MagicMock()
    response.content = b"png-data"
    response.headers = {"content-type": "image/png", "cache-control": "max-age=86400"}
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=client)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    return context_manager, client


def test_valid_tile_is_fetched_with_osm_headers_and_returned(client):
    ui_map_tiles._tile_cache.clear()
    context_manager, upstream_client = _mock_osm_client()
    with patch("app.routers.ui_map_tiles.httpx.AsyncClient", return_value=context_manager):
        response = client.get("/karten/osm-tile/1/1/0")

    assert response.status_code == 200
    assert response.content == b"png-data"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "max-age=86400"
    url = "https://tile.openstreetmap.org/1/1/0.png"
    upstream_client.get.assert_awaited_once_with(
        url, headers={"User-Agent": OSM_TILE_USER_AGENT, "Referer": OSM_TILE_REFERER},
    )


def test_invalid_tile_coordinates_are_rejected(client):
    assert client.get("/karten/osm-tile/-1/0/0").status_code in {400, 404}
    assert client.get("/karten/osm-tile/2/4/0").status_code == 400
    assert client.get("/karten/osm-tile/2/0/4").status_code == 400


def test_second_request_for_same_tile_uses_memory_cache(client):
    ui_map_tiles._tile_cache.clear()
    context_manager, upstream_client = _mock_osm_client()
    with patch("app.routers.ui_map_tiles.httpx.AsyncClient", return_value=context_manager):
        assert client.get("/karten/osm-tile/1/1/0").status_code == 200
        assert client.get("/karten/osm-tile/1/1/0").status_code == 200

    assert upstream_client.get.await_count == 1


def test_osm_fetch_failure_returns_bad_gateway(client):
    ui_map_tiles._tile_cache.clear()
    context_manager, upstream_client = _mock_osm_client()
    upstream_client.get.side_effect = httpx.TimeoutException("timeout")
    with patch("app.routers.ui_map_tiles.httpx.AsyncClient", return_value=context_manager):
        response = client.get("/karten/osm-tile/1/1/0")

    assert response.status_code == 502
