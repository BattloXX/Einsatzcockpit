"""Zentraler Druckdialog mit echten HTTP-Endpunkten auf Desktop und Touch-Ansichten."""

import io
from urllib.parse import urlsplit

import pytest
from pypdf import PdfReader

from tests.test_dokumentdruck import station as station
from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_dokumentviewer_ui import gezeichnet
from tests.test_dokumentviewer_ui import viewer_seite as viewer_seite
from tests.test_einheit_uebersicht import browser as browser
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401


def oeffnen(page, client):
    page.context.add_cookies([{"name": "ec_csrf", "value": client.cookies.get("ec_csrf"),
                               "url": "https://ecp.test"}])
    page.evaluate("window.allowDocumentPrint = true")
    page.locator('[data-dv-group]').filter(has_text="Vier Unterlagen").locator(
        '[data-dv-document][data-dv-kind="pdf"]').first.click()
    gezeichnet(page, 1)
    page.get_by_role("spinbutton", name="Sprung zu Seite").fill("5")
    page.get_by_role("spinbutton", name="Sprung zu Seite").press("Tab")
    gezeichnet(page, 5)
    page.get_by_role("button", name="Drucken", exact=True).click()
    return page.locator('#documentPrintDialog')


def druckpdf(dialog, client):
    from playwright.sync_api import expect

    dialog.get_by_role("button", name="PDF vorbereiten").click()
    link = dialog.get_by_role("link", name="PDF öffnen", exact=True)
    expect(link).to_be_visible()
    url = urlsplit(link.get_attribute("href"))
    response = client.get(url.path + "?" + url.query)
    assert response.status_code == 200, response.text
    return PdfReader(io.BytesIO(response.content))


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 768), (390, 844)])
def test_druckdialog_aktuell_bereich_frei_mehrere_reihenfolge(viewer_seite, client, width, height, tmp_path):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    page.set_viewport_size({"width": width, "height": height})
    dialog = oeffnen(page, client)
    expect(dialog).to_be_visible()
    assert druckpdf(dialog, client).pages[0].extract_text().startswith("Unterlage - Seite 5")
    dialog.locator('[data-dp-mode]').select_option("range")
    dialog.locator('[data-dp-pages]').fill("2–4")
    reader = druckpdf(dialog, client)
    assert [float(p.mediabox.width) for p in reader.pages] == [301, 302, 303]
    dialog.locator('[data-dp-mode]').select_option("free")
    dialog.locator('[data-dp-pages]').fill("1,5,8")
    reader = druckpdf(dialog, client)
    assert [float(p.mediabox.width) for p in reader.pages] == [300, 304, 307]
    dialog.locator('[data-dp-mode]').select_option("multiple")
    rows = dialog.locator('[data-dp-documents] li')
    # Galerie: Bild an erster Stelle, danach drei PDFs.
    rows.nth(1).locator('input[type="text"]').fill("2–4")
    rows.nth(2).locator('input[type="checkbox"]').check()
    rows.nth(2).locator('input[type="text"]').fill("8")
    rows.nth(2).get_by_role("button", name="Nach oben").click()
    reader = druckpdf(dialog, client)
    assert [float(p.mediabox.width) for p in reader.pages] == [307, 301, 302, 303]
    expect(dialog.get_by_role("link", name="PDF herunterladen")).to_be_visible()
    assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert dialog.get_by_role("button", name="Drucken", exact=True).bounding_box()["height"] >= 44
    page.screenshot(path=str(tmp_path / f"dokumentdruck-{width}.png"))
    dialog.get_by_role("button", name="Druckdialog schließen").click()
    expect(page.locator('#documentViewer')).to_be_visible()
    assert not errors


def test_lokaler_druck_und_fallback_ohne_erfolgsbehauptung(viewer_seite, client):
    from playwright.sync_api import expect

    page, errors = viewer_seite
    dialog = oeffnen(page, client)
    # Native PDF-/OS-Druckoberfläche ist in Headless-Chromium nicht automatisierbar.
    # Eine druckbare Same-Origin-Antwort prüft den Browseraufruf und Fehlerfallback.
    page.route("**/druck/viewer/pdf?**", lambda route: route.fulfill(
        content_type="text/html", body='<embed type="application/pdf"><script>window.print=()=>{'
                                      'parent.window.printCalled=true}</script>'))
    dialog.get_by_role("button", name="Drucken", exact=True).click()
    page.wait_for_function("window.printCalled === true")
    expect(dialog.locator('[data-dp-status]')).to_contain_text("nicht automatisch prüfbar")
    expect(dialog).to_have_attribute("data-confirmed", "false")
    expect(dialog.get_by_role("link", name="PDF öffnen", exact=True)).to_be_visible()
    page.unroute("**/druck/viewer/pdf?**")
    page.route("**/druck/viewer/pdf?**", lambda route: route.fulfill(
        content_type="text/html", body='<embed type="application/pdf"><script>window.print=()=>{'
                                      'throw Error("nicht unterstützt")}</script>'))
    dialog.get_by_role("button", name="Drucken", exact=True).click()
    expect(dialog.locator('[data-dp-status]')).to_contain_text("Bitte PDF öffnen oder herunterladen")
    assert not errors


