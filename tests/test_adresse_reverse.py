"""Tests fuer Reverse-Geocoding und den gemeinsamen Picker-Endpunkt."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services.geocoding import ReverseGeocodeResult, reverse_geocode


def _login(client, username):
    client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )


def _user(username, role):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=username, name=username, city="Teststadt", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password("Test1234!"),
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        rolle = db.query(Role).filter(Role.code == role).first()
        if rolle is None:
            rolle = Role(code=role, label=role)
            db.add(rolle)
            db.flush()
        db.add(UserRole(user_id=user.id, role_id=rolle.id))
        db.commit()
    finally:
        db.close()


def _mock_client(data):
    response = AsyncMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=data)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_reverse_geocode_parst_road_hausnummer_und_town(monkeypatch):
    async def no_throttle():
        return None

    monkeypatch.setattr("app.services.geocoding._throttle", no_throttle)
    data = {
        "display_name": "Hauptstrasse 7, Teststadt",
        "address": {"road": "Hauptstrasse", "house_number": "7", "town": "Teststadt"},
    }
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=_mock_client(data))
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await reverse_geocode(47.5, 9.7)
    assert result == ReverseGeocodeResult("Hauptstrasse", "7", "Teststadt", "Hauptstrasse 7, Teststadt")


@pytest.mark.asyncio
async def test_reverse_geocode_nutzt_fallback_felder(monkeypatch):
    async def no_throttle():
        return None

    monkeypatch.setattr("app.services.geocoding._throttle", no_throttle)
    data = {"display_name": "Platz, Dorf", "address": {"square": "Marktplatz", "village": "Dorf"}}
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=_mock_client(data))
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await reverse_geocode(47.5, 9.7)
    assert result == ReverseGeocodeResult("Marktplatz", None, "Dorf", "Platz, Dorf")


@pytest.mark.asyncio
async def test_reverse_geocode_gibt_bei_fehler_none_zurueck(monkeypatch):
    async def no_throttle():
        return None

    monkeypatch.setattr("app.services.geocoding._throttle", no_throttle)
    with patch("httpx.AsyncClient", side_effect=RuntimeError("offline")):
        assert await reverse_geocode(47.5, 9.7) is None


def test_reverse_route_erlaubt_recorder(client, setup_db, monkeypatch):
    _user("reverse_recorder", "recorder")

    async def reverse(*_args):
        return ReverseGeocodeResult("Hauptstrasse", "7", "Teststadt", "Hauptstrasse 7, Teststadt")

    monkeypatch.setattr("app.services.geocoding.reverse_geocode", reverse)
    _login(client, "reverse_recorder")
    response = client.post(
        "/adresse/reverse", data={"_csrf": client.cookies.get("ec_csrf"), "lat": "47.5", "lng": "9.7"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "street": "Hauptstrasse",
        "house_number": "7",
        "city": "Teststadt",
        "display_name": "Hauptstrasse 7, Teststadt",
    }


def test_reverse_route_sperrt_readonly_und_validiert_koordinaten(client, setup_db, monkeypatch):
    _user("reverse_readonly", "readonly")
    _login(client, "reverse_readonly")
    response = client.post(
        "/adresse/reverse", data={"_csrf": client.cookies.get("ec_csrf"), "lat": "47.5", "lng": "9.7"}
    )
    assert response.status_code == 403
    client.get("/logout")

    _user("reverse_invalid", "recorder")
    _login(client, "reverse_invalid")
    response = client.post(
        "/adresse/reverse", data={"_csrf": client.cookies.get("ec_csrf"), "lat": "200", "lng": "9.7"}
    )
    assert response.status_code == 422

    async def no_result(*_args):
        return None

    monkeypatch.setattr("app.services.geocoding.reverse_geocode", no_result)
    response = client.post(
        "/adresse/reverse", data={"_csrf": client.cookies.get("ec_csrf"), "lat": "47.5", "lng": "9.7"}
    )
    assert response.status_code == 404
