"""Tests für den Hydranten-/Löschwasser-Layer (OSM/OSMHydrant) und die
Einsatzinfo-Verdrahtung (QR-Ziel, SMS-{link}-Platzhalter, Routen-Registrierung)."""
import asyncio
import importlib.util
import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

from app.config import settings
from app.services import hydrant_service as hs
from app.services import shared_cache as sc

# ── Reine Parsing-/Distanz-Funktionen ──────────────────────────────────────────

def test_typ_aus_tags_ueberflur_unterflur_loeschwasser():
    assert hs._typ_aus_tags({"emergency": "fire_hydrant", "fire_hydrant:type": "pillar"}) == "ueberflur"
    assert hs._typ_aus_tags({"emergency": "fire_hydrant", "fire_hydrant:type": "underground"}) == "unterflur"
    # Ohne Typ-Tag: Default Überflur
    assert hs._typ_aus_tags({"emergency": "fire_hydrant"}) == "ueberflur"
    # Saugstelle / Löschteich → loeschwasser
    assert hs._typ_aus_tags({"emergency": "suction_point"}) == "loeschwasser"
    assert hs._typ_aus_tags({"emergency": "fire_water_pond"}) == "loeschwasser"


def test_parse_overpass_elements_sortiert_und_dedupliziert():
    lat, lng = 47.465, 9.750
    elements = [
        {"id": 1, "lat": 47.470, "lon": 9.755, "tags": {"emergency": "fire_hydrant"}},        # weiter weg
        {"id": 2, "lat": 47.4651, "lon": 9.7501, "tags": {"emergency": "fire_hydrant",
                                                           "fire_hydrant:type": "underground"}},  # nah
        {"id": 2, "lat": 47.4651, "lon": 9.7501, "tags": {"emergency": "fire_hydrant"}},        # Duplikat (id=2)
        {"id": 3, "tags": {"emergency": "fire_hydrant"}},                                        # ohne Koordinaten → raus
    ]
    res = hs.parse_overpass_elements(elements, lat, lng)
    assert [h["id"] for h in res] == ["osm-2", "osm-1"]          # sortiert nach Entfernung, dedupliziert
    assert res[0]["typ"] == "unterflur"
    assert res[0]["entfernung_m"] < res[1]["entfernung_m"]
    assert res[0]["quelle"] == "osm"
    assert res[0]["richtung"] in ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def test_parse_overpass_elements_respektiert_max_results():
    """Einsatzinfo (2-km-Radius) darf mehr als den Standard-Cap liefern; parse_overpass
    deckelt auf das übergebene max_results (None → Standard HYDRANT_MAX)."""
    lat, lng = 47.465, 9.750
    elements = [
        {"id": i, "lat": 47.465 + i * 0.001, "lon": 9.750, "tags": {"emergency": "fire_hydrant"}}
        for i in range(1, 11)
    ]
    assert len(hs.parse_overpass_elements(elements, lat, lng, max_results=3)) == 3
    assert len(hs.parse_overpass_elements(elements, lat, lng, max_results=50)) == 10


def test_overpass_query_nutzt_radius_und_max():
    """Der 2-km-Radius und das erhöhte Limit landen im Overpass-Query."""
    q = hs._overpass_query(47.465, 9.750, 2000, 120)
    assert "around:2000" in q
    assert "out body 240;" in q  # max_results * 2


def test_manuelle_objekt_hydranten_mapping_und_distanz():
    karten = [
        SimpleNamespace(id=10, typ="hydrant_ueberflur", lat=47.4655, lng=9.7505, label="H1"),
        SimpleNamespace(id=11, typ="hydrant_unterflur", lat=47.4660, lng=9.7510, label=None),
        SimpleNamespace(id=12, typ="feuerloescher", lat=47.4661, lng=9.7511, label="kein Hydrant"),
    ]
    res = hs.manuelle_objekt_hydranten(karten, 47.465, 9.750)
    assert [h["typ"] for h in res] == ["ueberflur", "unterflur"]  # feuerloescher ignoriert
    assert all(h["quelle"] == "objekt" for h in res)
    assert res[0]["entfernung_m"] is not None and res[0]["entfernung_m"] >= 0


