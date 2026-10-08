"""Tests fuer die automatische Abschnittsgeometrie der Strassensperren."""

from types import SimpleNamespace

import pytest

from app.routers import ui_road_closure
from app.services import road_closure_section_service as section_service
from tests.test_strassensperren_ui import _closure, _login, _setup_user


async def _geocode(street, number, city):
    points = {"12a": (47.5, 9.7), "38": (47.51, 9.72)}
    point = points.get(number)
    return SimpleNamespace(lat=point[0], lng=point[1]) if point else None


@pytest.mark.asyncio
async def test_zwei_hausnummern_ergeben_linie(monkeypatch):
    async def route(points):
        return {"coords": [[47.5, 9.7], [47.505, 9.71], [47.51, 9.72]], "laenge_m": 2000}

    monkeypatch.setattr(section_service, "geocode_address", _geocode)
    monkeypatch.setattr(section_service, "strassen_route", route)
    result = await section_service.section_from_address("Hauptstraße", "Hausnummer 12a", "Nr. 38", "Wolfurt")
    assert result["geometry"]["type"] == "LineString"
    assert result["geometry"]["coordinates"][0] == [9.7, 47.5]
    assert result["geometry_status"] == "needs_review"


@pytest.mark.asyncio
async def test_ohne_routing_wird_gerade_linie_verwendet(monkeypatch):
    async def no_route(points):
        return None

    monkeypatch.setattr(section_service, "geocode_address", _geocode)
    monkeypatch.setattr(section_service, "strassen_route", no_route)
    result = await section_service.section_from_address("Hauptstraße", "12a", "38", None)
    assert result["geometry"]["coordinates"] == [[9.7, 47.5], [9.72, 47.51]]


@pytest.mark.asyncio
async def test_ohne_hausnummer_und_mit_einer_hausnummer(monkeypatch):
    monkeypatch.setattr(section_service, "geocode_address", _geocode)
    with pytest.raises(ValueError, match="Hausnummern nicht erkannt"):
        await section_service.section_from_address("Hauptstraße", "beim Park", None, None)
    result = await section_service.section_from_address("Hauptstraße", "Nr. 38", None, None)
    assert result["geometry"] == {"type": "Point", "coordinates": [9.72, 47.51]}
    assert result["geometry_status"] == "needs_review"


@pytest.mark.asyncio
async def test_stark_abweichende_route_hat_hinweis(monkeypatch):
    async def long_route(points):
        return {"coords": [[47.5, 9.7], [47.9, 9.7], [47.51, 9.72]], "laenge_m": 100000}

    monkeypatch.setattr(section_service, "geocode_address", _geocode)
    monkeypatch.setattr(section_service, "strassen_route", long_route)
    result = await section_service.section_from_address("Hauptstraße", "12a", "38", None)
    assert result["hinweis"] == "Route weicht stark ab – bitte Abschnitt einzeichnen"


def test_abschnitt_endpoint_rollenschutz_und_fehler(client, monkeypatch):
    readonly = _setup_user("readonly")
    _login(client, readonly)
    assert client.post("/strassensperren/abschnitt", json={}).status_code == 403

    manager = _setup_user("objekt_verwalter")
    _login(client, manager)

    async def success(*args, **kwargs):
        from app.services.road_closure_section_resolver import SectionResult

        return SectionResult(
            geometry={"type": "LineString", "coordinates": [[9.7, 47.5], [9.71, 47.51]]},
            quality="mittel",
            geometry_status="needs_review",
            hinweise=["Prüfen"],
        )

    monkeypatch.setattr(ui_road_closure, "resolve_section", success)
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    response = client.post("/strassensperren/abschnitt", json={"street": "Hauptstraße", "from_text": "12"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["geometry_status"] == "needs_review"

    async def failure(*args, **kwargs):
        from app.services.road_closure_section_resolver import SectionResult

        return SectionResult(hinweise=["Adresse nicht gefunden – bitte Abschnitt auf der Karte einzeichnen."])

    monkeypatch.setattr(ui_road_closure, "resolve_section", failure)
    assert client.post("/strassensperren/abschnitt", json={}, headers=headers).status_code == 422


def test_abschnitt_endpoint_ist_bei_modul_aus_nicht_sichtbar(client):
    disabled = _setup_user("objekt_verwalter", enabled=False)
    _login(client, disabled)
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    assert client.post("/strassensperren/abschnitt", json={}, headers=headers).status_code == 404


def test_sperren_seiten_binden_karten_skripte_ein(client):
    manager = _setup_user("objekt_verwalter")
    _login(client, manager)
    closure = _closure()
    assert 'src="/static/js/road_closure_map.js' in client.get("/strassensperren").text
    assert 'src="/static/js/road_closure_editor.js' in client.get("/strassensperren/neu").text
    assert 'src="/static/js/road_closure_map.js' in client.get(f"/strassensperren/{closure.id}").text
