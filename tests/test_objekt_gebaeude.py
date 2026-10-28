"""Overpass ist immer gemockt; Fehler liefern niemals erfundene Ecken."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.services import hydrant_service as overpass
from app.services.objekt_gebaeude_service import gebaeude_umrisse
from app.services.objekt_karte_service import ObjektKartenFehler


@pytest.mark.asyncio
async def test_gebaeude_cache_ecken_und_fehler(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    ring = [{"lat": 47, "lon": 9}, {"lat": 47.001, "lon": 9}, {"lat": 47, "lon": 9.001}, {"lat": 47, "lon": 9}]
    antwort = httpx.Response(
        200, json={"elements": [{"id": 123, "geometry": ring}]}, request=httpx.Request("POST", "https://overpass.test")
    )
    post = AsyncMock(return_value=antwort)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    a = await gebaeude_umrisse(47, 9)
    assert a["gebaeude"][0]["ecken"][0] == {"nummer": 1, "lat": 47, "lng": 9}
    assert len(a["gebaeude"][0]["ecken"]) == 3
    assert await gebaeude_umrisse(47, 9) == a
    assert post.await_count == 1
    assert 'way["building"]' in post.call_args.kwargs["data"]["data"]
    post.side_effect = httpx.ConnectError("offline")
    with pytest.raises(ObjektKartenFehler, match="fehlgeschlagen"):
        await gebaeude_umrisse(48, 10)
    with pytest.raises(ObjektKartenFehler, match="derzeit nicht erreichbar"):
        await gebaeude_umrisse(48, 10)
    assert post.await_count == 2
    with pytest.raises(ObjektKartenFehler, match="radius_m"):
        await gebaeude_umrisse(47, 9, 5000)
