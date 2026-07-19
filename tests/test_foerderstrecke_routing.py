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
    # L-Form (nicht kollinear) → alle drei Punkte überleben die Vereinfachung
    payload = {"routes": [{
        "geometry": {"coordinates": coords or [[9.75, 47.47], [9.76, 47.47], [9.76, 47.48]]},
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


async def test_route_wird_ausgeduennt(monkeypatch):
    """Dichte, nahezu gerade Geometrie wird auf wenige Stützpunkte reduziert."""
    monkeypatch.setattr(routing_service.settings, "ROUTING_OSRM_URL", "https://osrm.example")
    monkeypatch.setattr(routing_service.settings, "ROUTING_SIMPLIFY_TOLERANCE_M", 25.0)
    # 50 fast kollineare Punkte entlang einer Geraden
    dichte = [[9.75 + i * 0.001, 47.47 + i * 0.001] for i in range(50)]
    monkeypatch.setattr(routing_service.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_osrm_response(coords=dichte)))
    res = await routing_service.strassen_route([(47.47, 9.75), (47.52, 9.80)])
    assert res is not None
    assert len(res["coords"]) < len(dichte)      # deutlich weniger Griffe
    assert res["coords"][0] == [47.47, 9.75]      # Endpunkte bleiben
    assert res["coords"][-1] == [47.519, 9.799]


def test_vereinfache_route_behaelt_ecken():
    """Douglas-Peucker entfernt kollineare Zwischenpunkte, behält echte Ecken + Endpunkte."""
    from app.services.routing_service import vereinfache_route

    # Gerade mit überflüssigem Mittelpunkt → 2 Punkte
    gerade = [[47.47, 9.75], [47.475, 9.755], [47.48, 9.76]]
    assert vereinfache_route(gerade, 25.0) == [[47.47, 9.75], [47.48, 9.76]]
    # Rechtwinklige Ecke → Mittelpunkt bleibt erhalten
    ecke = [[47.47, 9.75], [47.47, 9.76], [47.48, 9.76]]
    assert vereinfache_route(ecke, 25.0) == ecke
    # tol<=0 → unverändert
    assert vereinfache_route(gerade, 0) == gerade


# settings-Default: OSRM-URL ist gesetzt (Feature aktiv out-of-the-box)
def test_default_osrm_konfiguriert():
    assert settings.ROUTING_OSRM_URL.startswith("http")
