"""P7a: echte BSP-Browserablaeufe mit isolierter SQLite-DB und synthetischem PNG.

Aufruf: PLAYWRIGHT_CHROMIUM=/pfad/zu/chromium .venv/bin/python -m pytest
    e2e/test_bsp_overlay_browser.py -v -rs
Kein externer E2E-Stack, keine Produktivdaten, keine Browserinstallation.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BILD = ".leaflet-bsp-pane img.leaflet-image-layer"
VIEWPORTS = [{"width": 1024, "height": 768}, {"width": 390, "height": 844}]


def _serve(directory: Path, port: int) -> None:
    """Nur im Kindprozess: App-Imports erst nach isolierter Konfiguration."""
    os.environ.update(
        DATABASE_URL=f"sqlite:///{directory / 'bsp.db'}",
        SECRET_KEY="bsp-browser-test-secret-key-mindestens-32-zeichen",
        DEBUG="true", TESTING="true", COOKIE_SECURE="false", REDIS_URL="",
        OBJEKT_MEDIA_DIR=str(directory), MEDIA_STORAGE_DIR=str(directory / "media"),
        NACHSCHLAGEWERK_SYNC_ENABLED="false", MAIL_IMAP_POLL_ENABLED="false",
        MAIL_GRAPH_POLL_ENABLED="false",
    )
    import math

    import uvicorn
    from PIL import Image
    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    from app.core.security import hash_password
    from app.core.tenant import set_tenant_context
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models.incident import Incident
    from app.models.master import AlarmType, FireDept, OrgSettings, SystemSettings
    from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite, ObjektEinsatz
    from app.models.user import Role, User, UserRole
    from app.seed_data import _upsert_roles
    from app.services import objekt_georef_service as geo

    @compiles(BigInteger, "sqlite")
    def bigint_sqlite(element, compiler, **kw):
        return "INTEGER"

    Base.metadata.create_all(engine)
    image = Image.new("RGB", (240, 120), "white")
    image.paste("red", (20, 20, 110, 100))
    image.paste("blue", (130, 20, 220, 100))
    image.save(directory / "plan.png")
    (directory / "plan.pdf").write_bytes(b"synthetische BSP-Testquelle")
    # Ausschliesslich synthetische Geometrie: 100 x 50 m um den Testmittelpunkt.
    faktor = geo.ERDRADIUS_M * math.pi / 180
    punkte = [
        {"id": i, "x": x, "y": y, "lat": 47 + (0.5 - y) * 50 / faktor,
         "lng": 9 + (x - 0.5) * 100 / (faktor * math.cos(math.radians(47))),
         "quelle": "manuell", "beschreibung": f"Testpunkt {i}"}
        for i, (x, y) in enumerate(((0.1, 0.1), (0.9, 0.1), (0.9, 0.9)))
    ]
    with SessionLocal() as db:
        set_tenant_context(db, None)
        _upsert_roles(db)
        org = FireDept(slug="bsp-browser", name="BSP Browser Test")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, objekt_module_enabled=True, lagefuehrung_modul_aktiv=True))
        for key in ("objekt_module_enabled", "lagefuehrung_modul_aktiv"):
            db.add(SystemSettings(key=key, value="true"))
        db.add(AlarmType(org_id=org.id, code="T1", category="T", label="Browser-Test"))
        users = {}
        for name, role in (("verwalter", "objekt_verwalter"), ("lage", "incident_leader")):
            user = User(username=name, display_name=name, org_id=org.id,
                        password_hash=hash_password("BspBrowser123!"))
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
            users[name] = user.id
        objekt = Objekt(org_id=org.id, nummer=1, name="BSP Testhalle", status="freigegeben", lat=47, lng=9)
        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active", lat=47.0004, lng=9)
        db.add_all([objekt, incident])
        db.flush()
        dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id, pfad="plan.pdf",
                                  dateiname_original="Synthetischer Plan.pdf", status="fertig", seitenzahl=1)
        db.add(dokument)
        db.flush()
        seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id, dokument_id=dokument.id,
                                   seiten_nr=1, bild_pfad="plan.png")
        db.add(seite)
        db.add(ObjektEinsatz(org_id=org.id, incident_id=incident.id, objekt_id=objekt.id,
                            quelle="manuell", status="bestaetigt"))
        db.flush()
        result = geo.referenzpunkte(db, objekt, dokument.id, 1, "setzen", punkte,
                                   "aehnlichkeit", users["verwalter"])
        assert result["georeferenz"]["status"] == "gueltig", result
        db.commit()
        daten = {"objekt_id": objekt.id, "incident_id": incident.id, "seite_id": seite.id,
                 "dokument_id": dokument.id, "punkte": punkte}
    (directory / "daten.json").write_text(json.dumps(daten), encoding="utf-8")
    # Keine Scheduler/externen Poller in diesem lokalen UI-Testserver.
    uvicorn.run(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")


@pytest.fixture(scope="module")
def bsp_browser():
    executable = Path(os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium"))
    if not executable.is_file():
        pytest.skip(f"Chromium fehlt: {executable}; PLAYWRIGHT_CHROMIUM auf vorhandene Binary setzen")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        except Error as exc:
            pytest.skip(f"Chromium kann lokal nicht starten: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def bsp_server(bsp_browser, tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    log = tmp_path / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-c", "import runpy; runpy.run_path(" + repr(str(Path(__file__).resolve()))
             + ", run_name='__main__')", str(tmp_path), str(port)],
            cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 45
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(log.read_text())
                    try:
                        if client.get(base + "/login").status_code == 200:
                            break
                    except httpx.TransportError:
                        time.sleep(0.1)
                else:
                    pytest.fail("BSP-Testserver startet nicht:\n" + log.read_text())
            yield json.loads((tmp_path / "daten.json").read_text()) | {"base": base, "directory": tmp_path}
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def bsp_sessions(bsp_browser, bsp_server):
    contexts = []
    errors = []

    def session(username="lage", viewport=None):
        context = bsp_browser.new_context(viewport=viewport or {"width": 1440, "height": 1000},
                                          service_workers="block")
        contexts.append(context)
        # Nur externe Grundkarten ersetzen; alle App-/BSP-Requests bleiben echt.
        def route_external(route):
            if urlsplit(route.request.url).path.endswith("/karte/gebaeude.json"):
                # OSM-Gebaeudeecken sind ein externer Dienst, in der Testumgebung nicht erreichbar.
                route.fulfill(content_type="application/json", body='{"gebaeude": []}')
                return
            host = urlsplit(route.request.url).hostname or ""
            tile_host = host == "tile.openstreetmap.org" or host.endswith((".tile.openstreetmap.org", ".basemap.at"))
            if route.request.resource_type == "image" and tile_host:
                route.fulfill(content_type="image/png", body=(bsp_server["directory"] / "plan.png").read_bytes())
            else:
                route.continue_()
        context.route("**/*", route_external)
        def capture(page):
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text + " " + str(message.location)) if message.type == "error" else None)
        page = context.new_page()
        page.goto(bsp_server["base"] + "/login")
        page.locator("#username").fill(username)
        page.locator("#password").fill("BspBrowser123!")
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url(lambda url: urlsplit(url).path != "/login")
        # Geprueft werden die BSP-Ansichten ab erfolgreichem Login, inkl. Druck-Popups.
        # Separater Bestandsbefund: login.html:12 laedt native-bridge vor native-ptr.
        capture(page)
        context.on("page", capture)
        return page

    yield session
    for context in contexts:
        context.close()
    assert not errors, "Browser-JS-/Console-Fehler: " + "\n".join(errors)


def _freigeben(session, daten):
    page = session("verwalter")
    page.goto(f'{daten["base"]}/objekte/{daten["objekt_id"]}/dokumente/seite/{daten["seite_id"]}/georef')
    root = page.locator("#bsp-overlay")
    root.get_by_role("button", name="Ganze Seite", exact=True).click()
    root.locator('[name="name"]').fill("E2E Hallenplan")
    root.locator('[name="standard_sichtbar"]').check()
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/bsp-overlays")) as created:
        root.get_by_role("button", name="Entwurf speichern", exact=True).click()
    assert created.value.status == 200, created.value.text()
    overlay = created.value.json()
    assert overlay["ausschnitt"] == {"art": "seite", "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    root.get_by_role("button", name="Freigeben", exact=True).click()
    dialog = root.locator("dialog")
    expect(dialog).to_contain_text("3 Punkte")
    checks = dialog.locator('input[type="checkbox"]')
    expect(checks).to_have_count(2)
    confirm = dialog.get_by_role("button", name="Verbindlich freigeben")
    expect(confirm).to_be_disabled()
    checks.nth(0).check()
    expect(confirm).to_be_disabled()
    checks.nth(1).check()
    with page.expect_response(lambda r: r.url.endswith("/freigeben")) as released:
        confirm.click()
    assert released.value.status == 200, released.value.text()
    expect(root.get_by_role("button", name="E2E Hallenplan · freigegeben", exact=True)).to_be_visible()
    assert released.value.json()["freigabe_nr"] == 1
    return page, overlay["id"]


@pytest.fixture
def freigabe(bsp_sessions, bsp_server):
    return _freigeben(bsp_sessions, bsp_server)


def _karte(page, daten):
    page.goto(f'{daten["base"]}/einsatz/{daten["incident_id"]}/lagefuehrung')
    expect(page.locator("#lft-karte")).to_be_visible()
    expect(page.locator("#lft-bsp")).to_be_visible()


def _bild(page):
    image = page.locator(BILD)
    expect(image).to_have_count(1)
    expect(image).to_be_visible()
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("img => img.naturalWidth > 0")
    return image


def _sichtbar(page, wert):
    checkbox = page.locator("#lft-layer-bsp")
    expect(checkbox).to_be_checked(checked=not wert)
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/einstellung")) as response:
        # Der Serverzustand wird erst nach POST und anschliessendem GET gerendert.
        checkbox.click()
    assert response.value.status == 200, response.value.text()
    expect(checkbox).to_be_enabled()
    expect(checkbox).to_be_checked(checked=wert)


def test_verwalter_ganze_seite_freigabe_mit_warnungen(bsp_sessions, bsp_server):
    _freigeben(bsp_sessions, bsp_server)


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["tablet", "handy"])
def test_einsatzkarte_bsp_pane_und_deckkraft(bsp_sessions, bsp_server, freigabe, viewport):
    page = bsp_sessions(viewport=viewport)
    _karte(page, bsp_server)
    image = _bild(page)
    expect(image).to_have_css("opacity", "0.6")
    slider = page.locator("#lft-bsp-deckkraft")
    slider.focus()
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/einstellung")) as response:
        slider.press("ArrowRight")
    assert response.value.status == 200
    expect(image).to_have_css("opacity", "0.65")
    expect(page.locator("#lft-bsp-prozent")).to_have_text("65 %")


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["tablet", "handy"])
def test_opt_out_alle_persistent_und_nur_fuer_mich_isoliert(bsp_sessions, bsp_server, freigabe, viewport):
    page = bsp_sessions(viewport=viewport)
    other = bsp_sessions(viewport=viewport)
    _karte(page, bsp_server)
    _karte(other, bsp_server)
    _bild(page)
    _bild(other)
    page.locator("#lft-bsp-scope").select_option("alle")
    _sichtbar(page, False)
    expect(page.locator(BILD)).to_have_count(0)
    expect(other.locator(BILD)).to_have_count(0)
    page.reload()
    expect(page.locator("#lft-layer-bsp")).not_to_be_checked()
    expect(page.locator(BILD)).to_have_count(0)
    other.reload()
    expect(other.locator("#lft-layer-bsp")).not_to_be_checked()
    _sichtbar(page, True)
    _bild(page)
    _bild(other)
    page.locator("#lft-bsp-scope").select_option("lokal")
    page.locator("#lft-layer-bsp").uncheck()
    expect(page.locator(BILD)).to_have_count(0)
    expect(page.locator("#lft-bsp-lokal")).to_contain_text("Persönliche Überschreibung aktiv")
    _bild(other)
    page.reload()
    expect(page.locator("#lft-bsp-lokal")).to_contain_text("Persönliche Überschreibung aktiv")
    expect(page.locator(BILD)).to_have_count(0)
    other.reload()
    _bild(other)


def test_marker_popup_zeichenwerkzeug_und_pane_reihenfolge(bsp_sessions, bsp_server, freigabe):
    page = bsp_sessions()
    _karte(page, bsp_server)
    _bild(page)
    assert page.locator(".leaflet-bsp-pane").evaluate(
        "pane => +getComputedStyle(pane).zIndex < "
        "+getComputedStyle(document.querySelector('.leaflet-marker-pane')).zIndex"
    )
    expect(page.locator(".leaflet-bsp-pane")).to_have_css("pointer-events", "none")
    page.locator(".lft-objekt-icon").click()
    expect(page.locator(".leaflet-popup-content")).to_contain_text("BSP Testhalle")
    page.locator(".leaflet-popup-close-button").click()
    tool = page.locator(".leaflet-pm-icon-polygon")
    tool.click()
    expect(tool.locator("../..")).to_have_class(re.compile(r"\bactive\b"))
    tool.click()
    _bild(page)


def test_druckdialog_unabhaengig_vom_opt_out_mit_freigabenachweis(bsp_sessions, bsp_server, freigabe):
    page = bsp_sessions()
    _karte(page, bsp_server)
    _bild(page)
    page.locator("#lft-tool-druck").click()
    page.locator("#lft-druck-bsp-modus").select_option("MAP_WITH_BSP")
    page.locator("#lft-druck-bsp-ids").select_option(str(freigabe[1]))
    expect(page.locator("#lft-druck-bsp-status")).to_have_text("BSP-Overlay wird gedruckt: ja")
    page.locator("#lft-druck-abbrechen").click()
    _sichtbar(page, False)
    expect(page.locator(BILD)).to_have_count(0)
    page.locator("#lft-tool-druck").click()
    expect(page.locator("#lft-druck-bsp-modus")).to_have_value("MAP_WITH_BSP")
    expect(page.locator("#lft-druck-bsp-status")).to_have_text("BSP-Overlay wird gedruckt: ja")
    with page.expect_popup() as popup:
        page.locator("#lft-druck-vorschau").click()
    printed = popup.value
    printed.wait_for_load_state()
    expect(printed.locator("[data-bsp-nachweis]")).to_contain_text("Freigabe-Nr. 1 vom")
    expect(printed.locator("[data-bsp-nachweis]")).to_contain_text("BSP Testhalle")
    expect(printed.locator("[data-bsp-nachweis]")).to_contain_text("Seite 1, Version")
    _bild(printed)


def test_neue_georef_revision_entfernt_overlay_ohne_js_fehler(bsp_sessions, bsp_server, freigabe):
    page = bsp_sessions()
    _karte(page, bsp_server)
    _bild(page)
    editor = freigabe[0]
    csrf = editor.locator("#georef-editor").get_attribute("data-csrf")
    response = editor.request.post(
        f'{bsp_server["base"]}/objekte/{bsp_server["objekt_id"]}/karte/georef',
        headers={"X-CSRF-Token": csrf},
        data={"dokument_id": bsp_server["dokument_id"], "seite": 1, "punkte": bsp_server["punkte"],
              "modell": "aehnlichkeit", "expected_revision": 1, "expected_rotation": 0},
    )
    assert response.status == 200, response.text()
    assert response.json()["georeferenz"]["revision"] == 2
    # Die offene Karte aktualisiert die Liste spaetestens im 30-Sekunden-Intervall.
    # Kein Reload: auch das Entfernen eines bereits gerenderten Layers muss funktionieren.
    expect(page.locator("#lft-bsp")).to_be_hidden(timeout=35_000)
    expect(page.locator(BILD)).to_have_count(0)
    page.locator(".lft-objekt-icon").click()
    expect(page.locator(".leaflet-popup-content")).to_contain_text("BSP Testhalle")
    expect(page.locator(".leaflet-control-zoom-in")).to_be_visible()
    # pageerror und console.error werden fuer alle Sessions bis zum Teardown geprueft.


@pytest.mark.parametrize("viewport", VIEWPORTS, ids=["tablet", "handy"])
@pytest.mark.parametrize("ansicht", ["tab", "editor"])
def test_objektkarte_bsp_lokal_und_marker_editierbar(bsp_sessions, bsp_server, freigabe, viewport, ansicht):
    # Marker nur fuer diesen Ablauf anlegen: kein Ueberdecken des Objektmarkers in Einsatztests.
    editor = freigabe[0]
    csrf = editor.locator("#georef-editor").get_attribute("data-csrf")
    response = editor.request.post(
        f'{bsp_server["base"]}/objekte/{bsp_server["objekt_id"]}/karte/objekte',
        headers={"X-CSRF-Token": csrf},
        data={"typ": "bmz", "lat": 47, "lng": 9, "label": "BSP Testmarker"},
    )
    assert response.status == 200, response.text()
    page = bsp_sessions("verwalter", viewport=viewport)
    url = f'{bsp_server["base"]}/objekte/{bsp_server["objekt_id"]}'
    page.goto(url + ("/karte" if ansicht == "editor" else ""))
    if ansicht == "tab":
        page.get_by_role("button", name="Lagekarte", exact=True).click()
    canvas = page.locator("#objekt-karte" if ansicht == "editor" else "#objekt-karte-tab")
    expect(canvas).to_be_visible()
    image = _bild(page)
    expect(page.locator(".leaflet-bsp-pane")).to_have_css("z-index", "250")
    expect(page.locator(".leaflet-bsp-pane")).to_have_css("pointer-events", "none")
    panel = page.locator("#lft-bsp")
    expect(panel).to_be_visible()
    assert panel.bounding_box()["y"] >= canvas.bounding_box()["y"] + canvas.bounding_box()["height"]
    panel.locator("summary").click()
    checkbox = panel.locator("#lft-layer-bsp")
    checkbox.uncheck()
    expect(page.locator(BILD)).to_have_count(0)
    checkbox.check()
    image = _bild(page)
    expect(image).to_have_css("opacity", "0.6")
    assert panel.evaluate("el => el.getBoundingClientRect().right <= innerWidth")
    marker = canvas.locator(".oks-divicon").filter(has_text="BSP Testmarker")
    marker.click()
    expect(canvas.locator(".leaflet-popup-content")).to_contain_text("BSP Testmarker")
    expect(canvas.get_by_role("button", name="Entfernen", exact=True)).to_be_visible()
    canvas.locator(".leaflet-popup-close-button").press("Enter")
    tool = canvas.locator(".leaflet-pm-icon-polygon")
    tool.click()
    expect(tool.locator("../..")).to_have_class(re.compile(r"\bactive\b"))
    tool.click()
    if ansicht == "tab":
        page.get_by_role("button", name="Übersicht", exact=True).click()
        page.get_by_role("button", name="Lagekarte", exact=True).click()
        expect(canvas.locator(".leaflet-bsp-pane")).to_have_count(1)
        expect(canvas.locator(".leaflet-pm-toolbar").first).to_be_visible()
        _bild(page)
    page.reload()
    if ansicht == "tab":
        page.get_by_role("button", name="Lagekarte", exact=True).click()
    _bild(page)


if __name__ == "__main__":
    _serve(Path(sys.argv[1]), int(sys.argv[2]))