def test_manuelle_objekt_hydranten_ohne_bezugspunkt():
    karten = [SimpleNamespace(id=10, typ="hydrant_ueberflur", lat=47.4655, lng=9.7505, label="H1")]
    res = hs.manuelle_objekt_hydranten(karten, None, None)
    assert res[0]["entfernung_m"] is None and res[0]["richtung"] is None


def test_merge_hydranten_sortiert_none_ans_ende():
    osm = [{"id": "osm-1", "entfernung_m": 120}, {"id": "osm-2", "entfernung_m": 30}]
    manuell = [{"id": "objekt-1", "entfernung_m": None}]
    res = hs.merge_hydranten(osm, manuell)
    assert [h["id"] for h in res] == ["osm-2", "osm-1", "objekt-1"]


# ── Verdrahtung: SMS-{link}-Platzhalter + Routen-Registrierung ──────────────────

def test_sms_link_platzhalter():
    from app.services.sms_dispatch_service import default_einsatzinfo_template, render_template
    assert "{link}" in default_einsatzinfo_template()
    text = render_template("Einsatz {stichwort}: {adresse}. {link}", {
        "stichwort": "B2", "adresse": "Senderstr. 1", "link": "https://x/alarm/abc",
    })
    assert text == "Einsatz B2: Senderstr. 1. https://x/alarm/abc"


def test_neue_routen_registriert():
    from app.main import app
    from tests.conftest import all_app_paths
    pfade = all_app_paths(app)
    assert "/einsatz/{incident_id}/info" in pfade
    assert "/einsatz/{incident_id}/hydranten.json" in pfade
    assert "/objekte/{objekt_id}/hydranten.json" in pfade
    assert "/objekte/{objekt_id}/einsatz-fragment" in pfade
    assert "/alarm/{token}/hydranten.json" in pfade


# Fehler- und Parallelbetrieb mit kontrollierter Uhr und ohne Overpass-Netzverkehr.

@pytest.fixture
def hydranten_umgebung(monkeypatch):
    hs._cache.clear()
    hs._flights.clear()
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(hs.random, "uniform", lambda *args: 1)
    uhr = [time.time()]
    monkeypatch.setattr(hs.time, "time", lambda: uhr[0])
    yield uhr
    hs._cache.clear()
    hs._flights.clear()


@pytest.fixture
def redis_hydranten(hydranten_umgebung, monkeypatch):
    verbindung = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(sc, "client", lambda: verbindung)
    return verbindung


def _antwort(status=200, headers=None):
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://test"),
                          json={"elements": [{"id": 1, "lat": 47.4651, "lon": 9.7501,
                                              "tags": {"emergency": "fire_hydrant"}}]})


def _upstream(monkeypatch, antwort=None, fehler=None):
    abfrage = AsyncMock(return_value=antwort or _antwort(), side_effect=fehler)
    monkeypatch.setattr(httpx.AsyncClient, "post", abfrage)
    return abfrage


async def _altbestand(uhr, monkeypatch):
    abfrage = _upstream(monkeypatch)
    frisch = await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    uhr[0] += settings.HYDRANT_CACHE_TTL_SECONDS + 1
    abfrage.reset_mock()
    return frisch


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_fehler_liefert_altdaten_und_backoff(status, hydranten_umgebung, monkeypatch, caplog):
    frisch = await _altbestand(hydranten_umgebung, monkeypatch)
    abfrage = _upstream(monkeypatch, _antwort(status))
    for _ in range(2):
        ergebnis = await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
        assert ergebnis.items == frisch.items
        assert ergebnis.stand == frisch.stand
        assert ergebnis.veraltet and ergebnis.quelle == "veraltet"
    assert abfrage.await_count == 1
    assert sum("klasse=HTTPStatusError" in text for text in caplog.messages) == 1
    assert "https://test" not in caplog.text


@pytest.mark.parametrize("fehler", [httpx.ReadTimeout("geheim"), httpx.ConnectError("geheim")])
async def test_transportfehler_backoff(fehler, hydranten_umgebung, monkeypatch, caplog):
    await _altbestand(hydranten_umgebung, monkeypatch)
    abfrage = _upstream(monkeypatch, fehler=fehler)
    assert (await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)).veraltet
    await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    assert abfrage.await_count == 1
    assert "geheim" not in caplog.text


