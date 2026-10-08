"""Regressionen fuer Photon- und OSM-Adressprüfung der Straßensperren."""

from dataclasses import dataclass

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.tools import strassensperren as mcp_sperren
from app.models.master import FireDept
from app.routers import ui_road_closure
from app.services import road_closure_section_service as section_service
from app.services.road_closure_section_resolver import SectionResult
from tests.test_mcp_objekte import _token
from tests.test_mcp_strassensperren import ROUTE, _bereit, _rufe
from tests.test_strassensperren_ui import _login, _setup_user


@dataclass
class _Suggestion:
    label: str
    street: str | None = None
    house_number: str | None = None
    city: str | None = None
    lat: float | None = None
    lng: float | None = None
    source: str = "photon"


def test_vorschlaege_rechte_flag_und_org_ort(client, monkeypatch):
    readonly = _setup_user("readonly")
    _login(client, readonly)
    assert client.get("/strassensperren/adresse/vorschlaege?q=Test").status_code == 403
    manager = _setup_user("objekt_verwalter")
    db = SessionLocal()
    set_tenant_context(db, None)
    db.get(FireDept, manager.org_id).city = "Wolfurt"
    db.commit()
    db.close()
    _login(client, manager)
    called = {}

    async def suggestions(*args, **kwargs):
        called.update(kwargs)
        return [_Suggestion("Testweg, Wolfurt", street="Testweg")]

    monkeypatch.setattr(ui_road_closure, "suggest_addresses", suggestions)
    response = client.get("/strassensperren/adresse/vorschlaege?q=Test")
    assert response.status_code == 200
    assert response.json()["items"][0]["street"] == "Testweg"
    assert called["city"] == "Wolfurt"
    assert client.get("/strassensperren/adresse/vorschlaege?q=").json() == {"items": []}
    disabled = _setup_user("objekt_verwalter", enabled=False)
    _login(client, disabled)
    assert client.get("/strassensperren/adresse/vorschlaege?q=Test").status_code == 404


@pytest.mark.asyncio
async def test_validate_address_status_und_vorschlaege(monkeypatch):
    org = type("Org", (), {"id": 1, "city": "Wolfurt"})()

    class Network:
        ways = [type("Way", (), {"name": "Rebbergstraße"})(), type("Way", (), {"name": "Kellaweg"})()]

    exact = SectionResult(osm_name="Rebberg", length_m=128, quality="hoch", endpoints=[
        {"rolle": "von", "text": "Kreuzung Kellaweg", "methode": "kreuzung"},
        {"rolle": "bis", "text": "", "methode": "strassenende"},
    ])

    async def resolve_exact(*args):
        return exact, Network()

    monkeypatch.setattr(section_service, "_resolve_osm_section", resolve_exact)
    result = await section_service.validate_address(None, org, "Rebberg", "Kreuzung Kellaweg", "")
    assert result["status"] == "ok" and result["laenge_m"] == 128
    assert result["von"]["gefunden"] is True
    different = SectionResult(osm_name="Rebbergstraße")

    async def resolve_different(*args):
        return different, Network()

    monkeypatch.setattr(section_service, "_resolve_osm_section", resolve_different)
    result = await section_service.validate_address(None, org, "Rebberg", "", "")
    # Querstraßen (Kellaweg) sind keine Schreibvarianten der gesuchten Straße.
    assert result["status"] == "abweichend" and result["vorschlaege"] == ["Rebbergstraße"]
    missing = SectionResult()

    async def resolve_missing(*args):
        return missing, Network()

    async def suggestions(*args, **kwargs):
        return [_Suggestion("A", street="A"), _Suggestion("A", street="A"), _Suggestion("B", street="B")]

    monkeypatch.setattr(section_service, "_resolve_osm_section", resolve_missing)
    monkeypatch.setattr(section_service, "suggest_addresses", suggestions)
    result = await section_service.validate_address(None, org, "Unbekannt", "", "")
    assert result["status"] == "nicht_gefunden" and result["vorschlaege"] == ["A", "B"]


