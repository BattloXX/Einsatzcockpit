"""Offline-Abnahme mit echtem Service Worker, IndexedDB, HTTP und WebSocket-Reconnect.

Ein lokaler HTTP-Proxy verwendet den bestehenden TestClient für echte ECP-Templates/APIs.
WebSockets liefern nur Ping/Pong; Reconnect muss die aktuellen Daten per API nachladen.
"""

import asyncio
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from app.models.major_incident import (
    EinheitAktion,
    EinheitSiteDispatch,
    LageEinheit,
    LageEinheitZugang,
    SiteLogEntry,
    SiteMedia,
)
from tests.test_einheit_ablauf_ui import _vorbereiten
from tests.test_einheit_api import _db, _png
from tests.test_einheit_uebersicht import browser as browser  # noqa: F401

DATEN = "Alpine.$data(document.querySelector('.einheit-page'))"


@pytest.fixture(autouse=True)
def medien_im_testverzeichnis(tmp_path, monkeypatch):
    from app.services import lage_media_service

    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "media"))


@pytest.fixture
def offline_browser(client, browser):
    state = {"posts": [], "ws": [], "ws_anzahl": 0, "loop": None, "antwort_verlieren": None}

    async def http(request: Request):
        state["loop"] = asyncio.get_running_loop()
        if request.url.path.startswith("/karten/osm-tile/"):
            return Response('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"/>',
                            media_type="image/svg+xml")
        body = await request.body()
        headers = {k: v for k, v in request.headers.items()
                   if k not in {"host", "content-length", "accept-encoding", "connection"}}
        headers["cookie"] = request.headers.get("cookie", "")
        path = request.url.path + ("?" + request.url.query if request.url.query else "")
        response = client.request(request.method, path, headers=headers, content=body)
        if request.method == "POST" and request.url.path.startswith("/einheit/api/"):
            state["posts"].append((request.url.path, response.status_code))
            if state["antwort_verlieren"] == request.url.path and response.status_code == 200:
                state["antwort_verlieren"] = None
                return Response('{"code":"antwort_verloren"}', status_code=503, media_type="application/json")
        result = Response(response.content, status_code=response.status_code)
        result.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers.multi_items()
                              if k.lower() not in {"content-length", "content-encoding"}]
        return result

    async def ws(websocket):
        await websocket.accept()
        state["ws"].append(websocket)
        state["ws_anzahl"] += 1
        try:
            while True:
                if await websocket.receive_text() == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            state["ws"].remove(websocket)

    proxy = Starlette(routes=[WebSocketRoute("/ws/{path:path}", ws),
                              Route("/{path:path}", http, methods=["GET", "HEAD", "POST"])])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(proxy, log_level="error", lifespan="off", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(.01)
    assert server.started
    context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="allow")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def starten(path="/einheit"):
        context.add_cookies([{"name": k, "value": v, "url": origin} for k, v in client.cookies.items()])
        page.goto(origin + path)
        page.wait_for_function("Boolean(navigator.serviceWorker.controller)")
        page.reload()  # Auch HTML und alle versionierten Module tatsächlich unter dem SW laden und speichern.
        page.wait_for_function(f"{DATEN}.geladen && {DATEN}.outboxVerfuegbar")
        page.wait_for_function("async () => Boolean(await caches.match(location.href))")
        return page

    def verbindungen_schliessen():
        async def schliessen():
            for verbindung in list(state["ws"]):
                await verbindung.close()
        asyncio.run_coroutine_threadsafe(schliessen(), state["loop"]).result(timeout=5)

    yield context, page, state, errors, starten, verbindungen_schliessen
    context.close()
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def _auftrag(page, dispatch):
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{dispatch}"] button').click()
    page.wait_for_url(f"**/einheit/auftrag/{dispatch}")


def _gesendet(page, anzahl=0):
    page.wait_for_function(f"{DATEN}.outboxListe.length === {anzahl}", timeout=20000)


