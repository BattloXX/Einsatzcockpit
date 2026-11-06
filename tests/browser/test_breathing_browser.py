"""Echter Browser-Pruefstand fuer das Atemschutz-Board (optional).

Laeuft nur mit ECP_BROWSER_TESTS=1 und einem Chromium (ECP_CHROMIUM=/pfad/zu/chrome).
Die Seite wird ueber den TestClient gerendert; Anfragen an /static und die Atemschutz-
Routen werden im Browser an den TestClient weitergereicht. Screenshots landen in
ECP_SCREENSHOT_DIR (Standard: tests/browser/out).
"""
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_breathing_routes import breathing_routes  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("ECP_BROWSER_TESTS") != "1" or not os.environ.get("ECP_CHROMIUM"),
    reason="Browser-Tests nur mit ECP_BROWSER_TESTS=1 und ECP_CHROMIUM",
)
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("ECP_SCREENSHOT_DIR", ROOT / "tests/browser/out"))
VIEWPORTS = {"klein": (360, 800), "768": (768, 1024), "1024": (1024, 768), "handy": (390, 844), "tablet_hoch": (800, 1280), "tablet_quer": (1280, 800), "desktop": (1600, 900)}


def _seed(ctx):
    from app.models.breathing import BreathingTroop
    from app.services.breathing_service import create_troop, start_troop
    now = datetime.now(UTC).replace(tzinfo=None)
    ctx.troop.status = "im_einsatz"
    ctx.troop.planned_duration_min = 30
    ctx.troop.entry_at = now - timedelta(minutes=11)
    for member, press in zip(ctx.troop.active_members, (210, 190)):
        member.current_press, member.withdraw_press = press, 60
    ctx.troop.start_press_avg, ctx.troop.withdraw_press_calc = 295, 60
    second = create_troop(ctx.db, ctx.incident_id, "Trupp B", [
        {"free_text_name": "Person C", "role": "truppfuehrer", "start_press": 300},
        {"free_text_name": "Person D", "role": "truppmann", "start_press": 300},
    ], is_sicherheitstrupp=True)
    third = create_troop(ctx.db, ctx.incident_id, "Trupp C", [
        {"free_text_name": "Person E", "role": "truppfuehrer", "start_press": 280},
        {"free_text_name": "Person F", "role": "truppmann", "start_press": 290},
    ])
    third.status, third.entry_at, third.planned_duration_min = "rueckzug", now - timedelta(minutes=26), 30
    ctx.db.commit()
    return second, third


def _session(playwright, ctx, viewport):
    browser = playwright.chromium.launch(executable_path=os.environ["ECP_CHROMIUM"], args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    messages = []
    page.on("console", lambda m: messages.append((m.type, m.text)))
    page.on("pageerror", lambda e: messages.append(("pageerror", str(e))))

    def handle(route):
        url = route.request.url
        path = re.sub(r"^https?://[^/]+", "", url)
        if path.startswith("/ws/"):
            return route.abort()
        method = route.request.method
        if method == "GET":
            response = ctx.client.get(path, headers=route.request.headers)
        else:
            response = ctx.client.post(path, content=route.request.post_data_buffer or b"",
                                       # Redirects ebenfalls im TestClient verfolgen: Playwright leitet
                                       # Folgeanfragen eines erfuellten Redirects nicht erneut an diese Route.
                                       headers=route.request.headers, follow_redirects=True)
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() in ("content-type", "cache-control", "location", "hx-trigger")})

    page.route("http://localhost/**", handle)
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    return browser, page, messages


def _open_board(page, ctx):
    page.goto(f"http://localhost/einsatz/{ctx.incident_id}/atemschutz", wait_until="networkidle")
    page.wait_for_selector(".troop-card", timeout=15000)


