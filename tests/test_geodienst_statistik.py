"""Persistente Zaehler, Ausfallbetrieb und tatsaechliche Endpunktwechsel."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest

from app.config import settings
from app.services import geodienst_statistik as statistik
from app.services import hydrant_service, overpass_endpunkte, shared_cache


@pytest.fixture
def redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(shared_cache, "client", lambda: client)
    statistik._lokal.clear()
    hydrant_service._cache.clear()
    monkeypatch.setattr(settings, "REDIS_URL", "")
    yield client
    statistik._lokal.clear()
    hydrant_service._cache.clear()


async def test_zaehler_ttl_und_letzte_ereignisse(redis):
    await statistik.fehler("overpass", "https://osm.test/api/interpreter?lat=47.12345", "OVERPASS_TIMEOUT", None, 55)
    await statistik.fehler("overpass", "osm.test", "OVERPASS_HTTP_5XX", 502, 66)
    await statistik.erfolg("overpass", "osm.test", 77)
    tag = datetime.now(UTC).date().isoformat()
    key = f"geodienst-stat:{tag}:overpass"
    assert json.loads(await redis.get(key)) == {"OVERPASS_TIMEOUT": 1, "OVERPASS_HTTP_5XX": 1, "ok": 1}
    gestern = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    await redis.set(f"geodienst-stat:{gestern}:overpass", json.dumps({"OVERPASS_TIMEOUT": 2}))
    host_key = "geodienst-letzt:overpass:" + hashlib.sha256(b"osm.test").hexdigest()[:16]
    assert 30 * 86400 - 10 <= await redis.ttl(key) <= 30 * 86400
    assert 30 * 86400 - 10 <= await redis.ttl(host_key) <= 30 * 86400
    statistik._lokal.clear()  # Ein anderer Worker kann die Daten ebenfalls lesen.
    daten = await statistik.zusammenfassung("overpass")
    assert daten["errors_by_code"] == {"OVERPASS_TIMEOUT": 3, "OVERPASS_HTTP_5XX": 1}
    assert (await statistik.zusammenfassung("overpass", 1))["errors_by_code"]["OVERPASS_TIMEOUT"] == 1
    assert daten["ok"] == 1 and daten["retry_count"] == 0
    assert daten["last_success"].endswith("Z") and daten["last_failure"].endswith("Z")
    assert daten["hosts"][0]["latenz_ms"] == 77
    assert daten["hosts"][0]["http_status"] is None
    assert "47.12345" not in json.dumps(daten) and "/api/" not in json.dumps(daten)


async def test_atomare_parallele_zaehler_und_cache_quote(redis):
    await asyncio.gather(*(statistik.zaehlen("gebaeude", "cache_hit") for _ in range(40)))
    await statistik.zaehlen("gebaeude", "cache_miss")
    await statistik.zaehlen("gebaeude", "cache_lokal")
    await statistik.zaehlen("gebaeude", "negativ_cache")
    daten = await statistik.zusammenfassung("gebaeude")
    assert daten["cache_hit"] == 40 and daten["cache_hit_rate"] == 40 / 41
    assert daten["errors_by_code"] == {} and daten["negativ_cache"] == 1


async def test_retry_nur_beim_tatsaechlichen_wechsel(redis, monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "https://fallback.test/api/interpreter")
    antworten = [httpx.Response(502, request=httpx.Request("POST", "https://osm.test")),
                 httpx.Response(200, json={"elements": []}, request=httpx.Request("POST", "https://fallback.test"))]
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=antworten))
    await overpass_endpunkte.abfragen("query", primaer="https://osm.test/api/interpreter",
                                     timeout_je_versuch=1, budget_s=4, zweck="test")
    daten = await statistik.zusammenfassung("overpass")
    assert daten["retry_count"] == 1 and daten["ok"] == 1
    assert daten["errors_by_code"] == {"OVERPASS_HTTP_5XX": 1}


async def test_cache_fehler_stoert_abfrage_nicht(monkeypatch, caplog):
    statistik._lokal.clear()
    monkeypatch.setattr(shared_cache, "client", lambda: (_ for _ in ()).throw(OSError("geheim")))
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")
    monkeypatch.setattr(hydrant_service, "_lesen", AsyncMock(return_value={}))
    monkeypatch.setattr(hydrant_service, "_setzen", AsyncMock())
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=httpx.Response(
        200, json={"elements": []}, request=httpx.Request("POST", "https://osm.test"))))
    caplog.set_level("DEBUG", logger=statistik.__name__)
    assert await overpass_endpunkte.abfragen("query", primaer="https://osm.test/api/interpreter",
                                            timeout_je_versuch=1, budget_s=4, zweck="test") == {"elements": []}
    await statistik.fehler("overpass", "osm.test", "OVERPASS_TIMEOUT", None, 1)
    daten = await statistik.zusammenfassung("overpass")
    assert daten["ok"] == 1 and daten["last_failure"]
    assert "nicht schreibbar" in caplog.text and "geheim" not in caplog.text
    statistik._lokal.clear()


async def test_lokaler_fallback_verfaellt(monkeypatch):
    from types import SimpleNamespace

    uhr = [1000.0]
    statistik._lokal.clear()
    monkeypatch.setattr(shared_cache, "client", lambda: None)
    monkeypatch.setattr(statistik, "time", SimpleNamespace(time=lambda: uhr[0]))
    await statistik.erfolg("overpass", "osm.test", 1)
    assert (await statistik.zusammenfassung("overpass"))["ok"] == 1
    uhr[0] += settings.GEODIENST_STATISTIK_TAGE * 86400 + 1
    daten = await statistik.zusammenfassung("overpass")
    assert daten["ok"] == 0 and daten["hosts"] == [] and daten["last_success"] is None
    statistik._lokal.clear()


@pytest.mark.parametrize("cacheart", ["cache_lokal", "cache_hit", "cache_miss", "negativ_cache"])
async def test_gebaeude_abfrage_zaehlt_cacheart(redis, monkeypatch, cacheart):
    import time

    from app.mcp.fehler import ExternerDienstFehler
    from app.services import objekt_gebaeude_service as gebaeude

    monkeypatch.setattr(settings, "OSM_GEBAEUDE_IMPORT_ENABLED", True)
    monkeypatch.setattr(gebaeude, "_lokale_umrisse", lambda *a: {"gebaeude": []} if cacheart == "cache_lokal" else None)
    antworten = [{"gebaeude": []}] if cacheart == "cache_hit" else [None, {"bis": time.time() + 60}
                 if cacheart == "negativ_cache" else {}]
    monkeypatch.setattr(hydrant_service, "_lesen", AsyncMock(side_effect=antworten))
    monkeypatch.setattr(hydrant_service, "_setzen", AsyncMock())
    monkeypatch.setattr(gebaeude, "abfragen", AsyncMock(return_value={"elements": []}))
    if cacheart == "negativ_cache":
        with pytest.raises(ExternerDienstFehler):
            await gebaeude.gebaeude_umrisse(47, 9)
    else:
        assert (await gebaeude.gebaeude_umrisse(47, 9))["gebaeude"] == []
    daten = await statistik.zusammenfassung("gebaeude")
    assert daten[cacheart] == 1
    assert sum(daten[art] for art in ("cache_lokal", "cache_hit", "cache_miss", "negativ_cache")) == 1