@pytest.mark.parametrize("retry,erwartet", [("300", 300), ("9000", 900), ("ungueltig", 60)])
async def test_retry_after(retry, erwartet, hydranten_umgebung, monkeypatch):
    abfrage = _upstream(monkeypatch, _antwort(429, {"Retry-After": retry}))
    await hs.fetch_osm_hydranten_ergebnis(47, 9)
    hydranten_umgebung[0] += erwartet - 1
    await hs.fetch_osm_hydranten_ergebnis(47, 9)
    assert abfrage.await_count == 1
    hydranten_umgebung[0] += 2
    await hs.fetch_osm_hydranten_ergebnis(47, 9)
    assert abfrage.await_count == 2


async def test_backoff_verdoppelt_bis_maximum(hydranten_umgebung, monkeypatch):
    monkeypatch.setattr(settings, "HYDRANT_BREAKER_FAILS", 100)
    _upstream(monkeypatch, _antwort(502))
    for pause in [60, 120, 240, 480, 900, 900]:
        await hs.fetch_osm_hydranten_ergebnis(47, 9)
        negativ = hs._lokal_lesen("hydranten:47.0000:9.0000:300:40:negativ")
        assert negativ["bis"] - hydranten_umgebung[0] == pause
        hydranten_umgebung[0] += pause + 1


async def test_breaker_global_oeffnet_und_half_open_schliesst(redis_hydranten, hydranten_umgebung, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=hs.__name__)
    abfrage = _upstream(monkeypatch, _antwort(502))
    for lat in [47, 48, 49]:
        await hs.fetch_osm_hydranten_ergebnis(lat, 9)
    hs._cache.clear()  # Ein anderer Worker muss den globalen Breaker beachten.
    await hs.fetch_osm_hydranten_ergebnis(50, 9)
    assert abfrage.await_count == 3
    hydranten_umgebung[0] += settings.HYDRANT_BREAKER_OPEN_SECONDS + 1
    abfrage.return_value = _antwort()
    await asyncio.gather(*(hs.fetch_osm_hydranten_ergebnis(50, 9) for _ in range(20)))
    assert abfrage.await_count == 4
    assert caplog.messages.count("Hydranten-Breaker offen") == 1
    assert caplog.messages.count("Hydranten-Breaker geschlossen") == 1
    hs._cache.clear()
    await hs.fetch_osm_hydranten_ergebnis(51, 9)
    assert abfrage.await_count == 5


async def test_single_flight_lokal(hydranten_umgebung, monkeypatch):
    abfrage = _upstream(monkeypatch)
    ergebnisse = await asyncio.gather(*(hs.fetch_osm_hydranten_ergebnis(47, 9) for _ in range(20)))
    assert abfrage.await_count == 1
    assert all(e.items == ergebnisse[0].items for e in ergebnisse)
    assert len(hs._flights.get(asyncio.get_running_loop(), {})) == 0
    assert await hs.fetch_osm_hydranten(47, 9) == ergebnisse[0].items


async def test_single_flight_redis_zwei_worker(redis_hydranten, monkeypatch):
    # Zweites Modul mit eigenem Cache/Sperren simuliert einen getrennten Worker.
    spezifikation = importlib.util.spec_from_file_location("hydrant_worker", Path(hs.__file__))
    worker = importlib.util.module_from_spec(spezifikation)
    monkeypatch.setitem(sys.modules, "hydrant_worker", worker)
    spezifikation.loader.exec_module(worker)
    abfrage = _upstream(monkeypatch)
    ergebnisse = await asyncio.gather(*(
        (hs if i % 2 else worker).fetch_osm_hydranten_ergebnis(47, 9) for i in range(20)
    ))
    assert abfrage.await_count == 1
    assert all(e.items == ergebnisse[0].items for e in ergebnisse)
    assert any(e.quelle == "redis" for e in ergebnisse)


async def test_redis_stale_hat_vorrang_und_fehlercache_gilt_workeruebergreifend(redis_hydranten, monkeypatch):
    key = "hydranten-stale:47.0000:9.0000:300:40"
    stand = datetime(2026, 1, 1, tzinfo=UTC)
    hs._lokal_setzen(key, {"items": [{"id": "lokal"}], "stand": stand}, 100)
    await sc.set(key, {"items": [{"id": "redis"}], "stand": stand}, 100)
    abfrage = _upstream(monkeypatch, _antwort(502))
    erstes = await hs.fetch_osm_hydranten_ergebnis(47, 9)
    hs._cache.clear()
    zweites = await hs.fetch_osm_hydranten_ergebnis(47, 9)
    assert erstes.items == zweites.items == [{"id": "redis"}]
    assert zweites.stand == stand and zweites.veraltet
    assert abfrage.await_count == 1