def test_pruefen_csrf_rolle_und_unbekannt(client, monkeypatch):
    readonly = _setup_user("readonly")
    _login(client, readonly)
    assert client.post("/strassensperren/adresse/pruefen", json={}).status_code == 403
    manager = _setup_user("objekt_verwalter")
    _login(client, manager)
    assert client.post("/strassensperren/adresse/pruefen", json={}).status_code == 403

    async def unknown(*args, **kwargs):
        return {"status": "unbekannt", "von": {}, "bis": {}, "vorschlaege": []}

    monkeypatch.setattr(ui_road_closure, "validate_address", unknown)
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    response = client.post("/strassensperren/adresse/pruefen", json={"street": "Test"}, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "unbekannt"


def test_mcp_adressvalidierung_ohne_zweiten_resolver(client, monkeypatch):
    seed = _bereit("mcp-adressvalidierung", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")

    async def section(*args, **kwargs):
        return SectionResult(geometry=ROUTE, osm_name="Hauptstraße", length_m=42, quality="hoch")

    async def forbidden(*args, **kwargs):
        raise AssertionError("validate_address darf nicht aufgerufen werden")

    monkeypatch.setattr(mcp_sperren.road_closure_section_service, "resolve_section", section)
    monkeypatch.setattr(mcp_sperren.road_closure_section_service, "validate_address", forbidden)
    created = _rufe(client, token, "strassensperre_anlegen", title="OSM", valid_from="2026-07-01T10:00",
                    restriction_type="closed", street="Hauptstraße")
    assert created["adressvalidierung"]["status"] == "ok"
    called = []

    async def validation(*args, **kwargs):
        called.append(True)
        return {"status": "ok"}

    monkeypatch.setattr(mcp_sperren.road_closure_section_service, "validate_address", validation)
    with_geometry = _rufe(client, token, "strassensperre_anlegen", title="Manuell", valid_from="2026-07-02T10:00",
                          restriction_type="closed", street="Testweg", geometry_geojson=ROUTE)
    assert called and with_geometry["adressvalidierung"] == {"status": "ok"}

    async def failing(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(mcp_sperren.road_closure_section_service, "validate_address", failing)
    survives = _rufe(client, token, "strassensperre_anlegen", title="Offline", valid_from="2026-07-03T10:00",
                     restriction_type="closed", street="Testweg 2", geometry_geojson=ROUTE)
    assert survives["status"] == "created" and survives["adressvalidierung"] == {"status": "unbekannt"}


def test_autocomplete_url_default_is_backward_compatible():
    text = open("app/static/js/address-autocomplete.js", encoding="utf-8").read()
    assert "opts.url        || '/adresse/vorschlaege'" in text


@pytest.mark.asyncio
async def test_ohne_strasse_oder_ohne_osm_kein_falsches_nicht_gefunden(monkeypatch):
    org = type("Org", (), {"id": 1, "city": "Wolfurt"})()

    async def must_not_run(*args):
        raise AssertionError("ohne Straße darf OSM nicht abgefragt werden")

    monkeypatch.setattr(section_service, "_resolve_osm_section", must_not_run)
    result = await section_service.validate_address(None, org, "  ", "", "")
    assert result["status"] == "keine_strasse"
    fallback = SectionResult(
        geometry=ROUTE, quality="niedrig", geometry_status="needs_review", osm_erreichbar=False,
        hinweise=["OSM-Straßennetz nicht erreichbar – Näherung über Hausnummern"],
    )
    assert section_service.validation_from_section(fallback, "Rebberg")["status"] == "unbekannt"
    assert section_service.validation_from_section(SectionResult(), "Rebberg")["status"] == "nicht_gefunden"


@pytest.mark.asyncio
async def test_resolve_section_markiert_osm_ausfall(monkeypatch):
    org = type("Org", (), {"id": 1, "city": "Wolfurt"})()

    async def offline(*args):
        return None, None

    async def fallback(street, from_text, to_text, city):
        return {"geometry": ROUTE}

    monkeypatch.setattr(section_service, "_resolve_osm_section", offline)
    monkeypatch.setattr(section_service, "section_from_address", fallback)
    result = await section_service.resolve_section(None, org, "Rebberg", "Nr. 1", "Nr. 5")
    assert result.osm_erreichbar is False and result.quality == "niedrig"
