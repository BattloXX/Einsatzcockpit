"""Endpunktwechsel und Fehlerschutz ohne echte Overpass-Anfragen."""

import hashlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.services import hydrant_service as hs
from app.services import overpass_endpunkte as oe
from tests.test_hydranten import hydranten_umgebung  # noqa: F401

PRIMAER = "https://primaer.test/api/interpreter"
FALLBACK = "https://fallback.test/api/interpreter"
_ENDPUNKT_FEHLER = oe._fehler


@pytest.fixture(autouse=True)
def umgebung(request, monkeypatch):
    uhr = request.getfixturevalue("hydranten_umgebung")
    monkeypatch.setattr(oe, "_fehler", _ENDPUNKT_FEHLER)
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", FALLBACK)
    monkeypatch.setattr(settings, "OVERPASS_ENDPUNKT_FEHLER", 2)
    return uhr


def _antwort(status=200, daten=None, headers=None):
    return httpx.Response(status, json=daten if daten is not None else {"elements": []},
                          headers=headers, request=httpx.Request("POST", PRIMAER))


async def _abfragen(budget=10):
    return await oe.abfragen("query", primaer=PRIMAER, timeout_je_versuch=8, budget_s=budget, zweck="test")


def test_endpunkte(monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS",
                        f" {FALLBACK},http://unsicher.test,{PRIMAER},{FALLBACK},,https://")
    assert oe.endpunkte(f" {PRIMAER} ") == [PRIMAER, FALLBACK]
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")
    assert oe.endpunkte(PRIMAER) == [PRIMAER]
    # Konfigurierte Primaer-URL gilt immer, z. B. eine interne http-Instanz.
    assert oe.endpunkte("http://intern.test/api/interpreter") == ["http://intern.test/api/interpreter"]


@pytest.mark.parametrize("fehler", [
    _antwort(502), _antwort(503), _antwort(504), httpx.ReadTimeout("langsam"), httpx.ConnectError("offline"),
    _antwort(daten={"remark": "runtime error: Query timed out"}),
    _antwort(daten={"remark": "timeout"}),
    httpx.Response(200, text="kein JSON", request=httpx.Request("POST", PRIMAER)),
])
async def test_fallback(fehler, monkeypatch, caplog):
    daten = {"elements": [{"id": 2}]}
    post = AsyncMock(side_effect=[fehler, _antwort(daten=daten)])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    caplog.set_level(logging.INFO, logger=oe.__name__)
    assert await _abfragen() == daten
    assert [aufruf.args[0] for aufruf in post.call_args_list] == [PRIMAER, FALLBACK]
    assert "zweck=test host=fallback.test" in caplog.text
    assert PRIMAER not in caplog.text and FALLBACK not in caplog.text


@pytest.mark.parametrize("status", [400, 406])
async def test_abfragefehler_sofort(status, monkeypatch):
    post = AsyncMock(return_value=_antwort(status))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(oe.OverpassAbfrageFehler) as fehler:
        await _abfragen()
    assert fehler.value.code == "OVERPASS_HTTP_4XX"
    assert post.await_count == 1
    assert not hs._cache


async def test_fehlerschutz_und_pause(umgebung, monkeypatch):
    async def post(self, url, **kwargs):
        aufrufe.append(url)
        return _antwort(502 if url == PRIMAER else 200)

    aufrufe = []
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    await _abfragen()
    await _abfragen()
    await _abfragen()
    assert aufrufe == [PRIMAER, FALLBACK, PRIMAER, FALLBACK, FALLBACK]
    umgebung[0] += settings.OVERPASS_ENDPUNKT_PAUSE_SECONDS + 1
    await _abfragen()
    assert aufrufe[-2:] == [PRIMAER, FALLBACK]


@pytest.mark.parametrize("retry", ["600", "Thu, 01 Jan 2099 00:00:00 GMT"])
async def test_retry_after_mindestpause(retry, umgebung, monkeypatch):
    post = AsyncMock(side_effect=[_antwort(429, headers={"Retry-After": retry}), _antwort(), _antwort()])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    await _abfragen()
    key = "overpass-endpunkt:" + hashlib.sha256(PRIMAER.encode()).hexdigest()
    zustand = await hs._lesen(key)
    assert zustand["offen_bis"] >= umgebung[0] + 600
    await _abfragen()
    assert post.await_count == 3  # Nur der Fallback wird erneut aufgerufen.


async def test_alle_scheitern(monkeypatch):
    post = AsyncMock(return_value=_antwort(502))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(oe.OverpassNichtVerfuegbar) as fehler:
        await _abfragen()
    assert fehler.value.versuchte == 2
    assert isinstance(fehler.value.letzter_fehler, httpx.HTTPStatusError)