async def test_locktimeout_liefert_fallback(redis_hydranten, hydranten_umgebung, monkeypatch):
    frisch = await _altbestand(hydranten_umgebung, monkeypatch)
    abfrage = _upstream(monkeypatch)

    @asynccontextmanager
    async def belegt(key):
        raise sc.LockTimeout()
        yield

    monkeypatch.setattr(sc, "lock", belegt)
    alt = await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    assert alt.items == frisch.items and alt.veraltet
    abfrage.assert_not_awaited()


async def test_gesamtbudget_begrenzt_sperrenwartezeit(hydranten_umgebung, monkeypatch):
    frisch = await _altbestand(hydranten_umgebung, monkeypatch)
    original_timeout = asyncio.timeout
    budgets = []

    def kurze_frist(budget):
        budgets.append(budget)
        return original_timeout(0.01)

    monkeypatch.setattr(hs.asyncio, "timeout", kurze_frist)

    @asynccontextmanager
    async def belegt(key):
        await asyncio.Event().wait()
        yield

    monkeypatch.setattr(hs, "_sperre", belegt)
    abfrage = _upstream(monkeypatch)
    start = time.monotonic()
    alt = await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    assert time.monotonic() - start < 5
    assert budgets == [10]
    assert alt.items == frisch.items and alt.veraltet
    abfrage.assert_not_awaited()