@pytest.mark.parametrize("name", list(VIEWPORTS))
def test_board_screenshots_and_no_js_errors(breathing_routes, name):  # noqa: F811
    from playwright.sync_api import sync_playwright
    ctx = breathing_routes
    _seed(ctx)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS[name])
        try:
            ctx.client.cookies  # Cookies des angemeldeten TestClients an den Browser uebergeben
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            _open_board(page, ctx)
            page.screenshot(path=str(OUT / f"board_{name}.png"), full_page=True)
            assert [t for t in messages if t[0] == "pageerror"] == []
            cards = page.locator(".troop-card")
            assert cards.count() == 3
            orders = [int(page.locator(".troop-card").nth(i).get_attribute("data-urgency")) for i in range(3)]
            positions = [cards.nth(i).bounding_box() for i in range(3)]
            visual_orders = [orders[i] for i in sorted(range(3), key=lambda i: (positions[i]["y"], positions[i]["x"]))]
            assert visual_orders == sorted(orders)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator('#breathingContrastToggle').click()
            assert page.locator('#breathingBoard').evaluate("el => el.classList.contains('breathing-hc')")
            page.screenshot(path=str(OUT / f"board_hc_{name}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            bar = page.locator('#breathingAlertBar')
            before = bar.locator('[data-alert-text]').inner_text()
            bar.locator('[data-alert-next]').click()
            assert bar.locator('[data-alert-text]').inner_text() != before
            page.wait_for_timeout(600)
            assert page.locator('.troop-card--highlight').count() == 1
            assert bar.bounding_box()["y"] >= page.locator('#topnav').bounding_box()["height"] - 1
            if name == "desktop":
                page.locator('.btn--druck').first.click()
                popup = page.locator('.press-picker-popup:visible').first
                assert popup.is_visible()
                assert popup.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(255, 255, 255)"
                page.screenshot(path=str(OUT / "board_hc_druck.png"), full_page=True)
        finally:
            browser.close()


def test_card_swap_reinitializes_without_duplicate_timers(breathing_routes):  # noqa: F811
    from playwright.sync_api import sync_playwright
    ctx = breathing_routes
    _seed(ctx)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS["desktop"])
        try:
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            _open_board(page, ctx)
            page.evaluate("""() => {
              window.__intervals = 0;
              const original = window.setInterval;
              window.setInterval = function () { window.__intervals++; return original.apply(this, arguments); };
            }""")
            for _ in range(5):
                page.evaluate(f"htmx.ajax('GET', '/einsatz/{ctx.incident_id}/atemschutz/{ctx.troop.id}/karte',"
                              " {target: '#troop-%d', swap: 'outerHTML'})" % ctx.troop.id)
                page.wait_for_timeout(400)
            assert page.locator(f"#troop-{ctx.troop.id}").count() == 1
            created = page.evaluate("window.__intervals")
            # Jede Karte darf je Swap hoechstens EIN neues Intervall anlegen (kein Anwachsen).
            assert created <= 5 * 2, created
            assert [t for t in messages if t[0] == "pageerror"] == []
        finally:
            browser.close()


@pytest.mark.parametrize("name", ["handy", "tablet_hoch", "desktop"])
def test_dialog_screenshot_and_creation(breathing_routes, name):  # noqa: F811
    from playwright.sync_api import sync_playwright
    ctx = breathing_routes
    _seed(ctx)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS[name])
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
        try:
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            _open_board(page, ctx)
            page.get_by_role("button", name="+ Trupp anlegen", exact=True).click()
            dialog = page.locator("#newTroopModal")
            assert dialog.is_visible()
            assert dialog.locator(".bt-dialog-member").count() == 3
            page.screenshot(path=str(OUT / f"dialog_{name}.png"))
            dialog.locator(".bt-dialog-member").first.scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f"dialog_besatzung_{name}.png"))
            footer = dialog.locator(".bt-dialog-footer").bounding_box()
            assert footer["y"] + footer["height"] <= VIEWPORTS[name][1] + 1
            assert dialog.locator('.bt-dialog-footer button[type=submit]').bounding_box()["height"] >= 56
            for index, person in enumerate(["Browser Person Eins", "Browser Person Zwei"]):
                row = dialog.locator(".bt-dialog-member").nth(index)
                row.locator(".person-select__trigger").click()
                flyout = dialog.locator(".person-flyout:visible")
                flyout.wait_for(state="visible")
                flyout.get_by_role("button", name="Freie Eingabe (nicht im Register)", exact=True).click()
                row.locator('input[type=text]').fill(person)
            dialog.locator(".bt-dialog-member").nth(0).get_by_role("button", name="300", exact=True).click()
            dialog.locator('.bt-dialog-footer button[type=submit]').click()
            page.wait_for_timeout(100)
            assert [t for t in messages if t[0] == "pageerror"] == []
            dialog.locator(".bt-dialog-errors").wait_for(state="visible")
            assert "Mitglied 2" in dialog.locator(".bt-dialog-errors").inner_text()
            assert dialog.is_visible()
            assert dialogs == []
            dialog.locator(".bt-dialog-member").nth(1).get_by_role("button", name="290", exact=True).click()
            dialog.locator('.bt-dialog-footer button[type=submit]').click()
            page.wait_for_timeout(100)
            assert [t for t in messages if t[0] == "pageerror"] == []
            page.wait_for_selector(".troop-card:has-text('Browser Person Eins')")
            assert not dialog.is_visible()
            assert page.locator(".troop-card").count() == 4
            assert [t for t in messages if t[0] == "pageerror"] == []
            assert dialogs == []
        finally:
            browser.close()


