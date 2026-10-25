"""Gemeinsamer PDF-/Bildviewer mit echtem PDF.js und Touch-Ansichten."""

import io
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pypdf import PdfReader

from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_einheit_uebersicht import browser as browser
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401


@pytest.fixture
def viewer_seite(client, browser, dokumente):
    login(client, dokumente, "readonly")
    context = browser.new_context(viewport={"width": 1024, "height": 768}, service_workers="block",
                                  has_touch=True, device_scale_factor=2)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""
        window.WebSocket = class extends EventTarget {
          constructor() { super(); this.readyState = 1; } close() {} send() {}
        };
        window.printRequests = [];
        document.addEventListener('documentviewer:print', event => {
          window.printRequests.push(event.detail); event.preventDefault();
        });
    """)

    def handle(route):
        url = urlsplit(route.request.url)
        if url.netloc != "ecp.test":
            route.abort()
        elif url.path.startswith("/static/"):
            datei = Path("app") / url.path.lstrip("/")
            if datei.is_file():
                route.fulfill(path=str(datei))
            else:
                route.fulfill(status=404)
        else:
            response = client.get(url.path + ("?" + url.query if url.query else ""),
                                  headers={k: v for k, v in route.request.headers.items() if k in ("range",)})
            headers = {k: v for k, v in response.headers.items() if k not in ("content-length", "content-encoding")}
            route.fulfill(status=response.status_code, body=response.content, headers=headers)

    page.route("**/*", handle)
    page.goto(f'https://ecp.test/plaene/{dokumente["plan_id"]}')
    yield page, errors
    context.close()


def gezeichnet(page, nummer, anzahl=12):
    from playwright.sync_api import expect

    expect(page.locator('#documentViewer [data-dv-count]')).to_have_text(f"von {anzahl}")
    expect(page.locator('#documentViewer .dv-paper canvas')).to_have_attribute(
        "aria-label", f"Seite {nummer} von {anzahl}")
    page.wait_for_function(
        "n => document.querySelector('#documentViewer').dataset.renderedPage === String(n)", arg=nummer)


@pytest.mark.parametrize("breite,hoehe", [(1440, 900), (1024, 768), (390, 844)])
def test_vier_dokumente_seitenwahl_und_sitzung(viewer_seite, client, dokumente, breite, hoehe, tmp_path):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    page.set_viewport_size({"width": breite, "height": hoehe})
    artikel = page.locator('[data-dv-group]').filter(has_text="Vier Unterlagen")
    artikel.locator('[data-dv-document][data-dv-kind="pdf"]').first.click()
    dialog = page.locator('#documentViewer')
    gezeichnet(page, 1)
    # Bilder stehen in der Galerie zuerst.
    expect(dialog.locator('[data-dv-position]')).to_have_text("Dokument 2 von 4")
    assert page.evaluate("document.querySelectorAll('#documentViewer').length") == 1
    page.get_by_role("spinbutton", name="Sprung zu Seite").fill("5")
    page.get_by_role("spinbutton", name="Sprung zu Seite").press("Tab")
    gezeichnet(page, 5)
    page.get_by_role("button", name="Nächstes Dokument", exact=True).click()
    gezeichnet(page, 1)
    page.get_by_role("spinbutton", name="Sprung zu Seite").fill("7")
    page.get_by_role("spinbutton", name="Sprung zu Seite").press("Tab")
    gezeichnet(page, 7)
    page.get_by_role("button", name="Vorheriges Dokument", exact=True).click()
    gezeichnet(page, 5)
    page.get_by_role("button", name="Nächste Seite", exact=True).click()
    gezeichnet(page, 6)
    page.get_by_role("button", name="Vorherige Seite", exact=True).click()
    gezeichnet(page, 5)
    page.get_by_role("button", name="Dokumentenliste anzeigen").click()
    expect(page.locator('.dv-documents button')).to_have_count(4)
    page.locator('.dv-documents button').first.click()
    gezeichnet(page, 1, 1)
    expect(dialog.locator('#dv-name')).to_have_text('Unterlage 4')
    page.get_by_role("button", name="90 Grad drehen").click()
    expect(dialog).to_have_attribute("data-rotation", "90")
    page.locator('.dv-documents button').nth(2).click()
    gezeichnet(page, 7)
    page.get_by_role("button", name="Dokumentenliste anzeigen").click()
    page.get_by_role("button", name="Miniaturseiten anzeigen").click()
    page.get_by_role("button", name="Seite 9 anzeigen", exact=True).click()
    gezeichnet(page, 9)
    page.get_by_role("button", name="Miniaturseiten anzeigen").click()
    page.get_by_role("button", name="Vergrößern", exact=True).click()
    expect(dialog.locator('[data-dv-zoom]')).to_have_text('125 %')
    page.get_by_role("button", name="Seite einpassen").click()
    page.get_by_text("Seitenauswahl", exact=True).click()
    auswahl = page.locator('[data-dv-selection]')
    auswahl.fill('3–5, 9–10')
    expect(page.locator('[data-dv-selected]')).to_have_text('Ausgewählt: 3-5,9-10 (5 Seiten)')
    assert page.evaluate('documentViewerSelection().pages') == [3, 4, 5, 9, 10]
    url = urlsplit(page.locator('[data-dv-excerpt]').get_attribute('href'))
    response = client.get(url.path + '?' + url.query)
    assert len(PdfReader(io.BytesIO(response.content)).pages) == 5
    page.get_by_role("button", name="Ausgewählte Seiten drucken").click()
    assert page.evaluate('printRequests[0].pages') == [3, 4, 5, 9, 10]
    expect(page.get_by_role('button', name='Zum Einsatz hinzufügen')).to_be_disabled()
    auswahl.fill('99')
    expect(page.get_by_role("button", name="Ausgewählte Seiten drucken")).to_be_disabled()
    page.get_by_role("button", name="Aktuelle Seite", exact=True).click()
    expect(auswahl).to_have_value('9')
    page.get_by_role("button", name="Gesamtes Dokument", exact=True).click()
    expect(page.locator('[data-dv-selected]')).to_have_text('Ausgewählt: 1-12 (12 Seiten)')
    page.get_by_text("Seitenauswahl", exact=True).click()
    assert dialog.evaluate('(e) => e.scrollWidth <= innerWidth')
    page.screenshot(path=str(tmp_path / f'dokumentviewer-{breite}.png'))
    page.get_by_role("button", name="Dokumentenviewer schließen").click()
    expect(dialog).not_to_be_visible()
    artikel.locator('[data-dv-document][data-dv-kind="pdf"]').nth(1).click()
    gezeichnet(page, 9)
    assert not errors


def test_fehler_und_weiterblaettern(viewer_seite, dokumente):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    link = page.locator('[data-dv-document][data-dv-kind="pdf"]').first
    uuid = next(datei for datei in dokumente['dateien'] if datei['uuid'] in link.get_attribute('href'))
    uuid['path'].write_bytes(b'kaputtes PDF')
    link.click()
    expect(page.locator('[data-dv-status]')).to_contain_text('Dokument nicht verfügbar')
    page.get_by_role("button", name="Nächstes Dokument", exact=True).click()
    gezeichnet(page, 1)
    expect(page.locator('[data-dv-status]')).to_be_empty()
    assert not errors


def test_touch_vollbild_und_schnelle_dokumentwechsel(viewer_seite):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    page.locator('[data-dv-document][data-dv-kind="pdf"]').first.click()
    gezeichnet(page, 1)
    page.get_by_role('button', name='Vollbild umschalten').click()
    page.wait_for_function("document.fullscreenElement?.classList.contains('dv-shell')")
    page.get_by_role('button', name='Vollbild umschalten').click()
    page.wait_for_function('!document.fullscreenElement')
    stage = page.locator('.dv-stage').bounding_box()
    session = page.context.new_cdp_session(page)
    x, y = stage['x'] + stage['width'] * .7, stage['y'] + stage['height'] * .5
    session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x - 130, 'y': y}]})
    session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    gezeichnet(page, 2)
    page.evaluate("""() => {
      const next = document.querySelector('[data-dv-action="document-next"]');
      next.click(); next.click(); document.querySelector('[data-dv-action="document-prev"]').click();
    }""")
    gezeichnet(page, 1)
    expect(page.locator('[data-dv-position]')).to_have_text('Dokument 3 von 4')
    assert page.evaluate("document.querySelectorAll('#documentViewer').length") == 1
    assert not errors


def test_bisherige_mediaaufrufe_und_explizite_startseite(viewer_seite, dokumente):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    url = f'/plaene/anhang/{dokumente["dateien"][0]["uuid"]}'
    page.evaluate("url => openMediaViewer(url + '#page=8', 'pdf', 'Bisheriger PDF-Aufruf')", url)
    gezeichnet(page, 8)
    expect(page.locator('#dv-name')).to_have_text('Bisheriger PDF-Aufruf')
    page.get_by_role("button", name="Dokumentenviewer schließen").click()
    page.evaluate("url => openMediaViewer(url + '#page=3', 'pdf', 'Seitensprung')", url)
    gezeichnet(page, 3)
    image = f'/plaene/anhang/{dokumente["dateien"][3]["uuid"]}'
    page.evaluate("url => openImageLightbox([{src: url, edit: '/annotieren/incident/1'}])", image)
    gezeichnet(page, 1, 1)
    expect(page.locator('[data-dv-edit]')).to_have_attribute('href', 'https://ecp.test/annotieren/incident/1')
    assert page.evaluate("document.querySelectorAll('#documentViewer').length") == 1
    assert not errors