async def test_redis_fehler_bleiben_lokal(hydranten_umgebung, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    monkeypatch.setattr(sc, "client", lambda: (_ for _ in ()).throw(OSError("geheim")))
    abfrage = _upstream(monkeypatch)
    ergebnis = await hs.fetch_osm_hydranten_ergebnis(47, 9)
    assert ergebnis.quelle == "frisch"
    assert abfrage.await_count == 1


async def test_limit_ist_teil_des_cache_schluessels(hydranten_umgebung, monkeypatch):
    abfrage = _upstream(monkeypatch)
    await hs.fetch_osm_hydranten(47, 9, max_results=1)
    await hs.fetch_osm_hydranten(47, 9, max_results=2)
    assert abfrage.await_count == 2


def test_einsatzinfo_altdaten_bei_overpass_ausfall(hydranten_umgebung, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.main import app
    from app.models.incident import Incident
    from tests.test_einsatzinfo_pr11 import _login, _setup_incident

    _, incident_id, _ = _setup_incident("hydranten_ausfall")
    stand = datetime(2026, 1, 1, tzinfo=UTC)
    gespeichert = json.dumps([{"id": "osm-alt", "entfernung_m": 1, "typ": "ueberflur"}])
    with SessionLocal() as db:
        set_tenant_context(db, None)
        incident = db.get(Incident, incident_id)
        incident.hydranten_json = gespeichert
        incident.hydranten_stand = stand
        db.commit()
    _upstream(monkeypatch, _antwort(502))
    with TestClient(app) as client:
        _login(client, "hydranten_ausfall", "Test1234!")
        assert client.get(f"/einsatz/{incident_id}/info").status_code == 200
        antwort = client.get(f"/einsatz/{incident_id}/hydranten.json")
        assert antwort.status_code == 200
        assert antwort.json()["veraltet"]
        assert "veraltet" in antwort.json()["stand"]
        assert antwort.json()["hydranten"][0]["id"] == "osm-alt"
        # Auch ein Treffer aus dem Stale-Cache darf den Einsatz nicht ueberschreiben.
        stale = hs.HydrantErgebnis(
            [{"id": "osm-stale", "entfernung_m": 2}], True,
            datetime(2026, 2, 1, tzinfo=UTC), "veraltet",
        )
        monkeypatch.setattr(hs, "fetch_osm_hydranten_ergebnis", AsyncMock(return_value=stale))
        antwort = client.get(f"/einsatz/{incident_id}/hydranten.json")
        assert antwort.status_code == 200
        assert antwort.json()["veraltet"]
        assert antwort.json()["hydranten"][0]["id"] == "osm-stale"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        incident = db.get(Incident, incident_id)
        assert incident.hydranten_json == gespeichert
        assert incident.hydranten_stand.replace(tzinfo=UTC) == stand


async def test_redis_single_flight_zwei_event_loops(hydranten_umgebung, monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://test")
    clients = threading.local()

    def verbindung():
        if not hasattr(clients, "redis"):
            clients.redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        return clients.redis

    monkeypatch.setattr(sc, "client", verbindung)
    spezifikation = importlib.util.spec_from_file_location("hydrant_worker_loops", Path(hs.__file__))
    worker = importlib.util.module_from_spec(spezifikation)
    monkeypatch.setitem(sys.modules, "hydrant_worker_loops", worker)
    spezifikation.loader.exec_module(worker)
    zaehler = [0]
    zaehler_sperre = threading.Lock()
    start = threading.Barrier(2)

    async def abfrage(*args, **kwargs):
        with zaehler_sperre:
            zaehler[0] += 1
        await asyncio.sleep(0.05)
        return _antwort()

    monkeypatch.setattr(httpx.AsyncClient, "post", abfrage)

    def ausfuehren(modul):
        async def parallel():
            start.wait(timeout=3)
            try:
                return await asyncio.gather(*(modul.fetch_osm_hydranten_ergebnis(47, 9) for _ in range(10)))
            finally:
                await verbindung().aclose()
        return asyncio.run(parallel())

    links, rechts = await asyncio.gather(asyncio.to_thread(ausfuehren, hs), asyncio.to_thread(ausfuehren, worker))
    assert zaehler[0] == 1
    assert all(e.items == links[0].items and not e.veraltet for e in links + rechts)


async def test_request_gesamtbudget_setzt_fehlercache(hydranten_umgebung, monkeypatch):
    frisch = await _altbestand(hydranten_umgebung, monkeypatch)
    original_timeout = asyncio.timeout
    budgets = []

    def kurze_frist(budget):
        budgets.append(budget)
        return original_timeout(0.04 if len(budgets) == 1 else 0.01)

    async def haengt(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(hs.asyncio, "timeout", kurze_frist)
    abfrage = _upstream(monkeypatch, fehler=haengt)
    start = time.monotonic()
    alt = await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    assert time.monotonic() - start < 5
    assert alt.items == frisch.items and alt.veraltet
    await hs.fetch_osm_hydranten_ergebnis(47.465, 9.75)
    assert abfrage.await_count == 1
    assert budgets[0] == 10 and 0 < budgets[1] < 10


async def test_breaker_zaehlt_fehler_im_zeitfenster_auch_mit_zwischenerfolg(hydranten_umgebung, monkeypatch):
    abfrage = _upstream(monkeypatch, _antwort(502))
    await hs.fetch_osm_hydranten_ergebnis(47, 9)
    abfrage.return_value = _antwort()
    await hs.fetch_osm_hydranten_ergebnis(48, 9)
    abfrage.return_value = _antwort(502)
    for lat in [49, 50, 51]:
        await hs.fetch_osm_hydranten_ergebnis(lat, 9)
    assert abfrage.await_count == 4
    hydranten_umgebung[0] += settings.HYDRANT_BREAKER_OPEN_SECONDS + 1
    await asyncio.gather(*(hs.fetch_osm_hydranten_ergebnis(52 + i, 9) for i in range(20)))
    # Genau eine fehlgeschlagene Half-Open-Probe, danach wieder global offen.
    assert abfrage.await_count == 5


def test_retry_after_http_datum_und_jitter(hydranten_umgebung, monkeypatch):
    from email.utils import format_datetime

    antwort = _antwort(429, {"Retry-After": format_datetime(
        datetime.fromtimestamp(hydranten_umgebung[0] + 300, UTC), usegmt=True,
    )})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        antwort.raise_for_status()
    monkeypatch.setattr(hs.random, "uniform", lambda *args: 0.8)
    assert 299 <= hs._wartezeit(exc.value, 1) <= 300
    assert hs._wartezeit(httpx.ReadTimeout("Zeitlimit"), 1) == 48
    monkeypatch.setattr(hs.random, "uniform", lambda *args: 1.2)
    assert hs._wartezeit(httpx.ReadTimeout("Zeitlimit"), 1) == 72
