"""Browser-Regressionen fuer reloadfreie Einsatzboards.

Der Dump-basierte Lauf nutzt standardmaessig Einsatz 351. CI setzt
``E2E_INCIDENT_ID`` auf die ID des synthetisch geseedeten Einsatzes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, expect

INCIDENT_ID = int(os.environ.get("E2E_INCIDENT_ID", "351"))
REDIS_CONTAINER = os.environ.get("E2E_REDIS_CONTAINER", "ec-board-e2e-redis-1")
APP_CONTAINER = os.environ.get("E2E_APP_CONTAINER", "ec-board-e2e-app-1")


def _publish(event: dict) -> None:
    payload = json.dumps({"key": INCIDENT_ID, "event": event}, separators=(",", ":"))
    subscribers = 0
    for _ in range(20):
        result = subprocess.run(
            ["docker", "exec", REDIS_CONTAINER, "redis-cli", "PUBLISH", "ec:ws", payload],
            check=True,
            capture_output=True,
            text=True,
        )
        subscribers = int(result.stdout.strip())
        if subscribers > 0:
            return
        time.sleep(0.25)
    assert subscribers > 0


def _board(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/einsatz/{INCIDENT_ID}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#kanban")).to_be_visible()
    expect(page.locator(".card[data-uid]").first).to_be_visible()


def _mutation(page: Page, marker: str) -> None:
    field = page.locator("#msgInput")
    field.fill(marker)
    field.locator("xpath=ancestor::form[1]").evaluate("form => form.requestSubmit()")


def _task_mutation(page: Page, marker: str) -> None:
    field = page.locator("#taskInput")
    field.fill(marker)
    field.locator("xpath=ancestor::form[1]").evaluate("form => form.requestSubmit()")


def _load_id(page: Page) -> str | None:
    try:
        return page.evaluate("window.__ecLoadId")
    except Exception:
        return None


def _move_card(page: Page, kind: str, uid: str, **payload: str) -> None:
    """Sendet denselben form-urlencoded DnD-Request wie sortable-glue.js."""
    data = {"kind": kind, "uid": uid, "position": "0", **payload}
    status = page.evaluate(
        """async ({incidentId, data}) => {
          const clientId = sessionStorage.getItem('ecBoardClientId');
          const response = await fetch(`/einsatz/${incidentId}/karte/verschieben`, {
            method: 'POST', headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
              'X-EC-Client': clientId,
            },
            body: new URLSearchParams(data), credentials: 'same-origin'
          });
          return response.status;
        }""",
        {"incidentId": INCIDENT_ID, "data": data},
    )
    assert status == 204


def test_board_bleibt_bei_updates_und_reconnect_montiert(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    """Gate: Vor Batch 1 loesen Sync-Events und kurzer Offlinewechsel Reloads aus."""
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)
    assert load_id
    loads: list[float] = []
    page.on("load", lambda _: loads.append(time.monotonic()))

    kanban = page.locator("#kanban")
    kanban.evaluate("el => { el.scrollLeft = Math.min(180, el.scrollWidth - el.clientWidth); }")
    scroll_left = kanban.evaluate("el => el.scrollLeft")
    card = page.locator(".card[data-uid]").first
    card.locator(".card__title").first.click()
    expect(page.locator("#cardDetailModal")).to_have_js_property("open", True)
    draft = "e2e-draft-" + uuid4().hex
    draft_field = page.locator("#cardDetailModal").locator('input[type="text"]:visible, textarea:visible').last
    expect(draft_field).to_be_visible()
    draft_field.fill(draft)
    draft_field.evaluate("el => { el.dataset.e2eDraft = '1'; el.focus(); }")

    for event_type in ("lis_sync", "dibos_sync", "objektgefahren"):
        _publish({"type": event_type, "reload_board": True})
        page.wait_for_timeout(800)

    marker = "e2e-update-" + uuid4().hex
    other = zweiter_kontext.pages[0]
    _board(other, base_url)
    _mutation(other, marker)
    expect(page.locator(".card", has_text=marker)).to_be_visible(timeout=10_000)

    page.context.set_offline(True)
    page.wait_for_timeout(1_000)
    page.context.set_offline(False)
    expect(page.get_by_text("verbunden", exact=True)).to_be_visible(timeout=20_000)
    _publish({"type": "lis_sync"})
    page.wait_for_timeout(1_500)

    assert _load_id(page) == load_id
    assert loads == []
    assert kanban.evaluate("el => el.scrollLeft") == scroll_left
    expect(page.locator('[data-e2e-draft="1"]')).to_have_value(draft)
    expect(page.locator("#cardDetailModal")).to_have_js_property("open", True)
    assert page.evaluate("document.activeElement && document.activeElement.dataset.e2eDraft") == "1"
    expect(page.locator(".card", has_text=marker)).to_be_visible()


@pytest.mark.parametrize("delay_ms", [100, 250, 500])
def test_fragment_updates_mit_latenz_ohne_reload(angemeldete_seite: Page, base_url: str, delay_ms: int) -> None:
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)
    requests = []

    def delayed(route):
        requests.append(route.request.url)
        time.sleep(delay_ms / 1000)
        route.continue_()

    page.route(re.compile(rf".*/einsatz/{INCIDENT_ID}/kanban(?:\?.*)?$"), delayed)
    page.evaluate(
        "url => { setTimeout(() => fetch(url, {headers:{'HX-Request':'true'}}), 0); }",
        f"/einsatz/{INCIDENT_ID}/kanban",
    )
    page.wait_for_timeout(delay_ms + 1_000)
    assert _load_id(page) == load_id
    assert len(requests) == 1


@pytest.mark.parametrize("status", [500, 401, 403])
def test_fragmentfehler_erzeugen_weder_reload_noch_endlosloop(
    angemeldete_seite: Page, base_url: str, status: int
) -> None:
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)
    requests = 0

    def fail(route):
        nonlocal requests
        requests += 1
        route.fulfill(status=status, body="")

    page.route(re.compile(rf".*/einsatz/{INCIDENT_ID}/kanban(?:\?.*)?$"), fail)
    page.evaluate(
        "url => { setTimeout(() => fetch(url, {headers:{'HX-Request':'true'}}), 0); }",
        f"/einsatz/{INCIDENT_ID}/kanban",
    )
    page.wait_for_timeout(2_000)
    assert _load_id(page) == load_id
    assert requests == 1


@pytest.mark.slow
def test_backend_neustart_resynct_ohne_reload(angemeldete_seite: Page, base_url: str) -> None:
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)
    subprocess.run(["docker", "restart", APP_CONTAINER], check=True, capture_output=True)
    expect(page.get_by_text("verbunden", exact=True)).to_be_visible(timeout=45_000)
    _publish({"type": "lis_sync"})
    page.wait_for_timeout(1_000)
    assert _load_id(page) == load_id


def test_hintergrund_tab_und_viewports(angemeldete_seite: Page, base_url: str) -> None:
    page = angemeldete_seite
    for viewport in ({"width": 1024, "height": 768}, {"width": 2560, "height": 1440}):
        page.set_viewport_size(viewport)
        _board(page, base_url)
        assert page.locator("#kanban").bounding_box()
    load_id = _load_id(page)
    page.context.new_page().bring_to_front()
    page.wait_for_timeout(1_000)
    page.bring_to_front()
    _publish({"type": "lis_sync"})
    page.wait_for_timeout(1_000)
    assert _load_id(page) == load_id


def test_header_actions_follow_mannschaft_und_bleiben_mobil_erreichbar(
    angemeldete_seite: Page, base_url: str
) -> None:
    page = angemeldete_seite
    page.set_viewport_size({"width": 1920, "height": 1080})
    _board(page, base_url)
    crew = page.get_by_role("link", name=re.compile("Mannschaft"))
    more = page.get_by_role("button", name="Weitere Einsatzaktionen")
    expect(crew).to_be_visible()
    expect(more).to_be_visible()
    assert crew.bounding_box() and more.bounding_box()
    assert more.bounding_box()["x"] > crew.bounding_box()["x"]

    page.set_viewport_size({"width": 390, "height": 844})
    expect(more).to_be_visible()
    # The single weather control is independently reachable and opens one panel.
    weather = page.get_by_role("button", name="Wetter öffnen")
    if weather.count():
        weather.click()
        expect(page.locator("#wetter-panel")).to_have_count(1)


def test_zwei_sessions_aendern_parallel(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)
    one, two = "e2e-a-" + uuid4().hex, "e2e-b-" + uuid4().hex
    _task_mutation(first, one)
    _mutation(second, two)
    expect(first.locator(".card", has_text=one)).to_be_visible(timeout=10_000)
    expect(second.locator(".card", has_text=two)).to_be_visible(timeout=10_000)
    expect(first.locator(".card", has_text=two)).to_be_visible(timeout=10_000)
    expect(second.locator(".card", has_text=one)).to_be_visible(timeout=10_000)


def test_statuswechsel_aktualisieren_alle_kartentypen_in_beiden_sessions(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    """Die echten Status-Controls synchronisieren jede Kartenart ohne Reload."""
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)
    first_load_id, second_load_id = _load_id(first), _load_id(second)

    first.get_by_title("Einheit zum Einsatz hinzufügen").first.click()
    suggestion = first.locator("#vehicleWizard .suggestion-pill--block:not([disabled])").first
    expect(suggestion).to_be_visible(timeout=10_000)
    vehicle_code = suggestion.locator("strong").inner_text()
    suggestion.click()
    first.locator("#vehicleWizard form").first.get_by_role("button", name="Einheit hinzufügen").click()
    vehicle = first.locator('.card[data-kind="vehicle"]', has_text=vehicle_code).last
    expect(vehicle).to_be_visible(timeout=10_000)
    vehicle_id = vehicle.get_attribute("data-uid")
    assert vehicle_id
    vehicle_status = vehicle.locator("button[aria-label^='Einheitenstatus:']")
    vehicle_status.click()
    vehicle_next = vehicle.locator("[role=menuitem]").first
    expected_vehicle_status = vehicle_next.inner_text()
    vehicle_next.click()
    expect(first.locator(f"#vehicle-card-{vehicle_id} button[aria-label^='Einheitenstatus:']")).to_have_text(
        expected_vehicle_status, timeout=10_000
    )
    expect(second.locator(f"#vehicle-card-{vehicle_id} button[aria-label^='Einheitenstatus:']")).to_have_text(
        expected_vehicle_status, timeout=10_000
    )

    task = first.locator('.card[data-kind="task"]').first
    task_id = task.get_attribute("data-uid")
    assert task_id
    task.locator("button[aria-label^='Auftragsstatus:']").click()
    task.get_by_role("menuitem", name="Status auf In Arbeit setzen").click()
    expect(first.locator(f"#task-card-{task_id}")).to_have_attribute("data-status", "in_progress", timeout=10_000)
    expect(second.locator(f"#task-card-{task_id}")).to_have_attribute("data-status", "in_progress", timeout=10_000)

    message_title = "E2E Status Meldung " + uuid4().hex
    _mutation(first, message_title)
    message = first.locator('.card[data-kind="message"]', has_text=message_title)
    expect(message).to_be_visible(timeout=10_000)
    message_id = message.get_attribute("data-uid")
    assert message_id
    message.locator("button[aria-label^='Meldungsstatus:']").click()
    message.get_by_role("menuitem", name="Status auf Achtung setzen").click()
    expect(first.locator(f"#msg-card-{message_id}")).to_have_attribute("data-status", "achtung", timeout=10_000)
    expect(second.locator(f"#msg-card-{message_id}")).to_have_attribute("data-status", "achtung", timeout=10_000)

    person_name = "E2E Status Person " + uuid4().hex[:8]
    first.get_by_title("Gerettete Person erfassen").first.click()
    first.locator("#personWizard input[x-model=quickName]").first.fill(person_name)
    first.locator("#personWizard").get_by_role("button", name="Speichern").click()
    person = first.locator('.card[data-kind="person"]', has_text=person_name)
    expect(person).to_be_visible(timeout=10_000)
    person_id = person.get_attribute("data-uid")
    assert person_id
    person.locator("select[name=status]").select_option("versorgt")
    expect(first.locator(f"#person-card-{person_id}")).to_have_attribute("data-status", "versorgt", timeout=10_000)
    expect(second.locator(f"#person-card-{person_id}")).to_have_attribute("data-status", "versorgt", timeout=10_000)

    assert _load_id(first) == first_load_id
    assert _load_id(second) == second_load_id


def test_personen_abschnitt_erscheint_desktop_und_im_mobilen_personen_lane(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    first.set_viewport_size({"width": 1920, "height": 1080})
    _board(first, base_url)
    _board(second, base_url)

    first.get_by_role("button", name="Abschnitt hinzufügen").click()
    dialog = first.locator("#addColumnDialog")
    dialog.locator('input[name="title"]').fill("E2E Personenabschnitt")
    dialog.locator('select[name="column_kind"]').select_option("rescued")
    dialog.get_by_role("button", name="Anlegen").click()

    own_person_col = first.locator(".kanban-col").filter(
        has=first.locator(".kanban-col__title", has_text="E2E Personenabschnitt")
    )
    expect(own_person_col).to_be_visible(timeout=10_000)
    person_col = second.locator(".kanban-col").filter(
        has=second.locator(".kanban-col__title", has_text="E2E Personenabschnitt")
    )
    expect(person_col).to_be_visible(timeout=10_000)
    col_id = person_col.get_attribute("id")
    assert col_id and col_id.startswith("col-")
    column_id = col_id.removeprefix("col-")

    second.set_viewport_size({"width": 390, "height": 844})
    second.locator("#mobile-lane-select").select_option(f"col-{column_id}")
    expect(person_col).to_have_attribute("data-lane-active", "")
    expect(person_col).to_be_visible()

    csrf_token = first.evaluate(
        "document.cookie.split('; ').find(v => v.startsWith('ec_csrf='))?.split('=').slice(1).join('=')"
    )
    assert csrf_token
    response = first.request.delete(
        f"{base_url}/einsatz/{INCIDENT_ID}/spalten/{column_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status == 204


def test_person_can_move_repeatedly_between_rescued_columns_without_duplicates(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    """A person has one persisted rescued column, even across repeated DnD moves."""
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)

    title = "E2E Zweite Personen-Spalte " + uuid4().hex[:8]
    first.get_by_role("button", name="Abschnitt hinzufügen").click()
    dialog = first.locator("#addColumnDialog")
    dialog.locator('input[name="title"]').fill(title)
    dialog.locator('select[name="column_kind"]').select_option("rescued")
    dialog.get_by_role("button", name="Anlegen").click()

    second_column = first.locator(".kanban-col").filter(
        has=first.locator(".kanban-col__title", has_text=title)
    )
    expect(second_column).to_be_visible(timeout=10_000)
    second_column_id = second_column.get_attribute("data-col-id")
    first_column = first.locator(
        '.kanban-col:has([title="Gerettete Person erfassen"])'
    ).filter(has_not_text=title).first
    first_column_id = first_column.get_attribute("data-col-id")
    assert first_column_id and second_column_id

    person_name = "E2E Mehrfachzug Person " + uuid4().hex[:8]
    second_column.get_by_title("Gerettete Person erfassen").click()
    first.locator("#personWizard input[x-model=quickName]").first.fill(person_name)
    first.locator("#personWizard").get_by_role("button", name="Speichern").click()
    person = first.locator('.card[data-kind="person"]', has_text=person_name)
    expect(person).to_be_visible(timeout=10_000)
    person_uid = person.get_attribute("data-uid")
    assert person_uid

    for target_column_id in (first_column_id, second_column_id, first_column_id, second_column_id):
        _move_card(first, "person", person_uid, column_id=target_column_id)
        for page in (first, second):
            target_card = page.locator(
                f"#zone-{target_column_id} .card[data-kind='person']", has_text=person_name
            )
            expect(target_card).to_have_count(1, timeout=10_000)
            expect(page.locator('.card[data-kind="person"]', has_text=person_name)).to_have_count(1, timeout=10_000)

    csrf_token = first.evaluate(
        "document.cookie.split('; ').find(v => v.startsWith('ec_csrf='))?.split('=').slice(1).join('=')"
    )
    assert csrf_token
    person_response = first.request.post(
        f"{base_url}/einsatz/{INCIDENT_ID}/person/{person_uid}/loeschen",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert person_response.status in (200, 204)
    response = first.request.delete(
        f"{base_url}/einsatz/{INCIDENT_ID}/spalten/{second_column_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status == 204


def test_mobile_lane_selector_lists_each_custom_column_separately(
    angemeldete_seite: Page, base_url: str
) -> None:
    """Zusatzspalten aller Kinds bleiben mobil einzeln statt gruppiert sichtbar."""
    page = angemeldete_seite
    _board(page, base_url)
    columns: list[tuple[str, str]] = []
    for kind, prefix in (("tasks", "E2E Zweiter Auftrag"), ("messages", "E2E Zweite Meldung")):
        title = prefix + " " + uuid4().hex[:8]
        page.get_by_role("button", name="Abschnitt hinzufügen").click()
        dialog = page.locator("#addColumnDialog")
        dialog.locator('input[name="title"]').fill(title)
        dialog.locator('select[name="column_kind"]').select_option(kind)
        dialog.get_by_role("button", name="Anlegen").click()
        column = page.locator(".kanban-col").filter(
            has=page.locator(".kanban-col__title", has_text=title)
        )
        expect(column).to_be_visible(timeout=10_000)
        column_id = column.get_attribute("data-col-id")
        assert column_id
        columns.append((title, column_id))

    page.set_viewport_size({"width": 390, "height": 844})
    selector = page.locator("#mobile-lane-select")
    expect(selector).to_be_visible()
    values = selector.locator("option").evaluate_all("options => options.map(option => option.value)")
    assert [f"col-{column_id}" for _, column_id in columns] == [
        value for value in values if value in {f"col-{column_id}" for _, column_id in columns}
    ]
    for title, column_id in columns:
        lane = f"col-{column_id}"
        selector.select_option(lane)
        selected = page.locator(f'.kanban-col[data-lane="{lane}"]')
        expect(selected).to_have_attribute("data-lane-active", "")
        expect(selected).to_be_visible()
        for _, other_id in columns:
            if other_id != column_id:
                expect(page.locator(f'.kanban-col[data-lane="col-{other_id}"]')).not_to_have_attribute(
                    "data-lane-active", ""
                )

    csrf_token = page.evaluate(
        "document.cookie.split('; ').find(v => v.startsWith('ec_csrf='))?.split('=').slice(1).join('=')"
    )
    assert csrf_token
    for _, column_id in columns:
        response = page.request.delete(
            f"{base_url}/einsatz/{INCIDENT_ID}/spalten/{column_id}", headers={"X-CSRF-Token": csrf_token}
        )
        assert response.status == 204


def test_mobile_root_scrolling_bottom_nav_and_contextual_fab(
    angemeldete_seite: Page, base_url: str
) -> None:
    """Root-Scroll bleibt fuer PTR frei; FAB delegiert nur auf dem Board."""
    page = angemeldete_seite
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/einsatz/{INCIDENT_ID}/info")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.scrollingElement.scrollHeight > document.scrollingElement.clientHeight")

    _board(page, base_url)
    assert page.evaluate("document.scrollingElement.scrollHeight > document.scrollingElement.clientHeight")
    assert page.locator("#kanban").evaluate("el => el.scrollHeight >= el.clientHeight")

    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".bottom-nav")).to_be_visible()
    page.locator(".bottom-nav__fab").click()
    expect(page.locator("#newIncidentModal")).to_have_js_property("open", True)
    page.locator("#newIncidentModal").evaluate("dialog => dialog.close()")

    # Die Bottom-Nav ist am Board nur bis 760px verborgen; bei 800px ist sie
    # die mobile Navigation, waehrend alle Lanes zur gezielten FAB-Pruefung
    # sichtbar bleiben.
    page.set_viewport_size({"width": 800, "height": 844})
    _board(page, base_url)
    task_column = page.locator('.kanban-col:has([title="Auftrag anlegen"])').first
    vehicle_column = page.locator('.kanban-col:has([title="Einheit zum Einsatz hinzufügen"])').first
    expect(task_column).to_be_visible()
    expect(vehicle_column).to_be_visible()
    task_column.evaluate("el => { document.querySelectorAll('.kanban-col').forEach(col => col.removeAttribute('data-lane-active')); el.setAttribute('data-lane-active', ''); }")
    page.locator(".bottom-nav__fab").click()
    expect(page.locator("#quickAddTaskDialog")).to_have_js_property("open", True)
    page.locator("#quickAddTaskDialog").evaluate("dialog => dialog.close()")
    vehicle_column.evaluate("el => { document.querySelectorAll('.kanban-col').forEach(col => col.removeAttribute('data-lane-active')); el.setAttribute('data-lane-active', ''); }")
    page.locator(".bottom-nav__fab").click()
    expect(page.locator("#vehicleWizard")).to_be_visible()


def test_fahrzeug_zuordnung_und_loesen_aktualisiert_beide_sessions(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    """DnD refreshes the vehicle card and the old column in every WS session."""
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)

    # Don't assume an earlier test already added a unit to this incident (this
    # test must also pass in isolation) — add one via the wizard, same as
    # test_wizard_formulare_zeigen_neue_karten_ohne_reload below.
    first.get_by_title("Einheit zum Einsatz hinzufügen").first.click()
    suggestion = first.locator("#vehicleWizard .suggestion-pill--block:not([disabled])").first
    expect(suggestion).to_be_visible(timeout=10_000)
    vehicle_code = suggestion.locator("strong").inner_text()
    suggestion.click()
    first.locator("#vehicleWizard form").first.get_by_role("button", name="Einheit hinzufügen").click()
    vehicle = first.locator('.card[data-kind="vehicle"]', has_text=vehicle_code).last
    expect(vehicle).to_be_visible(timeout=10_000)
    vehicle_id = vehicle.get_attribute("data-uid")
    assert vehicle_id

    first_load_id, second_load_id = _load_id(first), _load_id(second)

    # The vehicle's own quick-add flows must refresh its nested checklist in
    # both websocket sessions (not merely the board lane).
    vehicle_task = "E2E Fahrzeugauftrag " + uuid4().hex[:8]
    own_vehicle = first.locator(f"#vehicle-card-{vehicle_id}")
    other_vehicle = second.locator(f"#vehicle-card-{vehicle_id}")
    own_vehicle.get_by_role("button", name="+ Auftrag").click()
    first.locator("#quickAddTaskTitle").fill(vehicle_task)
    first.locator("#quickAddTaskDialog").get_by_role("button", name="Anlegen").click()
    for card in (own_vehicle, other_vehicle):
        expect(card.locator(".assigned-task", has_text=vehicle_task)).to_be_visible(timeout=10_000)

    vehicle_message = "E2E Fahrzeugmeldung " + uuid4().hex[:8]
    own_vehicle.get_by_role("button", name="Fahrzeugaktionen").click()
    own_vehicle.get_by_role("menuitem", name="+ Meldung").click()
    first.locator("#quickAddMsgTitle").fill(vehicle_message)
    first.locator("#quickAddMsgDialog").get_by_role("button", name="Anlegen").click()
    for card in (own_vehicle, other_vehicle):
        expect(card.locator(".assigned-msg", has_text=vehicle_message)).to_be_visible(timeout=10_000)

    # Native SortableJS drag isn't reliably simulatable via Playwright's mouse API
    # (no other test in this suite attempts it either) — exercise the same
    # form-urlencoded endpoint sortable-glue.js's onEnd() POSTs to instead, exactly
    # like the task/message coverage below. A person must vanish from the rescued
    # column and appear in the other browser's vehicle mini-zone without reload.
    person_name = "E2E DnD Person " + uuid4().hex[:8]
    first.get_by_title("Gerettete Person erfassen").first.click()
    first.locator("#personWizard input[x-model=quickName]").first.fill(person_name)
    first.locator("#personWizard").get_by_role("button", name="Speichern").click()
    person = first.locator('.card[data-kind="person"]', has_text=person_name)
    expect(person).to_be_visible(timeout=10_000)
    person_uid = person.get_attribute("data-uid")
    person_column_id = person.locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' kanban-col ')]"
    ).get_attribute("data-col-id")
    assert person_uid and person_column_id

    # Reproduce Sortable's former optimistic invalid drop: it moved the DOM
    # node into a non-person lane although the server has no such person state.
    # Both sessions must end with precisely one card in the rescued column.
    invalid_zone = first.locator(
        '.kanban-col:has([title="Auftrag anlegen"]) .kanban-col__body.sortable-zone'
    ).first
    invalid_column_id = invalid_zone.evaluate(
        "el => el.closest('.kanban-col').dataset.colId"
    )
    assert invalid_column_id
    first.evaluate(
        "({personId, zoneId}) => document.getElementById(zoneId).appendChild(document.getElementById(personId))",
        {"personId": f"person-card-{person_uid}", "zoneId": f"zone-{invalid_column_id}"},
    )
    _move_card(first, "person", person_uid, column_id=invalid_column_id)
    for page in (first, second):
        expect(page.locator(f"#zone-{person_column_id} .card[data-kind='person']", has_text=person_name)).to_have_count(1, timeout=10_000)
        expect(page.locator(f"#zone-{invalid_column_id} .card[data-kind='person']", has_text=person_name)).to_have_count(0, timeout=10_000)

    _move_card(first, "person", person_uid, vehicle_id=vehicle_id)

    expect(own_vehicle.locator(".assigned-person", has_text=person_name)).to_be_visible(timeout=10_000)
    expect(other_vehicle.locator(".assigned-person", has_text=person_name)).to_be_visible(timeout=10_000)
    expect(second.locator(f"#zone-{person_column_id}").locator(".card", has_text=person_name)).not_to_be_visible()
    assert _load_id(second) == second_load_id

    # Detach direction: person must reappear in "Gerettete Personen" for the other
    # session and vanish from the vehicle card, without duplicating anywhere.
    _move_card(
        first, "person", person_uid, column_id=person_column_id,
        detach_vehicle="true", source_vehicle_id=vehicle_id,
    )
    expect(own_vehicle.locator(".assigned-person", has_text=person_name)).not_to_be_visible(timeout=10_000)
    expect(other_vehicle.locator(".assigned-person", has_text=person_name)).not_to_be_visible(timeout=10_000)
    expect(second.locator(f"#zone-{person_column_id}").locator(".card", has_text=person_name)).to_be_visible(
        timeout=10_000
    )

    # Endpoint-level regression coverage for task/message assignment and detach:
    # assert the actual source-column and vehicle-card fragments, not just DB state.
    # Don't assume an earlier test seeded a message on this incident (this test
    # must also pass in isolation, and seed_board_ci.py only seeds a task).
    message_marker = "E2E DnD Meldung " + uuid4().hex[:8]
    _mutation(first, message_marker)
    expect(first.locator('.card[data-kind="message"]', has_text=message_marker)).to_be_visible(timeout=10_000)

    for kind in ("task", "message"):
        card = first.locator(f'.card[data-kind="{kind}"]').first
        uid = card.get_attribute("data-uid")
        title = card.locator(".card__title").inner_text()
        column_id = card.locator(
            "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' kanban-col ')]"
        ).get_attribute("data-col-id")
        assert uid and column_id
        _move_card(first, kind, uid, vehicle_id=vehicle_id)
        assigned_class = f".assigned-{'task' if kind == 'task' else 'msg'}"
        expect(own_vehicle.locator(assigned_class, has_text=title)).to_be_visible(timeout=10_000)
        expect(other_vehicle.locator(assigned_class, has_text=title)).to_be_visible(timeout=10_000)
        source_html = first.evaluate(
            "url => fetch(url).then(response => response.text())",
            f"/einsatz/{INCIDENT_ID}/spalte/{column_id}/inhalt",
        )
        vehicle_html = first.evaluate(
            "url => fetch(url).then(response => response.text())",
            f"/einsatz/{INCIDENT_ID}/karte/vehicle/{vehicle_id}",
        )
        card_id = f"{'msg' if kind == 'message' else kind}-card-{uid}"
        assert f'id="{card_id}"' not in source_html
        assert title in vehicle_html

        _move_card(
            first, kind, uid, column_id=column_id, detach_vehicle="true", source_vehicle_id=vehicle_id
        )
        expect(own_vehicle.locator(assigned_class, has_text=title)).not_to_be_visible(timeout=10_000)
        expect(other_vehicle.locator(assigned_class, has_text=title)).not_to_be_visible(timeout=10_000)
        source_html = first.evaluate(
            "url => fetch(url).then(response => response.text())",
            f"/einsatz/{INCIDENT_ID}/spalte/{column_id}/inhalt",
        )
        vehicle_html = first.evaluate(
            "url => fetch(url).then(response => response.text())",
            f"/einsatz/{INCIDENT_ID}/karte/vehicle/{vehicle_id}",
        )
        assert f'id="{card_id}"' in source_html
        assert f'id="{card_id}"' not in vehicle_html

    assert _load_id(first) == first_load_id
    assert _load_id(second) == second_load_id


def test_wizard_formulare_zeigen_neue_karten_ohne_reload(angemeldete_seite: Page, base_url: str) -> None:
    """Einheit und Person kommen per HTMX sofort in ihre Lane, ohne Navigation."""
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)
    loads: list[float] = []
    page.on("load", lambda _: loads.append(time.monotonic()))

    page.get_by_title("Einheit zum Einsatz hinzufügen").first.click()
    suggestion = page.locator("#vehicleWizard .suggestion-pill--block:not([disabled])").first
    expect(suggestion).to_be_visible(timeout=10_000)
    vehicle_code = suggestion.locator("strong").inner_text()
    suggestion.click()
    page.locator("#vehicleWizard form").first.get_by_role("button", name="Einheit hinzufügen").click()
    expect(page.locator(".card", has_text=vehicle_code)).to_be_visible(timeout=10_000)

    person_name = "E2E Person " + uuid4().hex[:8]
    page.get_by_title("Gerettete Person erfassen").first.click()
    page.locator("#personWizard input[x-model=quickName]").first.fill(person_name)
    page.locator("#personWizard").get_by_role("button", name="Speichern").click()
    expect(page.locator(".card", has_text=person_name)).to_be_visible(timeout=10_000)

    assert _load_id(page) == load_id
    assert loads == []


def test_neue_fahrzeugkarte_weist_auftrag_und_meldung_ohne_reload_zu(
    angemeldete_seite: Page, base_url: str
) -> None:
    """Die OOB-Auswahlen enthalten ein eben per Wizard angelegtes Fahrzeug sofort."""
    page = angemeldete_seite
    _board(page, base_url)
    load_id = _load_id(page)

    page.get_by_title("Einheit zum Einsatz hinzufügen").first.click()
    suggestion = page.locator("#vehicleWizard .suggestion-pill--block:not([disabled])").first
    expect(suggestion).to_be_visible(timeout=10_000)
    vehicle_code = suggestion.locator("strong").inner_text()
    suggestion.click()
    page.locator("#vehicleWizard form").first.get_by_role("button", name="Einheit hinzufügen").click()
    vehicle = page.locator('.card[data-kind="vehicle"]', has_text=vehicle_code).last
    expect(vehicle).to_be_visible(timeout=10_000)

    task_title = "E2E Fahrzeugauftrag " + uuid4().hex
    vehicle.get_by_role("button", name="+ Auftrag").click()
    page.locator("#quickAddTaskTitle").fill(task_title)
    page.locator("#quickAddTaskDialog").get_by_role("button", name="Anlegen").click()
    task = page.locator('.card[data-kind="task"]', has_text=task_title)
    expect(task).to_be_visible(timeout=10_000)
    expect(task).to_contain_text(vehicle_code)

    message_title = "E2E Fahrzeugmeldung " + uuid4().hex
    vehicle.get_by_role("button", name="Fahrzeugaktionen").click()
    page.get_by_role("menuitem", name="+ Meldung").click()
    page.locator("#quickAddMsgTitle").fill(message_title)
    page.locator("#quickAddMsgDialog").get_by_role("button", name="Anlegen").click()
    message = page.locator('.card[data-kind="message"]', has_text=message_title)
    expect(message).to_be_visible(timeout=10_000)
    expect(message).to_contain_text(vehicle_code)

    assert _load_id(page) == load_id


def test_gleiche_karte_mit_entwurf_zeigt_aktualisieren_hinweis(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    """Ein Fremd-Update derselben Karte darf einen laufenden Entwurf nicht ersetzen."""
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)
    task = first.locator('.card[data-kind="task"]').first
    task_id = task.get_attribute("data-uid")
    assert task_id
    task.locator(".card__title").click()
    draft = "e2e-same-card-draft-" + uuid4().hex
    draft_field = first.locator("#taskEditForm textarea[name=detail]")
    draft_field.fill(draft)

    status_chip = second.locator(
        f"#task-card-{task_id} button[aria-label^='Auftragsstatus:']"
    )
    status_chip.click()
    second.locator(f"#task-card-{task_id}").get_by_role(
        "menuitem", name="Status auf In Arbeit setzen"
    ).click()
    expect(first.locator("#cardDetailRefreshNotice")).to_be_visible(timeout=10_000)
    expect(draft_field).to_have_value(draft)
    first.locator("#cardDetailRefreshNotice").get_by_role("button", name="Aktualisieren").click()
    expect(first.locator("#cardDetailBody select[name=status]").first).to_have_value("in_progress")


@pytest.mark.slow
def test_langzeit_broadcasts_ohne_deutliches_ressourcenwachstum(angemeldete_seite: Page, base_url: str) -> None:
    """180 s Last; erlaubt 25 MiB bzw. 50 % Heap-Jitter und 10 interne Timer.

    Die kombinierte absolute/relative Heap-Schwelle toleriert Chromiums GC-Zyklen,
    schlaegt aber bei linearem Wachstum durch liegenbleibende Fragmente klar fehl.
    """
    page = angemeldete_seite
    page.add_init_script("""
      window.__e2eSockets = 0; window.__e2eTimers = new Set();
      const WS = window.WebSocket; window.WebSocket = function(...a) {
        const ws = new WS(...a); window.__e2eSockets++;
        ws.addEventListener('close', () => window.__e2eSockets--); return ws;
      }; window.WebSocket.prototype = WS.prototype;
      const si = window.setInterval, ci = window.clearInterval;
      window.setInterval = (...a) => { const id=si(...a); window.__e2eTimers.add(id); return id; };
      window.clearInterval = id => { window.__e2eTimers.delete(id); return ci(id); };
    """)
    _board(page, base_url)
    session = page.context.new_cdp_session(page)
    session.send("Performance.enable")

    def metrics() -> tuple[int, int, int]:
        values = {m["name"]: m["value"] for m in session.send("Performance.getMetrics")["metrics"]}
        state = page.evaluate("({s:window.__e2eSockets,t:window.__e2eTimers.size})")
        return int(values["JSHeapUsedSize"]), state["s"], state["t"]

    start = metrics()
    duration = int(os.environ.get("E2E_MEMORY_SECONDS", "180"))
    until = time.monotonic() + duration
    while time.monotonic() < until:
        _publish({"type": "lis_sync"})
        page.wait_for_timeout(250)
    session.send("HeapProfiler.collectGarbage")
    end = metrics()
    assert end[0] - start[0] < max(25 * 1024 * 1024, start[0] // 2)
    assert end[1] <= start[1] + 1
    assert end[2] <= start[2] + 10