@pytest.mark.parametrize("name", ["handy", "desktop"])
def test_dashboard_kachel_screenshot_and_live_refresh(breathing_routes, name):  # noqa: F811
    """EL-Kachel auf dem Dashboard, inklusive WS-Swap und Serverzeit-Ticker."""
    from playwright.sync_api import sync_playwright
    ctx = breathing_routes
    _seed(ctx)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS[name])
        try:
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            # Die TestClient-Bruecke transportiert HTTP; WS-Ereignisse gezielt einspeisen.
            page.add_init_script("""window.WebSocket = class {
              constructor() { this.readyState = 1; setTimeout(() => this.onopen && this.onopen(), 0); }
              send() {}
            };""")
            page.goto(f"http://localhost/einsatz/{ctx.incident_id}/dashboard", wait_until="networkidle")
            page.wait_for_selector('#breathingKachel')
            assert page.locator('.asuew-kachel__troop').count() == 2
            assert page.locator('#breathingKachel').evaluate("el => el.scrollWidth <= el.clientWidth")
            timer = page.locator('.asuew-kachel__time').first
            before = timer.inner_text()
            page.evaluate('ECPServerClock.setOffsetMs(120000)')
            page.wait_for_timeout(1100)
            assert timer.inner_text() != before
            page.screenshot(path=str(OUT / f"dashboard_{name}.png"), full_page=True)
            ctx.troop.active_members[0].current_press = 50
            ctx.db.commit()
            page.evaluate("ws.onmessage({data: JSON.stringify({type: 'pressure_logged'})})")
            page.wait_for_function("document.querySelector('#breathingKachel').textContent.includes('50 bar')")
            assert page.locator('.asuew-kachel__pressure.asuew-kachel--rot').count() >= 1
            page.evaluate("ws.onmessage({data: JSON.stringify({type: 'troop_updated'})})")
            page.wait_for_timeout(600)
            assert page.locator('#breathingKachel').count() == 1
            assert [t for t in messages if t[0] == "pageerror"] == []
            # Auch die Einsatzseite aus dem Aufgabenaufruf dokumentieren.
            page.goto(f"http://localhost/einsatz/{ctx.incident_id}", wait_until="networkidle")
            assert page.locator('body').is_visible()
            page.screenshot(path=str(OUT / f"einsatz_{name}.png"), full_page=True)
        finally:
            browser.close()


