"""Tests für die providerneutrale Einsatz-Anfahrtsroute."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.services.einsatz_routing import (
    OrsProvider,
    OsrmProvider,
    RouteResponse,
    RouteResult,
    RoutingError,
    choose_alternative,
    get_provider,
)


def _ors_feature(coordinates=None, names=None) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coordinates or [[9.8, 47.4], [9.9, 47.5]]},
        "properties": {
            "summary": {"distance": 1234.5, "duration": 321.0},
            "segments": [{"steps": [{"name": name} for name in (names or ["A", "A", "", "B"])]}],
        },
    }


def _osrm_route(coordinates=None) -> dict:
    return {
        "geometry": {"type": "LineString", "coordinates": coordinates or [[9.8, 47.4], [9.9, 47.5]]},
        "distance": 555.0,
        "duration": 120.0,
        "legs": [{"steps": [{"name": "Erste"}, {"name": "Erste"}, {"name": "Zweite"}]}],
    }


@pytest.fixture(autouse=True)
def routing_settings(monkeypatch):
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_API_URL", "")
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_PROFILE", "")
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_TIMEOUT_SECONDS", 5.0)


@pytest.mark.asyncio
async def test_ors_success_sends_lng_lat_and_extracts_street_names():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [_ors_feature()]})

    result = await OrsProvider(httpx.MockTransport(handler)).calculate_route((47.4, 9.8), (47.5, 9.9))
    body = json.loads(requests[0].content)
    assert body["coordinates"] == [[9.8, 47.4], [9.9, 47.5]]
    assert requests[0].headers["Authorization"] == "test-key"
    assert result.primary.distance_m == 1234.5
    assert result.primary.duration_s == 321.0
    assert result.primary.street_names == ["A", "B"]


@pytest.mark.asyncio
async def test_ors_combines_avoid_polygons_to_multi_polygon():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"features": [_ors_feature()]})

    polygons = [
        {"type": "Polygon", "coordinates": [[[9, 47], [10, 47], [10, 48], [9, 47]]]},
        {"type": "MultiPolygon", "coordinates": [[[[11, 47], [12, 47], [12, 48], [11, 47]]]]},
    ]
    await OrsProvider(httpx.MockTransport(handler)).calculate_route((47.4, 9.8), (47.5, 9.9), avoid_polygons=polygons)
    avoid = captured["options"]["avoid_polygons"]
    assert avoid["type"] == "MultiPolygon"
    assert len(avoid["coordinates"]) == 2


@pytest.mark.asyncio
async def test_ors_requests_and_returns_alternatives():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["alternative_routes"] == {
            "target_count": 2,
            "share_factor": 0.6,
            "weight_factor": 1.6,
        }
        return httpx.Response(200, json={"features": [_ors_feature(), _ors_feature(names=["Alternative"])]})

    result = await OrsProvider(httpx.MockTransport(handler)).calculate_route((47.4, 9.8), (47.5, 9.9), alternatives=True)
    assert len(result.alternatives) == 1
    assert result.alternatives[0].street_names == ["Alternative"]


@pytest.mark.asyncio
async def test_osrm_success_and_no_route():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/noroute/" in request.url.path:
            return httpx.Response(200, json={"code": "NoRoute", "routes": []})
        assert request.url.params["alternatives"] == "true"
        return httpx.Response(200, json={"code": "Ok", "routes": [_osrm_route(), _osrm_route()]})

    result = await OsrmProvider(httpx.MockTransport(handler)).calculate_route((47.4, 9.8), (47.5, 9.9), alternatives=True)
    assert result.primary.street_names == ["Erste", "Zweite"]
    assert len(result.alternatives) == 1
    provider = OsrmProvider(httpx.MockTransport(handler))
    provider.api_url = "https://routing.example/noroute"
    with pytest.raises(RoutingError, match="keine") as error:
        await provider.calculate_route((47.4, 9.8), (47.5, 9.9))
    assert error.value.kind == "no_route"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "kind"),
    [
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)), "timeout"),
        (lambda request: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)), "unreachable"),
        (lambda request: httpx.Response(500, json={}), "http"),
        (lambda request: httpx.Response(200, content=b"not json"), "invalid"),
    ],
)
async def test_errors_are_classified_without_api_key_in_message(handler, kind):
    with pytest.raises(RoutingError) as error:
        await OrsProvider(httpx.MockTransport(handler)).calculate_route((47.4, 9.8), (47.5, 9.9))
    assert error.value.kind == kind
    assert "test-key" not in str(error.value)


def test_get_provider_checks_enablement_and_defaults(monkeypatch):
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", False)
    with pytest.raises(RoutingError) as error:
        get_provider()
    assert error.value.kind == "disabled"
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", True)
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_PROVIDER", "ors")
    ors = get_provider()
    assert isinstance(ors, OrsProvider)
    assert ors.api_url == "https://api.openrouteservice.org"
    assert ors.profile == "driving-car"
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_PROVIDER", "osrm")
    osrm = get_provider()
    assert isinstance(osrm, OsrmProvider)
    assert osrm.api_url == settings.ROUTING_OSRM_URL
    assert osrm.profile == "driving"
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_PROVIDER", "unbekannt")
    with pytest.raises(RoutingError, match="Unbekannter") as error:
        get_provider()
    assert error.value.kind == "disabled"


def test_choose_alternative_skips_routes_crossing_closures():
    primary = RouteResult({"type": "LineString", "coordinates": [[9.8, 47.4], [10.0, 47.4]]}, 1, 1, [])
    alternative = RouteResult({"type": "LineString", "coordinates": [[9.8, 47.6], [10.0, 47.6]]}, 1, 1, [])
    response = RouteResponse(primary, [alternative], "osrm", False)
    closure = {"type": "LineString", "coordinates": [[9.85, 47.4], [9.95, 47.4]]}
    assert choose_alternative(response, [closure]) is alternative
    second_closure = {"type": "LineString", "coordinates": [[9.85, 47.6], [9.95, 47.6]]}
    assert choose_alternative(response, [closure, second_closure]) is None


def test_http_fehler_mit_html_body_bleibt_http_fehler(monkeypatch):
    """Eine HTML-Fehlerseite (z. B. Proxy-500) muss als HTTP-Fehler gemeldet werden, nicht als 'invalid'."""
    import asyncio

    import httpx

    from app.services.einsatz_routing import OrsProvider, OsrmProvider, RoutingError

    monkeypatch.setattr("app.config.settings.EINSATZ_ROUTING_API_KEY", "geheim")
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>"))
    for provider in (OrsProvider(transport=transport), OsrmProvider(transport=transport)):
        try:
            asyncio.run(provider.calculate_route((47.47, 9.75), (47.48, 9.76)))
        except RoutingError as exc:
            assert exc.kind == "http"
            assert "geheim" not in str(exc)
        else:
            raise AssertionError("RoutingError erwartet")
