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


def _load_id(page: Page) -> str | None:
    try:
        return page.evaluate("window.__ecLoadId")
    except Exception:
        return None


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


def test_zwei_sessions_aendern_parallel(
    angemeldete_seite: Page, zweiter_kontext: BrowserContext, base_url: str
) -> None:
    first, second = angemeldete_seite, zweiter_kontext.pages[0]
    _board(first, base_url)
    _board(second, base_url)
    one, two = "e2e-a-" + uuid4().hex, "e2e-b-" + uuid4().hex
    _mutation(first, one)
    _mutation(second, two)
    expect(first.locator(".card", has_text=two)).to_be_visible(timeout=10_000)
    expect(second.locator(".card", has_text=one)).to_be_visible(timeout=10_000)


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