async def test_budget_erschoepft(monkeypatch):
    uhr = [0.0]
    monkeypatch.setattr(oe, "time", SimpleNamespace(monotonic=lambda: uhr[0], time=hs.time.time))

    async def post(self, url, **kwargs):
        uhr[0] += 9.5
        return _antwort(502)

    abfrage = AsyncMock(side_effect=post)
    # AsyncMock als Klassenattribut bindet self nicht.
    async def gebunden(self, url, **kwargs):
        return await abfrage(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", gebunden)
    with pytest.raises(oe.OverpassNichtVerfuegbar) as fehler:
        await _abfragen()
    assert fehler.value.versuchte == 1
    assert abfrage.await_count == 1
    with pytest.raises(oe.OverpassNichtVerfuegbar) as fehler:
        await _abfragen(budget=0.5)
    assert fehler.value.versuchte == 0


async def test_erfolg_setzt_fehler_zurueck(monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")
    post = AsyncMock(side_effect=[_antwort(502), _antwort(), _antwort(502), _antwort()])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for _ in range(2):
        with pytest.raises(oe.OverpassNichtVerfuegbar):
            await _abfragen()
        await _abfragen()
    assert post.await_count == 4


def _mit_ursache(fehler, ursache, kontext=False):
    if kontext:
        fehler.__context__ = ursache
    else:
        fehler.__cause__ = ursache
    return fehler


@pytest.mark.parametrize("fehler,code", [
    (httpx.ConnectTimeout("langsam"), "OVERPASS_TIMEOUT"),
    (httpx.ReadTimeout("langsam"), "OVERPASS_TIMEOUT"),
    (TimeoutError(), "OVERPASS_TIMEOUT"),
    (httpx.ConnectError("Name or service not known"), "OVERPASS_DNS_ERROR"),
    (httpx.ConnectError("Temporary failure in name resolution"), "OVERPASS_DNS_ERROR"),
    (httpx.ConnectError("offline"), "OVERPASS_CONNECTION_ERROR"),
    (_antwort(429, headers={"Retry-After": "60"}), "OVERPASS_RATE_LIMIT"),
    *[(_antwort(status), "OVERPASS_HTTP_5XX") for status in (500, 502, 503, 504)],
    (_antwort(daten=[]), "OVERPASS_INVALID_RESPONSE"),
    (_antwort(daten={"remark": "timeout"}), "OVERPASS_INVALID_RESPONSE"),
    (_antwort(daten={"remark": "runtime error"}), "OVERPASS_INVALID_RESPONSE"),
    (httpx.Response(200, text="kaputt", request=httpx.Request("POST", PRIMAER)), "OVERPASS_INVALID_RESPONSE"),
])
async def test_fehlercodes(fehler, code, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=[fehler, fehler]))
    with pytest.raises(oe.OverpassNichtVerfuegbar) as ausfall:
        await _abfragen()
    assert ausfall.value.code == "OVERPASS_ALL_ENDPOINTS_FAILED"
    assert len(ausfall.value.versuche) == 2
    for versuch in ausfall.value.versuche:
        assert versuch["code"] == code
        assert versuch["host"] in {"primaer.test", "fallback.test"}
        assert versuch["dauer_ms"] >= 0
        assert not versuch["uebersprungen"]
        assert versuch["http_status"] == (fehler.status_code if isinstance(fehler, httpx.Response) else None)


@pytest.mark.parametrize("kontext", [False, True])
async def test_ursachenkette(kontext, monkeypatch):
    import socket
    import ssl

    for ursache, code in ((socket.gaierror("unbekannt"), "OVERPASS_DNS_ERROR"),
                          (ssl.SSLError("zertifikat"), "OVERPASS_TLS_ERROR")):
        hs._cache.clear()
        fehler = _mit_ursache(httpx.ConnectError("Verbindung"),
                              _mit_ursache(OSError("Zwischenschicht"), ursache, kontext), kontext)
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(side_effect=fehler))
        with pytest.raises(oe.OverpassNichtVerfuegbar) as ausfall:
            await _abfragen()
        assert all(v["code"] == code for v in ausfall.value.versuche)


async def test_uebersprungene_endpunkte(monkeypatch):
    post = AsyncMock(return_value=_antwort(502))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for _ in range(3):
        with pytest.raises(oe.OverpassNichtVerfuegbar) as ausfall:
            await _abfragen()
    assert ausfall.value.versuchte == 0
    assert len(ausfall.value.versuche) == 2
    assert all(v["uebersprungen"] and v["code"] == "OVERPASS_HTTP_5XX" for v in ausfall.value.versuche)
