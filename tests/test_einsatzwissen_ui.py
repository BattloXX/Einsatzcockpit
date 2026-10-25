"""Echte Chromium-Darstellung und Bedienung auf Smartphone und Tablet."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_einheit_uebersicht import browser as browser
from tests.test_einsatzwissen import anlegen, veroeffentliche
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as umgebung


@pytest.mark.parametrize("breite,hoehe", [(360, 800), (768, 1024), (1024, 768)])
def test_uebersicht_mobil_und_tablet(client, umgebung, browser, breite, hoehe, tmp_path):
    from playwright.sync_api import expect

    login(client, umgebung)
    plan_id = anlegen(client, kategorie="fuehrungswissen", unterkategorie="Einsatzleitung",
                      schlagwoerter="Funkordnung", kurzbeschreibung="Kompakte Informationen für die Einsatzleitung.")
    veroeffentliche(umgebung, plan_id)
    context = browser.new_context(viewport={"width": breite, "height": hoehe}, service_workers="block")
    page = context.new_page()

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
            response = client.get(url.path + ("?" + url.query if url.query else ""))
            route.fulfill(status=response.status_code, body=response.content,
                          headers={"content-type": response.headers.get("content-type", "text/html")})

    page.route("**/*", handle)
    try:
        page.goto("https://ecp.test/einsatzwissen")
        expect(page.get_by_role("heading", name="Einsatzwissen", exact=True)).to_be_visible()
        kategorien = page.get_by_role("navigation", name="Kategorien in Einsatzwissen")
        expect(kategorien.locator("a")).to_have_count(7)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert all(box["height"] >= 44 for box in page.locator(".ew-kategorien a").evaluate_all(
            "elements => elements.map(e => ({height: e.getBoundingClientRect().height}))"))
        kategorien.get_by_role("link", name="Führungswissen").click()
        page.get_by_role("searchbox").fill("Funkordnung")
        page.get_by_role("button", name="Suchen", exact=True).click()
        expect(page.locator(".ew-titel")).to_have_text(["Wissenseintrag"])
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator(".ew-titel").scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / f"einsatzwissen-{breite}.png"))
        page.locator(".ew-titel").click()
        expect(page.get_by_role("heading", name="Wissenseintrag", exact=True)).to_be_visible()
        page.goto("https://ecp.test/einsatzwissen")
        expect(page.locator("#ew-zuletzt + .ew-karten .ew-titel")).to_have_text(["Wissenseintrag"])
    finally:
        context.close()
