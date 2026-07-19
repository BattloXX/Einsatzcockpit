"""Straßen-Routing für den Förderstrecken-Planer (OSRM-Proxy)."""
import httpx
import pytest

from app.config import settings
from app.services import routing_service


class _FakeClient:
    """Minimaler async-Context-Manager-Ersatz für httpx.AsyncClient."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return self._response


def _osrm_response(status=200, coords=None, distance=1234.5):
    payload = {"routes": [{
        "geometry": {"coordinates": coords or [[9.75, 47.47], [9.755, 47.475], [9.76, 47.48]]},
        "distance": distance,
    }]} if coords is not False else {"routes": []}
    return httpx.Response(status, json=payload)


async def test_route_parsen(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "ROUTING_OSRM_URL", "https://osrm.example")
    monkeypatch.setattr(routing_service.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_osrm_response()))
    res = await routing_service.strassen_route([(47.47, 9.75), (47.48, 9.76)])
    assert res is not None
    assert res["laenge_m"] == 1234.5
    # GeoJSON [lng,lat] → [lat,lng], mind. 2 Punkte
    assert res["coords"][0] == [47.47, 9.75]
    assert len(res["coords"]) == 3


async def test_routing_deaktiviert_gibt_none(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "ROUTING_OSRM_URL", "")
    assert await routing_service.strassen_route([(47.47, 9.75), (47.48, 9.76)]) is None


async def test_zu_wenige_punkte(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "ROUTING_OSRM_URL", "https://osrm.example")
    assert await routing_service.strassen_route([(47.47, 9.75)]) is None


async def test_osrm_fehlerstatus_gibt_none(monkeypatch):
    monkeypatch.setattr(routing_service.settings, "ROUTING_OSRM_URL", "https://osrm.example")
    monkeypatch.setattr(routing_service.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_osrm_response(status=429)))
    assert await routing_service.strassen_route([(47.47, 9.75), (47.48, 9.76)]) is None


# settings-Default: OSRM-URL ist gesetzt (Feature aktiv out-of-the-box)
def test_default_osrm_konfiguriert():
    assert settings.ROUTING_OSRM_URL.startswith("http")
