"""Übernahmedialog und Einsatzfassung im echten PDF.js auf Desktop und Tablet."""

import io

import pytest
from pypdf import PdfReader

from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_dokumentviewer_ui import gezeichnet
from tests.test_dokumentviewer_ui import viewer_seite as viewer_seite
from tests.test_einheit_uebersicht import browser as browser
from tests.test_einsatz_dokumente import einsatz as einsatz
from tests.test_einsatz_dokumente import gk
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 768), (390, 844)])
def test_seiten_uebernahme_board_gk_und_viewerdruck(viewer_seite, client, dokumente, einsatz, width, height):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    login(client, dokumente, "org_admin")
    page.context.add_cookies([{"name": "ec_csrf", "value": client.cookies.get("ec_csrf"),
                              "url": "https://ecp.test"}])
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f'https://ecp.test/plaene/{dokumente["plan_id"]}?ansicht=freigabe')
    article = page.locator('[data-dv-group]').filter(has_text="Vier Unterlagen")
    article.locator('[data-dv-document][data-dv-title="Unterlage 1"]').first.click()
    gezeichnet(page, 1)
    page.get_by_text("Seitenauswahl", exact=True).click()
    page.locator('[data-dv-selection]').fill("4–6")
    page.get_by_role("button", name="Zum Einsatz hinzufügen", exact=True).click()
    dialog = page.locator('#documentTransferDialog')
    expect(dialog).to_be_visible()
    dialog.locator('[data-ed-target]').select_option(f'lage/{einsatz["lage"]}')
    expect(dialog.locator('[data-ed-submit]')).to_be_enabled()
    dialog.locator('[data-ed-order]').select_option(str(einsatz["site"]))
    dialog.locator(f'[data-ed-unit][value="{einsatz["einheiten"][0]}"]').check()
    dialog.get_by_role("button", name="Zum Einsatz hinzufügen", exact=True).click()
    link = dialog.get_by_role("link", name="Dokumente im Einsatz öffnen")
    expect(link).to_be_visible()
    assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert dialog.locator('[data-ed-submit]').bounding_box()["height"] >= 44
    link.click()
    expect(page.get_by_role("heading", name="Unterlage 1", exact=True)).to_be_visible()
    expect(page.get_by_text("4,5,6 (3 Seiten)", exact=True)).to_be_visible()
    page.get_by_role("link", name="Anzeigen", exact=True).click()
    gezeichnet(page, 1, 3)
    page.locator('#documentViewer').get_by_role("button", name="Drucken", exact=True).click()
    assert page.evaluate("printRequests[0].pages") == [1]
    page.get_by_role("button", name="Dokumentenviewer schließen").click()
    url = page.get_by_role("link", name="Anzeigen", exact=True).get_attribute("href")
    ident = int(url.rsplit("/", 1)[1])
    gk(client, einsatz)
    page.goto('https://ecp.test/einheit')
    expect(page.get_by_role("link", name="Unterlage 1 öffnen", exact=True)).to_be_visible()
    page.get_by_role("link", name="Unterlage 1 öffnen", exact=True).click()
    gezeichnet(page, 1, 3)
    data = client.get(f"/einheit/api/dokumente/{ident}/pdf?auswahl=1,3")
    assert len(PdfReader(io.BytesIO(data.content)).pages) == 2
    assert not errors


