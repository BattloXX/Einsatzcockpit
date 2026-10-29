"""OSM-Admincheck: Live-Proben, Maskierung, Status und lesende Uebersicht."""
import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.admin_mcp import registry, server
from app.config import settings
from app.db import SessionLocal
from app.services import geodienst_statistik, hydrant_service, osm_gebaeude_import
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call, tokens

admin_env = oauth.admin_env


@pytest.fixture
def umgebung(monkeypatch):
    monkeypatch.setattr(settings, "HYDRANT_OVERPASS_URL", "https://osm.test/api/interpreter?lat=47.12345")
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "https://fallback.test/api/interpreter")
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", "region=https://pbf.test/GEHEIM.pbf?lat=47.12345")
    monkeypatch.setattr(settings, "HYDRANT_USER_AGENT", "Einsatzcockpit-Test ehrlicher Kontakt")
    bestand = [{"quelle": "region", "aktiv": {"id": 1, "status": "aktiv", "anzahl": 42,
                "datenstand": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "alter_tage": 1},
                "pruefung": [], "letzter_fehler": None}]
    monkeypatch.setattr(osm_gebaeude_import, "status", lambda: deepcopy(bestand))
    daten = {"errors_by_code": {}, "ok": 4, "retry_count": 0, "hosts": [],
             "last_success": None, "last_failure": None, "cache_entries": None, "cache_hit_rate": None}
    monkeypatch.setattr(geodienst_statistik, "zusammenfassung", AsyncMock(side_effect=lambda *a: deepcopy(daten)))
    monkeypatch.setattr(hydrant_service, "_lesen", AsyncMock(return_value={}))
    anfragen = []
    zustand = {"status": 200, "fehler": None}
    original = httpx.AsyncClient
    zustand["client_klasse"] = original

    async def antwort(request):
        anfragen.append(request)
        assert request.headers["User-Agent"] == settings.HYDRANT_USER_AGENT
        if zustand["fehler"]:
            raise zustand["fehler"]
        return httpx.Response(zustand["status"], text="GEHEIM lat=47.12345 /private?query=adresse",
                              headers={"last-modified": "Wed, 28 Oct 2026 12:00:00 GMT"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(antwort), **kw))
    return bestand, daten, anfragen, zustand


@pytest.mark.parametrize("live", [False, True])
def test_collect_und_maskierung(umgebung, live):
    _, _, anfragen, _ = umgebung
    ergebnis = registry.collect(None, "admin_osm_pruefen", live=live)
    assert ergebnis["status"] == "ok"
    evidenz = ergebnis["evidence"]
    assert evidenz["overall_status"] == "ok" and evidenz["recommendations"] == []
    assert evidenz["lokaler_bestand"][0]["aktiv"]["anzahl"] == 42
    assert ("endpoints" in evidenz) is live
    assert len(anfragen) == (3 if live else 0)
    if live:
        assert [r.method for r in anfragen] == ["GET", "GET", "HEAD"]
        assert [r.url.path for r in anfragen[:2]] == ["/api/status", "/api/status"]
        assert all(not r.url.query for r in anfragen[:2])
        assert evidenz["quellen_proben"][0]["last_modified"] == "2026-10-28T12:00:00Z"
        assert all(p["fehlerschutz"]["status"] == "geschlossen" for p in evidenz["endpoints"])
    antwort = server.response("admin_osm_pruefen", "korrelation", ergebnis)
    assert antwort["korrelations_id"] == "korrelation"
    assert "correlation_id" not in evidenz and "korrelations_id" not in evidenz
    text = json.dumps(antwort)
    assert len(text.encode()) < server.MAX_RESPONSE_BYTES
    for wert in ("47.12345", "/api/", "GEHEIM", "?", "/private", "query="):
        assert wert not in text


@pytest.mark.parametrize("fehler,status,code", [
    (None, 502, "OVERPASS_HTTP_5XX"),
    (httpx.ReadTimeout("GEHEIM 47.12345"), 200, "OVERPASS_TIMEOUT"),
    (httpx.ConnectError("Name or service not known GEHEIM"), 200, "OVERPASS_DNS_ERROR"),
])
def test_live_fehler_ohne_bestand(umgebung, fehler, status, code):
    bestand, _, _, zustand = umgebung
    bestand[0]["aktiv"] = None
    zustand.update(fehler=fehler, status=status)
    ergebnis = registry.collect(None, "admin_osm_pruefen")
    assert ergebnis["status"] == "fail"
    assert all(p["code"] == code for p in ergebnis["evidence"]["endpoints"])
    assert ergebnis["evidence"]["endpoints"][0]["http_code"] == (502 if status == 502 else None)
    texte = " ".join(ergebnis["evidence"]["recommendations"])
    assert "Erstimport" in texte and "Rueckfall" in texte
    assert "GEHEIM" not in json.dumps(ergebnis) and "47.12345" not in json.dumps(ergebnis)


