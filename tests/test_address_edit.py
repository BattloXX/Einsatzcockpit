"""Tests für Adresse-Edit-Routes und Lagekarte-URL-Hilfsfunktionen."""
import pytest
from unittest.mock import AsyncMock, patch

from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept
from app.services.lagekarte import resolve_lagekarte_url, scatter_coords


# ── resolve_lagekarte_url ─────────────────────────────────────────────────────

def _make_incident(**kwargs) -> Incident:
    inc = Incident.__new__(Incident)
    inc.id = 1
    inc.lat = kwargs.get("lat")
    inc.lng = kwargs.get("lng")
    inc.lagekarte_shash_url = kwargs.get("lagekarte_shash_url")
    return inc


def test_resolve_shash_takes_priority():
    inc = _make_incident(lat=47.4, lng=9.7, lagekarte_shash_url="https://www.lagekarte.info/?shash=abc123")
    url = resolve_lagekarte_url(inc)
    assert url == "https://www.lagekarte.info/?shash=abc123"


def test_resolve_fallback_to_einsatz_link():
    inc = _make_incident(lat=47.4664, lng=9.7416)
    url = resolve_lagekarte_url(inc)
    assert url is not None
    assert "lagekarte.info" in url
    assert "einsatz=47.4664,9.7416" in url


def test_resolve_none_when_no_coords():
    inc = _make_incident()
    assert resolve_lagekarte_url(inc) is None


def test_resolve_shash_other_link_forms():
    inc = _make_incident(lagekarte_shash_url="https://www.lagekarte.info/?center=47.4664,9.7416,14")
    url = resolve_lagekarte_url(inc)
    assert "center=" in url


# ── scatter_coords ────────────────────────────────────────────────────────────

def test_scatter_single_vehicle():
    lat, lng = scatter_coords(47.4664, 9.7416, 0, 1)
    assert lat == 47.4664
    assert lng == 9.7416


def test_scatter_deterministic():
    r1 = scatter_coords(47.4664, 9.7416, 0, 3)
    r2 = scatter_coords(47.4664, 9.7416, 0, 3)
    assert r1 == r2


def test_scatter_different_indices():
    r0 = scatter_coords(47.4664, 9.7416, 0, 3)
    r1 = scatter_coords(47.4664, 9.7416, 1, 3)
    r2 = scatter_coords(47.4664, 9.7416, 2, 3)
    # Alle drei Positionen müssen verschieden sein
    assert r0 != r1
    assert r1 != r2
    assert r0 != r2


def test_scatter_small_radius():
    for i in range(5):
        lat, lng = scatter_coords(47.4664, 9.7416, i, 5)
        assert abs(lat - 47.4664) < 0.001
        assert abs(lng - 9.7416) < 0.01


# ── Adresse-Edit-Endpoints (ohne Session-Auth, da kein Session-Fixture) ───────

class TestAddressEndpoints:
    """Smoke-Tests ohne gültige Session → erwarten 401/Redirect."""

    def test_get_address_modal_requires_auth(self, client, setup_db):
        db = SessionLocal()
        try:
            inc = Incident(alarm_type_code="T1", status="active")
            db.add(inc)
            db.commit()
            inc_id = inc.id
        finally:
            db.close()

        r = client.get(f"/einsatz/{inc_id}/adresse/bearbeiten", follow_redirects=False)
        # Ohne Login: Redirect zu /login oder 401
        assert r.status_code in (302, 401, 403)

    def test_post_address_requires_auth(self, client, setup_db):
        r = client.post("/einsatz/1/adresse",
                        data={"address_street": "Test", "address_no": "1",
                              "address_city": "Wolfurt", "lat": "47.0", "lng": "9.7"},
                        follow_redirects=False)
        assert r.status_code in (302, 401, 403)

    def test_post_geocode_requires_auth(self, client, setup_db):
        r = client.post("/einsatz/1/adresse/geocode",
                        data={"address_street": "Teststraße", "address_no": "1",
                              "address_city": "Wolfurt"},
                        follow_redirects=False)
        assert r.status_code in (302, 401, 403)


# ── Geocoding-Service (mit Mock) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_geocode_returns_none_on_http_error():
    from app.services.geocoding import geocode_address
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = Exception("Connection error")
        result = await geocode_address("Musterstraße", "1", "Wolfurt")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_returns_none_on_empty_result():
    from app.services.geocoding import geocode_address
    with patch("httpx.AsyncClient") as mock_cls:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: []
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await geocode_address("UnbekannteAdresse", "99999", "NichtExistierend")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_parses_valid_response():
    from app.services.geocoding import geocode_address
    nominatim_response = [{"lat": "47.4664", "lon": "9.7416", "display_name": "Wolfurt, Vorarlberg"}]
    with patch("httpx.AsyncClient") as mock_cls:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: nominatim_response
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await geocode_address("Bahnhofstraße", "1", "Wolfurt")
    assert result is not None
    assert abs(result.lat - 47.4664) < 0.001
    assert abs(result.lng - 9.7416) < 0.001
    assert "Wolfurt" in result.display_name