def test_mehrfachauswahl_und_massnahmensammlung(viewer_seite, client, dokumente, einsatz):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    login(client, dokumente, "org_admin")
    page.context.add_cookies([{"name": "ec_csrf", "value": client.cookies.get("ec_csrf"),
                              "url": "https://ecp.test"}])
    page.goto(f'https://ecp.test/plaene/{dokumente["plan_id"]}?ansicht=freigabe')
    page.get_by_role("button", name="Dokumentensammlung zum Einsatz hinzufügen").first.click()
    dialog = page.locator('#documentTransferDialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('[data-ed-documents] input[type=checkbox]')).to_have_count(5)
    # Die Sammlung zeigt jede Unterlage; eigene Seiten pro markiertem Dokument.
    rows = dialog.locator('[data-ed-documents] > div')
    for checkbox in rows.locator('input[type=checkbox]').all():
        checkbox.uncheck()
    rows.nth(0).locator('input[type=checkbox]').check()
    rows.nth(0).locator('input[type=text]').fill('4-6')
    rows.nth(1).locator('input[type=checkbox]').check()
    rows.nth(1).locator('input[type=text]').fill('1')
    dialog.locator('[data-ed-target]').select_option(f'einsatz/{einsatz["incident"]}')
    expect(dialog.locator('[data-ed-submit]')).to_be_enabled()
    dialog.locator('[data-ed-submit]').click()
    expect(dialog.locator('[data-ed-status]')).to_contain_text('2 Dokument(e) übernommen')
    assert not errors


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 768), (390, 844)])
@pytest.mark.parametrize("art,key,prefix,menue", [
    ("einsatz", "incident", "/einsatz", "Weitere Einsatzaktionen"),
    ("lage", "lage", "/lage", "Weitere Lageaktionen"),
])
def test_board_dokumentfenster_lazy_refresh_zuordnung_und_escape(
    viewer_seite, client, dokumente, einsatz, width, height, art, key, prefix, menue,
):
    from playwright.sync_api import expect

    from tests.test_einsatz_dokumente import uebernehmen

    page, errors = viewer_seite
    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz, art=art, einsatz_id=einsatz[key])
    assert result.status_code == 200, result.text
    ident = result.json()["ids"][0]
    page.context.add_cookies([{"name": "ec_csrf", "value": client.cookies.get("ec_csrf"), "url": "https://ecp.test"}])
    path = f'/einsatz-dokumente/liste/{art}/{einsatz[key]}'
    requests = []
    page.on("request", lambda r: requests.append(r.url) if path in r.url else None)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f'https://ecp.test{prefix}/{einsatz[key]}')
    page.wait_for_load_state("networkidle")
    assert requests == []
    dialog = page.locator('#einsatzDokumenteDialog')
    expect(dialog).not_to_be_visible()
    trigger = page.get_by_role("button", name=menue, exact=True)
    assert trigger.bounding_box()["height"] >= 44
    trigger.click()
    button = page.get_by_role("menuitem", name="Dokumente", exact=True)
    expect(button).to_be_visible()
    assert button.bounding_box()["height"] >= 44
    with page.expect_response(lambda r: path in r.url):
        button.click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="Unterlage 1", exact=True)).to_be_visible()
    assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert dialog.get_by_role("button", name="Dokumente schließen").bounding_box()["height"] >= 44
    dialog.get_by_text("Auftrag und Freigaben bearbeiten", exact=True).click()
    dialog.locator('[name="auftrag_id"]').select_option(str(einsatz["task" if art == "einsatz" else "site"]))
    with page.expect_response(lambda r: f'/einsatz-dokumente/{ident}/zuordnung' in r.url):
        dialog.get_by_role("button", name="Zuordnung speichern", exact=True).click()
    expect(dialog.get_by_text("Zugehöriger Auftrag", exact=True).locator("..")).to_contain_text("Wasserentnahme")
    with page.expect_response(lambda r: path in r.url):
        page.evaluate("document.body.dispatchEvent(new CustomEvent('dokumente-aktualisiert', {bubbles:true}))")
    assert len(requests) == 2
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    page.evaluate("document.body.dispatchEvent(new CustomEvent('dokumente-aktualisiert', {bubbles:true}))")
    page.wait_for_timeout(100)
    assert len(requests) == 2
    page.get_by_role("button", name=menue, exact=True).click()
    with page.expect_response(lambda r: path in r.url):
        page.get_by_role("menuitem", name="Dokumente", exact=True).click()
    dialog.get_by_role("button", name="Dokumente schließen").click()
    expect(dialog).not_to_be_visible()
    assert not errors
