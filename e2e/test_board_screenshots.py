"""Screenshot walk-through of the current Einsatzboard UI.

Run explicitly after the isolated board stack has been started and seeded::

    E2E_INCIDENT_ID=<id> PLAYWRIGHT_BROWSERS_PATH=/tmp/einsatzcockpit-playwright \
      .venv/bin/pytest e2e/test_board_screenshots.py -q

This deliberately creates its demonstration cards only in the synthetic E2E
incident.  It is documentation generation, not part of the CI gate.
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect


INCIDENT_ID = int(os.environ.get("E2E_INCIDENT_ID", "351"))
SCREENSHOTS = Path(__file__).with_name("screenshots")


def _screenshot(page: Page, name: str) -> None:
    only = os.environ.get("E2E_SCREENSHOT_ONLY")
    if only and only != name:
        return
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _board(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/einsatz/{INCIDENT_ID}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#kanban")).to_be_visible()


def _wait_for_card(page: Page, kind: str, title: str):
    card = page.locator(f'.card[data-kind="{kind}"]', has_text=title).last
    expect(card).to_be_visible(timeout=10_000)
    return card


def test_board_screenshot_prepare(angemeldete_seite: Page, base_url: str) -> None:
    page = angemeldete_seite
    page.set_viewport_size({"width": 1920, "height": 1080})
    _board(page, base_url)

    # A vehicle section makes the lane menu and the section-leader picker visible.
    page.get_by_role("button", name="Abschnitt hinzufügen").click()
    section_dialog = page.locator("#addColumnDialog")
    expect(section_dialog).to_have_js_property("open", True)
    section_dialog.locator('input[name="title"]').fill("Abschnitt Brandbekämpfung")
    section_dialog.get_by_role("button", name="Anlegen").click()
    expect(section_dialog).to_have_js_property("open", False)
    # Column creation broadcasts to other clients; a fresh board fetch is also a
    # stable way to make the just-created lane available to this doc walk-through.
    _board(page, base_url)
    expect(page.locator(".kanban-col", has_text="Abschnitt Brandbekämpfung").last).to_be_visible(timeout=10_000)

    # Populate the fixed lanes through the same short forms that users use.
    page.locator("#taskInput").fill("Innenangriff vorbereiten")
    page.locator("#taskInput").locator("xpath=ancestor::form[1]").evaluate("form => form.requestSubmit()")
    task = _wait_for_card(page, "task", "Innenangriff vorbereiten")

    page.locator("#msgInput").fill("Atemschutzsammelplatz eingerichtet")
    page.locator("#msgInput").locator("xpath=ancestor::form[1]").evaluate("form => form.requestSubmit()")
    _wait_for_card(page, "message", "Atemschutzsammelplatz eingerichtet")

    page.get_by_title("Gerettete Person erfassen").click()
    person_dialog = page.locator("#personWizard")
    expect(person_dialog).to_have_js_property("open", True)
    person_dialog.locator("input[x-model=quickName]").first.fill("Maria Muster")
    person_dialog.get_by_role("button", name="Speichern").click()
    _wait_for_card(page, "person", "Maria Muster")

    section = page.locator(".kanban-col", has_text="Abschnitt Brandbekämpfung").last
    section.get_by_role("button", name="Einheit zum Einsatz hinzufügen").click()
    wizard = page.locator("#vehicleWizard")
    expect(wizard).to_have_js_property("open", True)
    suggestion = wizard.locator(".suggestion-pill--block:not([disabled])").first
    expect(suggestion).to_be_visible(timeout=10_000)
    vehicle_label = suggestion.locator("strong").inner_text()
    suggestion.click()
    wizard.get_by_role("button", name="Einheit hinzufügen").click()
    vehicle = page.locator('.card[data-kind="vehicle"]', has_text=vehicle_label).last
    expect(vehicle).to_be_visible(timeout=10_000)

    # Kept separate from capturing so a failed screenshot does not leave the
    # documentation incident half-populated during local iteration.


def test_board_screenshot_walkthrough(angemeldete_seite: Page, base_url: str) -> None:
    page = angemeldete_seite
    page.set_viewport_size({"width": 1920, "height": 1080})
    _board(page, base_url)
    section = page.locator(".kanban-col", has_text="Abschnitt Brandbekämpfung").last
    task = _wait_for_card(page, "task", "Innenangriff vorbereiten")
    vehicle = page.locator('.card[data-kind="vehicle"]').last
    expect(vehicle).to_be_visible()
    page.locator("#taskInput").fill("Wasserversorgung dokumentieren")
    page.locator("#taskInput").locator("xpath=ancestor::form[1]").evaluate("form => form.requestSubmit()")
    done_task = page.locator('.card[data-kind="task"]', has_text="Wasserversorgung dokumentieren").filter(
        has=page.locator('button[aria-label="Auftrag als erledigt markieren"]')
    ).first
    expect(done_task).to_be_visible(timeout=10_000)
    # Keep the screenshot flow read-only after setup: use the same status endpoint
    # that the visible status chip calls, then reload the board for the done group.
    done_task_id = done_task.get_attribute("data-uid")
    assert done_task_id
    csrf_token = page.evaluate(
        "document.cookie.split('; ').find(v => v.startsWith('ec_csrf='))?.split('=').slice(1).join('=')"
    )
    assert csrf_token
    response = page.request.post(
        f"{base_url}/einsatz/{INCIDENT_ID}/aufgabe/{done_task_id}/ampel",
        form={"status": "done"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.ok
    page.goto(f"{base_url}/einsatz/{INCIDENT_ID}")
    page.wait_for_load_state("domcontentloaded")
    done_toggle = page.locator(".kanban-col__done-toggle", has_text="erledigt")
    expect(done_toggle).to_be_visible(timeout=10_000)
    section_dialog = page.locator("#addColumnDialog")

    # Full desktop board first, then the compact menus/chips users interact with.
    expect(vehicle).to_be_visible()
    _screenshot(page, "board-overview.png")

    section.get_by_role("button", name="Spaltenaktionen").click()
    expect(page.get_by_role("button", name="Abschnitt umbenennen")).to_be_visible()
    page.wait_for_timeout(250)  # let the 150ms dropdown opacity transition finish
    _screenshot(page, "lane-menu.png")
    page.mouse.click(5, 5)

    section.get_by_role("button", name="Abschnittsleiter auswählen").click()
    expect(section.get_by_text("Abschnittsleiter", exact=True)).to_be_visible()
    page.wait_for_timeout(250)
    _screenshot(page, "section-leader-picker.png")
    page.mouse.click(5, 5)

    vehicle.scroll_into_view_if_needed()
    vehicle.get_by_role("button", name="Fahrzeugaktionen").click()
    expect(vehicle.get_by_role("menuitem", name="+ Meldung")).to_be_visible()
    expect(vehicle.get_by_text("Verschieben nach", exact=True)).to_be_visible()
    page.wait_for_timeout(250)
    _screenshot(page, "vehicle-card-menu.png")
    page.mouse.click(5, 5)

    task.scroll_into_view_if_needed()
    expect(task.get_by_role("button", name="Auftrag als erledigt markieren")).to_be_visible()
    _screenshot(page, "task-quick-complete.png")

    expect(done_toggle).to_have_attribute("aria-expanded", "false")
    _screenshot(page, "done-group-collapsed.png")
    done_toggle.click()
    expect(done_toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator('.card[data-kind="task"]', has_text="Wasserversorgung dokumentieren")).to_be_visible()
    _screenshot(page, "done-group-expanded.png")

    sidebar = page.locator("#sidebar")
    if "sidebar--collapsed" in (sidebar.get_attribute("class") or ""):
        page.locator(".sidebar-toggle").click()
    expect(sidebar).to_be_visible()
    page.wait_for_timeout(300)  # let the 200ms sidebar width transition finish
    _screenshot(page, "sidebar-einsatzdetails.png")

    page.get_by_role("button", name="Weitere Einsatzaktionen").click()
    expect(page.get_by_text("Ausgabe", exact=True)).to_be_visible()
    page.wait_for_timeout(250)
    _screenshot(page, "header-mehr-menu.png")
    page.mouse.click(5, 5)
    page.get_by_role("button", name="Abschnitt hinzufügen").click()
    expect(section_dialog).to_have_js_property("open", True)
    _screenshot(page, "add-section-dialog.png")
    section_dialog.get_by_role("button", name="Schließen").click()

    page.set_viewport_size({"width": 1024, "height": 768})
    _board(page, base_url)
    kanban_tablet = page.locator("#kanban")
    expect(kanban_tablet).to_be_visible()
    # Regression guard: sidebarOpen=true is still set from the drawer step above.
    # A stale @media(max-width:1100px) rule once stacked #sidebar above #kanban
    # with an unbounded sidebar height, squeezing the board to a ~24px sliver
    # whenever the drawer was open at tablet widths (769-1100px).
    tablet_box = kanban_tablet.bounding_box()
    assert tablet_box and tablet_box["height"] > 400, (
        f"Kanban board is squeezed at tablet width with sidebar open: {tablet_box}"
    )
    _screenshot(page, "tablet-view.png")

    page.set_viewport_size({"width": 390, "height": 844})
    _board(page, base_url)
    expect(page.locator("#mobile-lane-select")).to_be_visible()
    _screenshot(page, "mobile-lane-tabs.png")

    page.get_by_role("button", name="Weitere Einsatzaktionen").click()
    expect(page.get_by_text("Ausgabe", exact=True)).to_be_visible()
    page.wait_for_timeout(250)
    _screenshot(page, "mobile-header-menu.png")
