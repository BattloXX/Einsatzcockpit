"""Browser-Abnahme des zentralen Kontakte-Moduls gegen Docker/MariaDB."""

from __future__ import annotations

from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.parametrize("mobile_width", [390, 360])
def test_kontakt_anlegen_suchen_und_mobil_telefonieren(
    angemeldete_seite: Page, base_url: str, mobile_width: int
) -> None:
    """Deckt Desktop-Formular/Live-Suche sowie mobile Tel-/SMS-Aktionen ab."""
    page = angemeldete_seite
    page.goto(f"{base_url}/?org=1")
    page.goto(f"{base_url}/kontakte/")
    expect(page.get_by_role("heading", name="Kontakte")).to_be_visible()

    suffix = uuid4().hex[:8]
    name = f"E2E Kontakt {suffix}"
    number = f"+43664{uuid4().int % 10_000_000:07d}"
    page.get_by_role("button", name="+ Neuer Kontakt").click()
    modal = page.locator("#kontaktModal")
    expect(modal).to_be_visible()
    modal.locator('input[name="anzeigename"]').fill(name)
    modal.locator('input[name="nummer"]').fill(number)
    modal.locator('input[name="sms_eignung"]').check()
    modal.locator(".modal__body").evaluate("element => element.scrollTop = element.scrollHeight")
    modal.get_by_role("button", name="Speichern").click()
    expect(page.locator("#kontakt-liste").get_by_text(name, exact=True)).to_be_visible()

    search = page.locator('input[type="search"][name="q"]')
    search.fill(suffix)
    expect(page.locator("#kontakt-liste").get_by_text(name, exact=True)).to_be_visible()
    row = page.locator("#kontakt-liste").get_by_text(name, exact=True).locator("xpath=ancestor::a[1]")
    row.click()
    expect(page.locator("#kontakt-detail").get_by_text(name, exact=True)).to_be_visible()

    page.set_viewport_size({"width": mobile_width, "height": 844})
    page.reload()
    expect(page.locator('a[aria-label$="anrufen"]')).to_have_attribute("href", f"tel:{number}")
    expect(page.locator('a[aria-label^="SMS an"]')).to_have_attribute("href", f"sms:{number}")