@pytest.mark.parametrize("fall", ["alt", "unbekannt", "pruefung", "importfehler", "overpassfehler", "fehlt"])
def test_warnungen_und_empfehlungen(umgebung, fall):
    bestand, daten, _, _ = umgebung
    if fall == "alt":
        bestand[0]["aktiv"]["alter_tage"] = 99
    elif fall == "unbekannt":
        bestand[0]["aktiv"]["alter_tage"] = None
    elif fall == "pruefung":
        bestand[0]["pruefung"] = [{"id": 2, "status": "pruefung"}]
    elif fall == "importfehler":
        bestand[0]["letzter_fehler"] = {"id": 2, "fehler_code": "OSM_DOWNLOAD_HTTP", "fehler_text": "GEHEIM"}
    elif fall == "overpassfehler":
        daten["last_failure"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    else:
        bestand[0]["aktiv"] = None
    ergebnis = registry.collect(None, "admin_osm_pruefen", live=False)
    assert ergebnis["status"] == "warn"
    assert ergebnis["evidence"]["recommendations"]
    assert "GEHEIM" not in json.dumps(ergebnis)


def test_uebersicht_ohne_live_http(umgebung):
    with SessionLocal() as db:
        ergebnis = registry.collect(db, "admin_system_uebersicht")
    assert ergebnis["evidence"]["teilchecks"]["admin_osm_pruefen"] == "ok"
    assert umgebung[2] == []
    assert "admin_osm_pruefen" not in registry.READ_TOOLS
    assert registry.ADMIN_TOOLS["admin_osm_pruefen"].module == "osm"


def test_tool_liste_und_wrapper(client, admin_env, umgebung):
    token = tokens(client, admin_env)["access_token"]
    liste = oauth.admin_call(client, token, "tools/list")
    assert "admin_osm_pruefen" in liste.text
    ergebnis = call(client, token, "admin_osm_pruefen", {"live": False})
    assert ergebnis["modul"] == "osm" and ergebnis["status"] == "ok"
    assert umgebung[2] == []


def test_proben_parallel_und_breaker_unveraendert(umgebung, monkeypatch):
    gleichzeitig = 0
    maximum = 0

    async def request(self, methode, url):
        nonlocal gleichzeitig, maximum
        gleichzeitig += 1
        maximum = max(maximum, gleichzeitig)
        await asyncio.sleep(0.01)
        gleichzeitig -= 1
        return httpx.Response(200, request=httpx.Request(methode, url))

    monkeypatch.setattr(umgebung[3]["client_klasse"], "request", request)
    schreiben = AsyncMock()
    monkeypatch.setattr(hydrant_service, "_setzen", schreiben)
    lesen = AsyncMock(return_value={"offen_bis": datetime.now(UTC).timestamp() + 60})
    monkeypatch.setattr(hydrant_service, "_lesen", lesen)
    ergebnis = registry.collect(None, "admin_osm_pruefen", live=True)
    assert maximum == 3
    assert ergebnis["evidence"]["circuit_breaker_status"]["hydranten"]["status"] == "offen"
    assert all(p["fehlerschutz"]["status"] == "offen" for p in ergebnis["evidence"]["endpoints"])
    schreiben.assert_not_awaited()


def test_langer_quellenname_bleibt_beim_statusvergleich_erhalten(umgebung, monkeypatch):
    name = "region_mit_einem_langen_quellennamen"
    umgebung[0][0]["quelle"] = name
    monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", name + "=https://pbf.test/GEHEIM.pbf")
    ergebnis = registry.collect(None, "admin_osm_pruefen", live=False)
    assert ergebnis["status"] == "ok"
    assert ergebnis["evidence"]["lokaler_bestand"][0]["host"] == "pbf.test"


@pytest.mark.parametrize("fehlt,erfolg,fehler,status", [
    (True, None, "2026-10-29T10:00:00Z", "fail"),
    (True, "2026-10-29T11:00:00Z", "2026-10-29T10:00:00Z", "warn"),
    (False, "2026-10-29T11:00:00Z", "2026-10-27T10:00:00Z", "ok"),
])
def test_status_ohne_live_und_24_stunden_fehler(umgebung, monkeypatch, fehlt, erfolg, fehler, status):
    from types import SimpleNamespace

    from app.admin_mcp import osm_tools

    monkeypatch.setattr(osm_tools, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 29, 12, tzinfo=UTC)))
    bestand, daten, anfragen, _ = umgebung
    if fehlt:
        bestand[0]["aktiv"] = None
    daten.update(last_success=erfolg, last_failure=fehler)
    assert registry.collect(None, "admin_osm_pruefen", live=False)["status"] == status
    assert anfragen == []