def test_offline_stationsdrucker_meldet_fehler_ohne_auftrag(viewer_seite, client, dokumente, station):
    from playwright.sync_api import expect

    from app.core.tenant import set_tenant_context
    from app.models.gateway import PrinterAgentRoute, PrintJob
    from tests.conftest import TestingSession

    page, errors = viewer_seite
    login(client, dokumente, "org_admin")
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(PrinterAgentRoute, station["route_id"]).ready = False
        db.commit()
    dialog = oeffnen(page, client)
    option = dialog.get_by_role("radio", name="Über Stationsdrucker drucken")
    expect(option).to_be_enabled()
    option.check()
    expect(dialog.locator('[data-dp-printer-note]')).to_contain_text("offline")
    expect(dialog.get_by_role("button", name="Druckauftrag senden")).to_be_disabled()
    dialog.get_by_role("radio", name="Auf diesem Gerät drucken").check()
    expect(dialog.get_by_role("button", name="Drucken", exact=True)).to_be_enabled()
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(PrintJob).filter_by(printer_id=station["printer_id"]).count() == 0
    assert not errors


def test_stationsauftrag_status_und_doppelklick(viewer_seite, client, dokumente, station):
    from playwright.sync_api import expect

    from app.core.tenant import set_tenant_context
    from app.models.gateway import Gateway, PrintJob
    from app.services import print_agent_service as agent
    from tests.conftest import TestingSession

    page, errors = viewer_seite
    login(client, dokumente, "org_admin")
    dialog = oeffnen(page, client)
    option = dialog.get_by_role("radio", name="Über Stationsdrucker drucken")
    expect(option).to_be_enabled()
    option.check()
    dialog.locator('[data-dp-format]').select_option("A3")
    dialog.locator('[data-dp-copies]').fill("2")
    druckpdf(dialog, client)
    # Eine Änderung nach der Vorbereitung muss vor dem Druck stoppen und die
    # erneute Vorbereitung freigeben, statt einen alten Token wiederzuverwenden.
    from pypdf import PdfWriter

    current_url = page.evaluate("documentViewerSelection().document.url")
    path = next(datei["path"] for datei in dokumente["dateien"] if datei["uuid"] in current_url)
    writer = PdfWriter(clone_from=io.BytesIO(path.read_bytes()))
    writer.add_metadata({"/Title": "Geänderte Unterlage"})
    stream = io.BytesIO()
    writer.write(stream)
    path.write_bytes(stream.getvalue())
    button = dialog.get_by_role("button", name="Druckauftrag senden")
    button.click()
    expect(dialog.locator('[data-dp-status]')).to_contain_text("Dokument wurde geändert")
    expect(dialog.get_by_role("link", name="PDF öffnen", exact=True)).not_to_be_visible()
    druckpdf(dialog, client)
    button.evaluate("e => { e.click(); e.click(); }")
    expect(dialog.locator('[data-dp-status]')).to_contain_text("Wartet auf Druckdienst")
    expect(button).to_be_disabled()
    expect(dialog).to_have_attribute("data-confirmed", "false")
    with TestingSession() as db:
        set_tenant_context(db, None)
        jobs = db.query(PrintJob).filter_by(printer_id=station["printer_id"]).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.options["media"] == "A3" and job.options["copies"] == 2
        gateway = db.get(Gateway, station["gateway_id"])
        grant = agent.lease_jobs(db, gateway, 1)[0]
        assert agent.print_intent(db, gateway, job.id, grant["lease_token"])[0]
        agent.apply_agent_status(db, gateway, job.id, grant["lease_token"], "printing", "spooled", "win:42", None)
        agent.apply_agent_status(db, gateway, job.id, grant["lease_token"], "done", "spooled", "win:42", None)
    dialog.get_by_role("button", name="Status prüfen").click()
    expect(dialog).to_have_attribute("data-confirmed", "true")
    expect(dialog.locator('[data-dp-status]')).to_contain_text("Abschluss bestätigt")
    assert not errors