@pytest.mark.parametrize("name", ["handy", "desktop"])
def test_assp_screenshot_and_live_pool(breathing_routes, name):  # noqa: F811
    """ASSP bei 390/1600px, Flaschen ohne Reload, WS-Swap und Erholzeit."""
    from playwright.sync_api import sync_playwright

    from app.models.breathing import BreathingTroop
    ctx = breathing_routes
    _seed(ctx)
    ctx.db.add(BreathingTroop(incident_id=ctx.incident_id, name="Erholung Browser", status="zurueck",
                             back_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=20)))
    ctx.db.commit()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS[name])
        try:
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            page.add_init_script("""window.WebSocket = class {
              constructor(url) { if (url.includes('/ws/incident/') && !window.asspSocket) window.asspSocket = this; this.readyState = 1;
                setTimeout(() => this.onopen && this.onopen(), 0); }
              send() {}
              close() {}
              addEventListener() {}
              removeEventListener() {}
            };""")
            page.goto(f"http://localhost/einsatz/{ctx.incident_id}/atemschutz/assp", wait_until="networkidle")
            page.wait_for_selector('#asspContent')
            assert [t for t in messages if t[0] == 'pageerror'] == []
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert page.locator('.assp-delta').first.bounding_box()['height'] >= 56
            assert page.locator('.assp-delta').first.bounding_box()['width'] >= 56
            page.get_by_role('button', name='Volle Flaschen erhöhen', exact=True).click()
            page.wait_for_function("document.getElementById('assp-voll').textContent === '1'")
            assert page.locator('#asspContent').count() == 1
            page.locator('#breathingContrastToggle').click()
            assert page.locator('#breathingBoard').evaluate("el => el.classList.contains('breathing-hc')")
            # Das geschlossene Profil-Sheet liegt per Transform unter dem Viewport;
            # Chromium bildet es sonst in einem Full-Page-Screenshot trotzdem ab.
            page.screenshot(path=str(OUT / f'assp_{name}.png'), full_page=True,
                            style='.profil-sheet:not(.profil-sheet--open) { visibility: hidden; }')
            timer = page.locator('.assp-recovery').first
            before = timer.inner_text()
            page.evaluate('ECPServerClock.setOffsetMs(3600000)')
            page.wait_for_function("document.querySelector('.assp-recovery').textContent === 'einsatzbereit'")
            assert before != timer.inner_text()
            page.get_by_role('button', name='ASSP eingerichtet', exact=True).click()
            page.wait_for_function("document.getElementById('asspContent').textContent.includes('eingerichtet seit')")
            # Ereignis eines anderen Clients: keinen Druck/Alarm in die Logistik holen.
            ctx.troop.name = 'WS Trupp aktualisiert'
            ctx.db.commit()
            page.evaluate("asspSocket.onmessage({data: JSON.stringify({type: 'troop_status_changed'})})")
            page.wait_for_function(
                "document.getElementById('asspContent').textContent.includes('WS Trupp aktualisiert')"
            )
            assert page.locator('#breathingBoard').evaluate("el => el.classList.contains('breathing-hc')")
            assert [t for t in messages if t[0] == 'pageerror'] == []
        finally:
            browser.close()


@pytest.mark.parametrize("name", ["handy", "desktop"])
def test_protokoll_screenshot(breathing_routes, name):  # noqa: F811
    """Protokollseite mit Druckmeldungen und Kontrolle, ohne JS-Fehler und ohne Ueberbreite."""
    from playwright.sync_api import sync_playwright

    from app.models.breathing import BreathingKontrolle, PressureLog
    ctx = breathing_routes
    _seed(ctx)
    now = datetime.now(UTC).replace(tzinfo=None)
    member = ctx.troop.active_members[0]
    ctx.db.add_all([
        PressureLog(troop_id=ctx.troop.id, troop_member_id=member.id, pressure_bar=230, ts=now - timedelta(minutes=8)),
        PressureLog(troop_id=ctx.troop.id, troop_member_id=member.id, pressure_bar=210, ts=now - timedelta(minutes=2),
                    note="Einsatzziel erreicht", nachgetragen=True),
        BreathingKontrolle(troop_id=ctx.troop.id, stufe="one_third", art="funk", erledigt_at=now - timedelta(minutes=1),
                           niedrigster_druck=205),
    ])
    ctx.db.commit()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser, page, messages = _session(playwright, ctx, VIEWPORTS[name])
        try:
            page.context.add_cookies([
                {"name": key, "value": value, "url": "http://localhost"} for key, value in ctx.client.cookies.items()
            ])
            page.goto(f"http://localhost/einsatz/{ctx.incident_id}/atemschutz/{ctx.troop.id}/protokoll",
                      wait_until="networkidle")
            assert [t for t in messages if t[0] == "pageerror"] == []
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(OUT / f"protokoll_{name}.png"), full_page=True,
                            style=".profil-sheet:not(.profil-sheet--open) { visibility: hidden; }")
        finally:
            browser.close()
