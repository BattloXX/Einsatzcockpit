"""Overpass ist immer gemockt; Fehler liefern niemals erfundene Ecken."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.mcp.fehler import ExternerDienstFehler
from app.services import hydrant_service as overpass
from app.services.objekt_gebaeude_service import gebaeude_umrisse
from app.services.objekt_karte_service import ObjektKartenFehler
from tests.test_osm_gebaeude_import import osm_umgebung as osm_umgebung


@pytest.mark.asyncio
async def test_gebaeude_cache_ecken_und_fehler(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")
    ring = [{"lat": 47, "lon": 9}, {"lat": 47.001, "lon": 9}, {"lat": 47, "lon": 9.001}, {"lat": 47, "lon": 9}]
    antwort = httpx.Response(
        200, json={"elements": [{"id": 123, "geometry": ring}]}, request=httpx.Request("POST", "https://overpass.test")
    )
    post = AsyncMock(return_value=antwort)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    a = await gebaeude_umrisse(47, 9)
    assert a["gebaeude"][0]["ecken"][0] == {"nummer": 1, "lat": 47, "lng": 9}
    assert len(a["gebaeude"][0]["ecken"]) == 3
    assert a["cache_status"] == "miss"
    assert await gebaeude_umrisse(47, 9) == a | {"cache_status": "hit"}
    assert post.await_count == 1
    assert 'way["building"]' in post.call_args.kwargs["data"]["data"]
    post.side_effect = httpx.ConnectError("offline")
    with pytest.raises(ExternerDienstFehler, match=".*alle Endpunkte ausgelastet oder gesperrt, 1 versucht"):
        await gebaeude_umrisse(48, 10)
    with pytest.raises(ExternerDienstFehler, match=".*derzeit nicht erreichbar"):
        await gebaeude_umrisse(48, 10)
    assert post.await_count == 2
    with pytest.raises(ObjektKartenFehler, match="radius_m"):
        await gebaeude_umrisse(47, 9, 5000)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_status", [200, 502])
async def test_gebaeude_fallback_trotz_hydranten_breaker(fallback_status, monkeypatch):
    import hashlib
    import time

    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    fallback = "https://fallback.test/api/interpreter"
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", fallback)
    breaker = "hydranten-breaker:" + hashlib.sha256(settings.HYDRANT_OVERPASS_URL.encode()).hexdigest()
    zustand = {"offen_bis": time.time() + 300, "fehler": [time.time()]}
    await overpass._setzen(breaker, zustand, 600)
    ring = [{"lat": 47, "lon": 9}, {"lat": 48, "lon": 9}, {"lat": 48, "lon": 10}, {"lat": 47, "lon": 9}]
    post = AsyncMock(side_effect=[
        httpx.Response(502, request=httpx.Request("POST", settings.HYDRANT_OVERPASS_URL)),
        httpx.Response(fallback_status, json={"elements": [{"id": 123, "geometry": ring}]},
                       request=httpx.Request("POST", fallback)),
    ])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    if fallback_status == 200:
        assert (await gebaeude_umrisse(47, 9))["gebaeude"][0]["osm_id"] == 123
    else:
        with pytest.raises(ExternerDienstFehler, match=".*alle Endpunkte ausgelastet oder gesperrt"):
            await gebaeude_umrisse(47, 9)
        with pytest.raises(ExternerDienstFehler, match=".*alle Endpunkte ausgelastet oder gesperrt"):
            await gebaeude_umrisse(47, 9)
    assert [aufruf.args[0] for aufruf in post.call_args_list] == [settings.HYDRANT_OVERPASS_URL, fallback]
    assert await overpass._lesen(breaker) == zustand


@pytest.mark.asyncio
async def test_gebaeude_abfragefehler_ohne_negativcache(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    post = AsyncMock(return_value=httpx.Response(400, request=httpx.Request("POST", "https://test")))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for _ in range(2):
        with pytest.raises(ExternerDienstFehler, match="fehlgeschlagen"):
            await gebaeude_umrisse(47, 9)
    assert post.await_count == 2
    assert not any(key.endswith(":negativ") for key in overpass._cache)


@pytest.mark.asyncio
async def test_strukturierte_ausfaelle_und_leerer_erfolg(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")
    post = AsyncMock(side_effect=httpx.ReadTimeout("langsam"))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for status in ("miss", "negativ_cache"):
        with pytest.raises(ExternerDienstFehler) as ausfall:
            await gebaeude_umrisse(47, 9)
        assert ausfall.value.code == "OVERPASS_ALL_ENDPOINTS_FAILED"
        assert ausfall.value.retryable
        assert ausfall.value.details["cache_status"] == status
        assert ausfall.value.details["attempted_endpoints_count"] == 1
        assert ausfall.value.details["endpunkte"][0]["code"] == "OVERPASS_TIMEOUT"
        if status == "negativ_cache":
            assert 0 < ausfall.value.details["retry_after_s"] <= 60
    assert post.await_count == 1
    overpass._cache.clear()
    post.side_effect = None
    post.return_value = httpx.Response(200, json={"elements": []}, request=httpx.Request("POST", "https://test"))
    assert await gebaeude_umrisse(47, 9) == {
        "gebaeude": [], "quelle": "OpenStreetMap / Overpass", "cache_status": "miss",
        "quelle_hinweis": "kein lokaler Bestand fuer diesen Ort",
    }
    assert (await gebaeude_umrisse(47, 9))["cache_status"] == "hit"
    overpass._cache.clear()
    post.return_value = httpx.Response(406, request=httpx.Request("POST", "https://test"))
    with pytest.raises(ExternerDienstFehler) as ausfall:
        await gebaeude_umrisse(47, 9)
    assert ausfall.value.code == "OVERPASS_HTTP_4XX" and not ausfall.value.retryable
    import asyncio

    async def langsam(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("app.services.objekt_gebaeude_service.abfragen", langsam)
    monkeypatch.setattr(settings, "OBJEKT_GEBAEUDE_TOTAL_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ExternerDienstFehler) as ausfall:
        await gebaeude_umrisse(48, 10)
    assert ausfall.value.code == "OVERPASS_TIMEOUT" and ausfall.value.retryable


@pytest.fixture
def lokaler_bestand(osm_umgebung, monkeypatch):
    from app.services import osm_gebaeude_import as osm
    from tests.test_osm_gebaeude_import import URL

    sitzung, pbf, zustand = osm_umgebung
    pbf({
        100: [[47, 9], [47.0002, 9], [47, 9.0002]],
        200: [[47.0006, 9], [47.0008, 9], [47.0006, 9.0002]],
        300: [[47.02, 9.02], [47.0202, 9.02], [47.02, 9.0202]],
    })
    lauf = osm.importieren("region", URL)
    assert lauf.status == "aktiv"
    mock = AsyncMock(side_effect=AssertionError("Overpass darf nicht aufgerufen werden"))
    monkeypatch.setattr("app.services.objekt_gebaeude_service.abfragen", mock)
    return sitzung, lauf, mock


@pytest.mark.asyncio
async def test_lokal_format_abstand_und_keine_netzabfrage(lokaler_bestand):
    _, lauf, mock = lokaler_bestand
    ergebnis = await gebaeude_umrisse(47, 9, 150)
    assert ergebnis["cache_status"] == "lokal"
    assert ergebnis["quelle"] == "OpenStreetMap (lokaler Bestand region)"
    assert ergebnis["datenstand"].endswith("Z") and not ergebnis["veraltet"]
    assert [g["osm_id"] for g in ergebnis["gebaeude"]] == [100, 200]
    assert ergebnis["gebaeude"][0]["ecken"] == [
        {"nummer": 1, "lat": 47, "lng": 9}, {"nummer": 2, "lat": 47.0002, "lng": 9},
        {"nummer": 3, "lat": 47, "lng": 9.0002},
    ]
    assert ergebnis["gebaeude"][0]["quelle"] == "osm_gebaeude"
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_lokal_leer_ist_erfolg(lokaler_bestand):
    _, _, mock = lokaler_bestand
    ergebnis = await gebaeude_umrisse(47.01, 9.01, 10)
    assert ergebnis["gebaeude"] == [] and ergebnis["cache_status"] == "lokal"
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ausserhalb_huelle_und_deaktiviert(lokaler_bestand, monkeypatch):
    _, _, mock = lokaler_bestand
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(overpass, "_cache", {})
    mock.side_effect = None
    mock.return_value = {"elements": []}
    ergebnis = await gebaeude_umrisse(48, 10)
    assert ergebnis["quelle_hinweis"] == "kein lokaler Bestand fuer diesen Ort"
    assert ergebnis["cache_status"] == "miss"
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_IMPORT_ENABLED", False)
    ergebnis = await gebaeude_umrisse(47, 9)
    assert ergebnis["quelle"] == "OpenStreetMap / Overpass" and "quelle_hinweis" not in ergebnis
    assert mock.await_count == 2


@pytest.mark.asyncio
async def test_lokal_veraltet_und_parallel(lokaler_bestand):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.models.osm import OsmImportLauf

    sitzung, lauf, mock = lokaler_bestand
    with sitzung() as db:
        db.get(OsmImportLauf, lauf.id).datenstand = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31)
        db.commit()
    ergebnisse = await asyncio.gather(*(gebaeude_umrisse(47, 9) for _ in range(5)))
    assert all(e == ergebnisse[0] for e in ergebnisse)
    assert ergebnisse[0]["veraltet"] and ergebnisse[0]["alter_tage"] == 31
    assert "warnung" in ergebnisse[0]
    mock.assert_not_awaited()
