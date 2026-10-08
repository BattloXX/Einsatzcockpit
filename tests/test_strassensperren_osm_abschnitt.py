from __future__ import annotations

import httpx
import pytest

from app.services import osm_street_service


@pytest.mark.asyncio
async def test_osm_street_network_parses_nodes_and_uses_substring(monkeypatch):
    osm_street_service._CACHE.clear()
    calls = []

    def handler(request):
        calls.append(request.content.decode())
        return httpx.Response(200, json={"elements": [{"type": "way", "id": 1, "nodes": [1, 2],
            "tags": {"name": "Rebbergstraße", "highway": "residential"},
            "geometry": [{"lat": 47.47, "lon": 9.74}, {"lat": 47.47, "lon": 9.75}]}]})

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(osm_street_service.httpx, "AsyncClient", Client)
    network = await osm_street_service.fetch_street_network("Rebberg", 47.47, 9.74)
    assert network and network.ways[0].node_ids == [1, 2] and len(calls) == 2


@pytest.mark.asyncio
async def test_osm_street_network_returns_none_on_error(monkeypatch):
    osm_street_service._CACHE.clear()

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(osm_street_service.httpx, "AsyncClient", Client)
    assert await osm_street_service.fetch_street_network('A"B', 47.47, 9.74) is None


def _way(way_id, name, coords, nodes):
    return {
        "type": "way",
        "id": way_id,
        "nodes": nodes,
        "tags": {"name": name, "highway": "residential"},
        "geometry": [{"lat": lat, "lon": lng} for lat, lng in coords],
    }


_ECHTER_CLIENT = httpx.AsyncClient


def _fake_client(monkeypatch, responses, calls):
    def handler(request):
        calls.append(request.content.decode())
        answer = responses.pop(0) if responses else httpx.Response(200, json={"elements": []})
        if isinstance(answer, Exception):
            raise answer
        return answer

    class Client(_ECHTER_CLIENT):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(osm_street_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(osm_street_service, "RETRY_DELAY_SECONDS", 0)


def _decoded(body: str) -> str:
    from urllib.parse import unquote_plus

    return unquote_plus(body)


KELLAWEG = {"elements": [_way(1, "Kellaweg", [(47.457, 9.754), (47.457, 9.756)], [1, 2])]}


@pytest.mark.asyncio
async def test_gemeinde_filter_wird_genutzt_und_cache_trennt_orte(monkeypatch):
    osm_street_service._CACHE.clear()
    calls: list[str] = []
    _fake_client(monkeypatch, [httpx.Response(200, json=KELLAWEG), httpx.Response(200, json=KELLAWEG)], calls)
    first = await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75, city="Wolfurt")
    assert first and first.ways[0].name == "Kellaweg"
    assert len(calls) == 1 and '"name"="Wolfurt"' in _decoded(calls[0]) and "(area.a)" in _decoded(calls[0])
    again = await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75, city=" Wolfurt ")
    assert again is first and len(calls) == 1
    await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75, city="Schwarzach")
    assert len(calls) == 2 and "Schwarzach" in _decoded(calls[1])


@pytest.mark.asyncio
async def test_ohne_treffer_im_gemeindegebiet_wird_umkreis_gesucht(monkeypatch):
    osm_street_service._CACHE.clear()
    calls: list[str] = []
    empty = {"elements": []}
    responses = [httpx.Response(200, json=empty), httpx.Response(200, json=empty), httpx.Response(200, json=KELLAWEG)]
    _fake_client(monkeypatch, responses, calls)
    network = await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75, city="Gibtsnicht")
    assert network and network.ways[0].name == "Kellaweg"
    assert "(area.a)" in _decoded(calls[0]) and "(area.a)" in _decoded(calls[1])
    assert "around:" in _decoded(calls[2])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.Response(504), httpx.Response(429), httpx.ReadTimeout("langsam")])
async def test_ueberlast_wird_einmal_wiederholt(monkeypatch, failure):
    osm_street_service._CACHE.clear()
    calls: list[str] = []
    _fake_client(monkeypatch, [failure, httpx.Response(200, json=KELLAWEG)], calls)
    network = await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75)
    assert network and len(calls) == 2


@pytest.mark.asyncio
async def test_zweimal_ueberlast_oder_400_liefert_none(monkeypatch):
    osm_street_service._CACHE.clear()
    calls: list[str] = []
    _fake_client(monkeypatch, [httpx.Response(504), httpx.Response(504)], calls)
    assert await osm_street_service.fetch_street_network("Kellaweg", 47.46, 9.75) is None
    assert len(calls) == 2
    osm_street_service._CACHE.clear()
    calls.clear()
    _fake_client(monkeypatch, [httpx.Response(400)], calls)
    assert await osm_street_service.fetch_street_network("Unterhub", 47.46, 9.75) is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_resolve_section_faellt_ohne_osm_auf_hausnummern_zurueck(monkeypatch):
    from types import SimpleNamespace

    from app.services import road_closure_section_service as section_service

    async def center(org, org_settings, city):
        return (47.46, 9.75)

    async def no_network(*args, **kwargs):
        return None

    async def fallback(street, from_text, to_text, city):
        return {"geometry": {"type": "LineString", "coordinates": [[9.75, 47.46], [9.76, 47.46]]}}

    async def failing(street, from_text, to_text, city):
        raise ValueError("Hausnummern nicht erkannt – bitte Abschnitt auf der Karte einzeichnen.")

    class Db:
        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def first(self):
            return None

    org = SimpleNamespace(id=1, city="Wolfurt")
    monkeypatch.setattr(section_service, "search_center", center)
    monkeypatch.setattr(section_service, "fetch_street_network", no_network)
    monkeypatch.setattr(section_service, "section_from_address", fallback)
    result = await section_service.resolve_section(Db(), org, "Kellaweg", "Nr. 2", "Nr. 9")
    assert result.quality == "niedrig" and result.geometry_status == "needs_review" and result.geometry
    assert "OSM-Straßennetz nicht erreichbar" in result.hinweise[0]
    monkeypatch.setattr(section_service, "section_from_address", failing)
    missing = await section_service.resolve_section(Db(), org, "Kellaweg", "", "")
    assert missing.geometry is None and missing.geometry_status == "missing"
    assert "erneut versuchen" in missing.hinweise[0]