def test_echter_offline_neustart_mehrfachuebernahme_meldung_foto_und_auto_sync(client, setup_db, offline_browser):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    context, page, state, errors, starten, _ = offline_browser
    starten()
    expect(page.locator('.einheit-meta')).to_contain_text("Keine offenen Übermittlungen")
    context.set_offline(True)
    page.reload()
    expect(page.locator('[data-einheit-auftrag]')).to_have_count(3)
    for dispatch in ids["offen"][:2]:
        _auftrag(page, dispatch)
        taste = page.get_by_role("button", name="Auftrag übernehmen", exact=True)
        taste.evaluate("el => { el.click(); el.click(); }")
        expect(page.locator('[data-einheit-hauptaktion]')).to_have_text("Übermittlung ausstehend")
    _auftrag(page, ids["d1"])
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Arbeit läuft", exact=True).click()
    dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
        "name": "offline.png", "mimeType": "image/png", "buffer": _png(),
    })
    dialog.get_by_role("button", name="Erfassen", exact=True).evaluate("el => { el.click(); el.click(); }")
    expect(dialog).not_to_be_visible()
    _gesendet(page, 4)
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.locator('[data-einheit-verlauf="zentral"]')).to_contain_text("nur lokal gespeichert")
    expect(page.locator('.einheit-meta')).not_to_contain_text("Alles übermittelt")
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).count() == 0
    page.reload()  # Persistente Outbox einschließlich Fotobinärdaten, nicht nur alter JavaScript-Zustand.
    _gesendet(page, 4)
    context.clear_cookies(name="ec_csrf")  # Wiederverbindung muss zuerst auch den Schreibschutz-Cookie erneuern.
    context.set_offline(False)
    _gesendet(page)
    expect(page.locator('.einheit-meta')).to_contain_text("Alles übermittelt")
    with _db() as db:
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in ids["offen"]] == [
            "bestaetigt", "bestaetigt", "zugewiesen",
        ]
        assert db.get(LageEinheit, ids["e1"]).incident_site_id is None
        assert db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).count() == 4
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"]).count() == 1
        assert db.query(SiteLogEntry).filter_by(einheit_id=ids["e1"], kind="lagemeldung").count() == 1
    assert len(state["posts"]) == 4 and errors == []


@pytest.mark.parametrize("geaendert", [False, True])
def test_erkundungsabschluss_offline_nach_neustart_mit_ergebnis_und_foto(
    client, setup_db, offline_browser, geaendert,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung", "vor_ort")
    context, page, _, errors, starten, _ = offline_browser
    starten()
    _auftrag(page, ids["d1"])
    context.set_offline(True)
    page.get_by_role("button", name="Erkundungsergebnis erfassen", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("akute_gefahr")
    dialog.get_by_label("Lage vor Ort", exact=True).fill("Gasflaschen im Keller")
    dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
        "name": "erkundung.png", "mimeType": "image/png", "buffer": _png(),
    })
    dialog.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.wait_for_function("location.pathname === '/einheit'")
    expect(page.locator(".einheit-auftrag")).not_to_be_visible()
    _gesendet(page, 3)
    page.reload()
    expect(page.locator('[data-einheit-auftrag]')).to_have_count(3)
    _auftrag(page, ids["d1"])
    expect(page.locator('[data-einheit-hauptaktion]')).to_have_text("Übermittlung ausstehend")
    if geaendert:
        with _db() as db:
            db.get(EinheitSiteDispatch, ids["d1"]).version += 1
            db.commit()
    context.set_offline(False)
    _gesendet(page, 1 if geaendert else 0)
    if geaendert:
        page.wait_for_function(f"{DATEN}.outboxListe[0]?.status === 'konflikt'")
    else:
        expect(page.locator('[data-einheit-hauptaktion]')).to_have_text("Erkundet")
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == ("vor_ort" if geaendert else "abgeschlossen")
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"]).count() == 1
        assert db.query(SiteLogEntry).filter_by(einheit_id=ids["e1"], kind="lagemeldung").one().text.startswith(
            "Akute Gefahr festgestellt")
    assert errors == []


@pytest.mark.parametrize("aktion", ["status", "foto"])
def test_verlorene_serverantwort_wiederholt_ohne_doppelte_wirkung(client, setup_db, offline_browser, aktion):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    _, page, state, errors, starten, _ = offline_browser
    starten()
    _auftrag(page, ids["d1"])
    url = f"/einheit/api/auftrag/{ids['d1']}/{aktion}"
    state["antwort_verlieren"] = url
    if aktion == "status":
        page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    else:
        page.locator('[x-ref=fotoDirekt]').set_input_files({
            "name": "verlust.png", "mimeType": "image/png", "buffer": _png(),
        })
    page.wait_for_function(f"{DATEN}.outboxListe.some(e => e.status === 'fehler_netz')")
    expect(page.locator('.einheit-meta')).not_to_contain_text("Alles übermittelt")
    _gesendet(page)
    expect(page.locator('.einheit-meta')).to_contain_text("Alles übermittelt")
    assert [status for path, status in state["posts"] if path == url] == [200, 200]
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).count() == 1
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"]).count() == (1 if aktion == "foto" else 0)
    assert errors == []


