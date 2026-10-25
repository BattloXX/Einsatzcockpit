"""Browserabnahme mit Android-/Windows-Profilen und Offline-/Entzugsszenarien."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_dokumentviewer_ui import gezeichnet
from tests.test_dokumentviewer_ui import viewer_seite as viewer_seite
from tests.test_einheit_offline_ui import offline_browser as offline_browser
from tests.test_einheit_uebersicht import browser as browser
from tests.test_einsatzwissen_phase6 import einsatzwissen as einsatzwissen
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401
from tests.test_katplan_k1b import umgebung as umgebung


@pytest.fixture(params=[(390, 844, True, 'Android'), (1024, 768, True, 'Android'), (1440, 900, False, 'Windows')])
def einsatzseite(request, client, browser, einsatzwissen):
    width, height, touch, platform = request.param
    login(client, einsatzwissen, "readonly")
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch,
                                  is_mobile=platform == 'Android', service_workers="block")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("window.WebSocket = class extends EventTarget {close() {} send() {}}")
    state = {"offline": False, "deny": False}

    def handle(route):
        url = urlsplit(route.request.url)
        if url.netloc != "ecp.test":
            route.abort()
        elif url.path.startswith("/static/"):
            path = Path("app") / url.path.lstrip("/")
            route.fulfill(path=str(path)) if path.is_file() else route.fulfill(status=404)
        elif url.path == "/einsatzwissen/offline-paket" and state["offline"]:
            route.abort("internetdisconnected")
        elif url.path == "/einsatzwissen/offline-paket" and state["deny"]:
            route.fulfill(status=403, body='{"detail":"Keine Berechtigung"}', content_type="application/json")
        else:
            response = client.request(route.request.method, url.path + ("?" + url.query if url.query else ""),
                                      content=route.request.post_data_buffer, follow_redirects=False,
                                      headers={k: v for k, v in route.request.headers.items()
                                               if k in ("content-type", "x-csrf-token")})
            route.fulfill(status=response.status_code, body=response.content,
                          headers={k: v for k, v in response.headers.items()
                                   if k not in ("content-length", "content-encoding")})

    page.route("**/*", handle)
    page.goto(f'https://ecp.test/einsatzwissen/{einsatzwissen["plan_id"]}')
    yield page, state, errors
    context.close()


def test_einsatzansicht_navigation_favorit_und_lokaler_stand(einsatzseite, einsatzwissen, tmp_path):
    from playwright.sync_api import expect

    page, state, errors = einsatzseite
    expect(page.locator('.ew-sprung a')).to_have_count(6)
    expect(page.locator('#ew-sofort')).to_contain_text('Sofort absperren')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert all(h >= 48 for h in page.locator('.ew-sprung a').evaluate_all(
        'links => links.map(a => a.getBoundingClientRect().height)'))
    page.screenshot(path=str(tmp_path / 'einsatzansicht.png'))
    page.get_by_role('button', name='Als Favorit merken').click()
    expect(page.get_by_role('button', name='Favorit entfernen')).to_be_visible()
    page.get_by_role('button', name='Kerninhalte lokal bereithalten').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('Kerninhalte bereit')
    assert page.evaluate("!localStorage.getItem('ec-einsatzwissen-v1').includes('Sofort absperren')")
    state['offline'] = True
    page.get_by_role('link', name='Lokale Kerninhalte öffnen').click()
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    expect(page.locator('[data-ew-offline-inhalt]')).not_to_contain_text('Vertraulicher Auftrag')
    expect(page.locator('[data-ew-offline-inhalt]')).not_to_contain_text('Stromerzeuger')
    expect(page.get_by_text('Lokale Offlinekopie', exact=False)).to_be_visible()
    page.get_by_role('button', name='Jetzt synchronisieren').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('Keine Verbindung')
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    state['offline'], state['deny'] = False, True
    page.get_by_role('button', name='Jetzt synchronisieren').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('entzogen')
    expect(page.locator('[data-ew-offline-inhalt]')).to_be_empty()
    assert not errors


def test_offline_ablauf_sperrt_sichtbaren_inhalt(einsatzseite):
    from playwright.sync_api import expect

    page, state, errors = einsatzseite
    page.get_by_role('button', name='Kerninhalte lokal bereithalten').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('Kerninhalte bereit')
    state['offline'] = True
    page.get_by_role('link', name='Lokale Kerninhalte öffnen').click()
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    page.clock.install(time=datetime.now(UTC))
    page.clock.set_fixed_time(datetime.now(UTC) + timedelta(minutes=16))
    expect(page.locator('[data-ew-offline-inhalt]')).to_be_empty(timeout=5000)
    assert page.evaluate("sessionStorage.getItem('ec-einsatzwissen-v1')") is None
    assert page.evaluate("localStorage.getItem('ec-einsatzwissen-v1')") is None
    assert not errors


def test_kontowechsel_verwirft_lokale_freigabe(einsatzseite, client, einsatzwissen):
    from playwright.sync_api import expect

    page, _, errors = einsatzseite
    page.get_by_role('button', name='Kerninhalte lokal bereithalten').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('Kerninhalte bereit')
    login(client, einsatzwissen, 'katplan_verwalter')
    page.goto('https://ecp.test/einsatzwissen')
    assert page.evaluate("sessionStorage.getItem('ec-einsatzwissen-v1')") is None
    assert page.evaluate("localStorage.getItem('ec-einsatzwissen-v1')") is None
    page.goto('https://ecp.test/static/einsatzwissen-offline.html')
    expect(page.locator('[data-ew-offline-inhalt]')).to_be_empty()
    assert not errors


def test_viewer_browser_zurueck_schliesst_dialog(viewer_seite):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    before = page.url
    artikel = page.locator('[data-dv-group]').filter(has_text='Vier Unterlagen')
    artikel.locator('[data-dv-document][data-dv-kind="pdf"]').first.click()
    gezeichnet(page, 1)
    page.go_back()
    expect(page.locator('#documentViewer')).not_to_be_visible()
    assert page.url == before
    assert not errors


@pytest.mark.parametrize('width,height', [(390, 844), (1024, 768), (1440, 900)])
def test_kompakte_dokumentfolge_seitensprung_und_druck(viewer_seite, dokumente, width, height):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(f'https://ecp.test/einsatzwissen/{dokumente["plan_id"]}')
    docs = page.locator('#ew-dokumente > [data-dv-group]')
    docs.locator('[data-dv-kind="pdf"]').first.click()
    gezeichnet(page, 1)
    expect(page.locator('[data-dv-position]')).to_have_text('Dokument 2 von 4')
    page.get_by_role('spinbutton', name='Sprung zu Seite').fill('5')
    page.get_by_role('spinbutton', name='Sprung zu Seite').press('Enter')
    gezeichnet(page, 5)
    page.get_by_role('button', name='Drucken', exact=True).click()
    page.wait_for_function('window.printRequests.length === 1')
    assert page.evaluate('window.printRequests[0].pages') == [5]
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.go_back()
    expect(page.locator('#documentViewer')).not_to_be_visible()
    assert not errors


def test_echter_serviceworker_offlinestart_reload_und_entzug(client, einsatzwissen, offline_browser):
    from playwright.sync_api import expect

    from app.core.tenant import set_tenant_context
    from app.models.katplan import KatplanPlan
    from tests.conftest import TestingSession

    login(client, einsatzwissen, 'readonly')
    context, page, state, errors, _, _ = offline_browser
    origin = state['origin']
    context.add_cookies([{'name': k, 'value': v, 'url': origin} for k, v in client.cookies.items()])
    path = f'/einsatzwissen/{einsatzwissen["plan_id"]}'
    page.goto(origin + path)
    page.wait_for_function('Boolean(navigator.serviceWorker.controller)')
    page.get_by_role('button', name='Kerninhalte lokal bereithalten').click()
    expect(page.locator('[data-ew-status]')).to_contain_text('Kerninhalte bereit')
    page.wait_for_function("async () => Boolean(await caches.match('/static/einsatzwissen-offline.html'))")
    context.set_offline(True)
    page.get_by_role('link', name='Lokale Kerninhalte öffnen').click()
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    page.reload()
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    response = page.goto(origin + path)
    assert response.status == 503 and response.headers['x-offline'] == '1'
    page.get_by_role('link', name='Lokale Kerninhalte öffnen').click()
    expect(page.locator('[data-ew-offline-inhalt]')).to_contain_text('Zufahrt sperren')
    with TestingSession() as db:
        set_tenant_context(db, einsatzwissen['org_id'])
        db.get(KatplanPlan, einsatzwissen['plan_id']).status = 'archiviert'
        db.commit()
    context.set_offline(False)
    expect(page.locator('[data-ew-offline-inhalt]')).to_be_empty(timeout=10000)
    expect(page.locator('[data-ew-status]')).to_contain_text('entzogen')
    assert not errors