@pytest.mark.parametrize("aenderung,code", [
    ("auftrag", "auftrag_geaendert"), ("rueckzug", "auftrag_zurueckgezogen"), ("boardstatus", "status_geaendert"),
])
def test_offline_bestaetigung_ueberschreibt_keinen_neuen_boardstand(
    client, setup_db, offline_browser, aenderung, code,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    context, page, _, errors, starten, _ = offline_browser
    starten()
    _auftrag(page, ids["d1"])
    context.set_offline(True)
    page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    _gesendet(page, 1)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        if aenderung == "auftrag":
            d.auftrag, d.version = "Neue Weisung der Einsatzleitung", 2
        elif aenderung == "rueckzug":
            d.withdrawn_at = datetime.now(UTC)
        else:
            d.einheit_status = "anfahrt"
            d.einheit.incident_site_id = d.site_id
        db.commit()
    context.set_offline(False)
    page.wait_for_function(f"{DATEN}.outboxListe[0]?.status === 'konflikt'", timeout=15000)
    assert page.evaluate(f"{DATEN}.outboxListe[0].letzter_fehler") == code
    expect(page.locator('.einheit-meta')).not_to_contain_text("Alles übermittelt")
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == (
            "anfahrt" if aenderung == "boardstatus" else "zugewiesen")
    assert errors == []


def test_websocket_reconnect_laesst_boardaenderung_nachladen(client, setup_db, offline_browser):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    _, page, state, errors, starten, schliessen = offline_browser
    starten()
    _auftrag(page, ids["d1"])
    expect(page.locator('[data-einheit-hauptaktion]')).to_have_text("Auftrag übernehmen")
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.auftrag, d.version = "Leitstelle: zuerst Nordtor prüfen", 2
        db.commit()
    vorher = state["ws_anzahl"]
    schliessen()
    expect(page.locator('.einheit-auftrag')).to_have_text("Leitstelle: zuerst Nordtor prüfen", timeout=15000)
    assert state["ws_anzahl"] > vorher
    assert not state["posts"] and errors == []


@pytest.mark.parametrize("zugang", ["gk", "qr"])
def test_abgelaufener_zugang_blockiert_offline_eintraege_vor_dem_versand(client, setup_db, offline_browser, zugang):
    from playwright.sync_api import expect

    if zugang == "gk":
        from tests.test_einheit_zugang import _zugang_mit_auftrag

        cookie, dispatch, einheit, zugang_id = _zugang_mit_auftrag()
        client.cookies.set("ec_gk", cookie)
    else:
        from tests.test_gk_qr_zugang import _eingeloest

        ids = _eingeloest(client)
        dispatch, einheit = ids["eigener"], ids["einheit"]
        with _db() as db:
            zugang_id = db.query(LageEinheitZugang).filter_by(einheit_id=einheit, typ="qr").one().id
    context, page, state, errors, starten, _ = offline_browser
    starten()
    _auftrag(page, dispatch)
    context.set_offline(True)
    page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    _gesendet(page, 1)
    with _db() as db:
        db.get(LageEinheitZugang, zugang_id).laeuft_ab_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    context.set_offline(False)
    page.wait_for_function(f"{DATEN}.zugangBeendet")
    expect(page.locator('.einheit-page')).to_contain_text("Die Anmeldung ist abgelaufen")
    expect(page.locator('.einheit-meta .einheit-offline')).to_have_text("Zugang beendet")
    page.wait_for_function(f"{DATEN}.outboxListe[0]?.status === 'blockiert_zugang'")
    expect(page.locator('.einheit-meta')).not_to_contain_text("Alles übermittelt")
    assert not state["posts"]  # Erst Kontext prüfen, dann senden; der abgelaufene Zugang sendet gar nicht.
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(einheit_id=einheit).count() == 0
    page.wait_for_function("""async () => {
      const keys = await (await caches.open('ec-board-v4')).keys();
      return !keys.some(r => /^\\/einheit(?:\\/auftrag\\/\\d+)?$/.test(new URL(r.url).pathname));
    }""")
    assert errors == []
