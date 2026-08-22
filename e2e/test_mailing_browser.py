"""Real-browser verification for the Docker/MariaDB Mailing module.

Run explicitly (this directory intentionally sits outside ``tests/`` so the
SQLite fixtures in ``tests/conftest.py`` are never imported):

    PLAYWRIGHT_BROWSERS_PATH=/tmp/einsatzcockpit-playwright \
      .venv/bin/pytest e2e/test_mailing_browser.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8092")
ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = Path(__file__).with_name("screenshots")


def _env(name: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise RuntimeError(f"{name} fehlt in .env")


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _download(page: Page, url: str, target: Path) -> bytes:
    with page.expect_download() as download_info:
        page.evaluate(
            "url => { const a=document.createElement('a'); a.href=url; "
            "a.download=''; document.body.appendChild(a); a.click(); a.remove(); }",
            url,
        )
    download = download_info.value
    download.save_as(target)
    content = target.read_bytes()
    assert content
    return content


def test_mailing_end_to_end(page: Page, tmp_path: Path) -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.set_viewport_size({"width": 1440, "height": 1000})

    # Login and prove the navigation is hidden while both feature flags are off.
    page.goto(BASE_URL + "/login")
    page.locator("#username").fill(_env("BOOTSTRAP_ADMIN_USER"))
    page.locator("#password").fill(_env("BOOTSTRAP_ADMIN_PASSWORD"))
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(page.locator('a[href="/mailing/"]')).to_have_count(0)

    # Two-tier feature flag: system first, then the bootstrap admin's own org.
    page.goto(BASE_URL + "/admin/settings")
    system_toggle = page.locator('form[action="/admin/settings/system/mailing-toggle"] input[name="enabled_raw"]')
    expect(system_toggle).not_to_be_checked()
    system_toggle.check()
    page.wait_for_url("**/admin/settings?saved=1")
    org_toggle = page.locator('input[name="mailing_module_enabled_raw"]')
    expect(org_toggle).to_be_enabled()
    org_toggle.check()
    org_form = org_toggle.locator("xpath=ancestor::form[1]")
    org_form.get_by_role("button", name="Speichern").click()
    page.wait_for_url("**/admin/settings?saved=1**")
    page.goto(BASE_URL + "/?org=1")
    expect(page.locator('a[href="/mailing/"]')).to_be_visible()

    # Dashboard: all KPI tiles and all three Chart.js instances must exist.
    page.goto(BASE_URL + "/mailing/dashboard")
    expect(page.get_by_role("heading", name="Mailing-Dashboard")).to_be_visible()
    expect(page.locator(".mailing-kpis .kpi-card")).to_have_count(6)
    expect(page.locator("canvas")).to_have_count(3)
    assert page.evaluate(
        "() => ['campaignRates','failureBreakdown','sendsOverTime'].every(id => "
        "Chart.getChart(document.getElementById(id)) && "
        "document.getElementById(id).getBoundingClientRect().width > 0)"
    )
    _screenshot(page, "dashboard.png")

    # Template editor: click-to-insert chip and debounced HTMX preview.
    page.goto(BASE_URL + "/mailing/templates/new")
    page.locator('input[name="name"]').fill("E2E Begrüßung")
    page.locator('input[name="subject"]').fill("Willkommen {{ vorname }}")
    html_editor = page.locator("#mailing-body-html")
    html_editor.fill("<p>Hallo </p>")
    html_editor.focus()
    html_editor.press("End")
    page.locator('.mailing-variable[data-variable="vorname"]').click()
    assert "{{ vorname }}" in html_editor.input_value()
    expect(page.locator("#preview")).to_contain_text("Max", timeout=5_000)
    _screenshot(page, "template-editor-preview.png")
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_url("**/mailing/templates")
    expect(page.get_by_text("E2E Begrüßung", exact=True)).to_be_visible()

    # Static list plus a 30-row real multipart CSV upload (enables pagination checks).
    page.goto(BASE_URL + "/mailing/lists/new")
    page.locator('input[name="name"]').fill("E2E Statisch")
    page.get_by_role("button", name="Anlegen").click()
    page.wait_for_url("**/mailing/lists")
    static_card = page.locator(".mailing-list-card", has_text="E2E Statisch")
    static_id = int(static_card.get_attribute("hx-get").rstrip("/").split("/")[-1])
    static_card.click()
    csv_file = tmp_path / "recipients.csv"
    csv_file.write_text(
        "email;display_name\n"
        + "".join(f"recipient{i:02d}@example.test;Test Person {i:02d}\n" for i in range(30)),
        encoding="utf-8",
    )
    page.locator('#mailing-list-detail input[type="file"]').set_input_files(csv_file)
    page.locator('#mailing-list-detail button', has_text="Importieren").click()
    page.wait_for_url("**/mailing/lists")
    static_card = page.locator(".mailing-list-card", has_text="E2E Statisch")
    static_card.click()
    expect(page.locator("#mailing-list-detail")).to_contain_text("30")
    expect(page.locator("#mailing-list-detail .mailing-pagination")).to_be_visible()

    # Dynamic filter builder and human-readable summary.
    page.goto(BASE_URL + "/mailing/lists/new")
    page.locator('input[name="name"]').fill("E2E Dynamisch")
    page.locator('input[name="kind"][value="dynamic"]').check()
    page.locator('select[name="active"]').select_option("true")
    page.get_by_role("button", name="Anlegen").click()
    page.wait_for_url("**/mailing/lists")
    dynamic_card = page.locator(".mailing-list-card", has_text="E2E Dynamisch")
    dynamic_card.click()
    expect(page.locator("#mailing-list-detail")).to_contain_text("Aktiv")
    _screenshot(page, "recipient-split-view.png")

    # Configure a deliberately invalid Resend key plus a valid-format local webhook secret.
    page.goto(BASE_URL + "/mailing/settings")
    expect(page.locator('input[name="resend_webhook_secret"]')).to_be_visible()
    webhook_url = page.locator('label:has-text("Webhook-URL") input').input_value()
    assert webhook_url.startswith(BASE_URL + "/mailing/webhook/resend/")
    page.locator('input[name="enabled"]').check()
    page.locator('input[name="resend_api_key"]').fill("re_e2e_invalid_key")
    page.locator('input[name="resend_webhook_secret"]').fill(
        "whsec_RUVFX01BSUxJTkdfV0VCSE9PS19TRUNSRVRfMzI="
    )
    page.locator('input[name="from_addr"]').fill("mailing-e2e@example.test")
    page.locator('input[name="sender_display_name"]').fill("E2E Testsystem")
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_url("**/mailing/settings")
    _screenshot(page, "settings.png")

    # Campaign: template + list, draft, queue, immediate dispatch/failure, poll/search/page 2.
    page.goto(BASE_URL + "/mailing/campaigns/new")
    page.locator('select[name="template_id"]').select_option(label="E2E Begrüßung")
    page.get_by_text("E2E Statisch", exact=True).locator("input").check()
    page.locator('input[name="max_attempts_override"]').fill("1")
    page.get_by_role("button", name="Entwurf anlegen").click()
    page.wait_for_url("**/mailing/campaigns/*")
    campaign_url = page.url
    campaign_id = int(campaign_url.rstrip("/").split("/")[-1])
    expect(page.get_by_text("draft", exact=True)).to_be_visible()
    page.get_by_role("button", name="Jetzt anstoßen").click()
    page.wait_for_url(campaign_url)
    expect(page.locator("#queue-status")).to_contain_text("30 Einträge")
    expect(page.locator("#queue-status")).to_contain_text("Fehlgeschlagen")
    expect(page.locator("#queue-status .mailing-pagination")).to_be_visible()
    with page.expect_response(lambda response: f"/campaigns/{campaign_id}/status" in response.url):
        page.wait_for_timeout(5_500)
    search = page.locator('#queue-status input[name="q"]')
    search.fill("recipient29@example.test")
    expect(page.locator("#queue-status")).to_contain_text("recipient29@example.test", timeout=5_000)
    expect(page.locator("#queue-status")).not_to_contain_text("recipient00@example.test")
    search.fill("")
    expect(page.locator("#queue-status .mailing-pagination")).to_be_visible(timeout=5_000)
    page.locator("#queue-status .mailing-pagination a", has_text="2").click()
    expect(page.locator("#queue-status")).to_contain_text("recipient29@example.test")
    _screenshot(page, "campaign-detail.png")

    # Browser downloads: real CSV/PDF bytes, not an HTML error document.
    csv_bytes = _download(page, f"{BASE_URL}/mailing/lists/{static_id}/export.csv", tmp_path / "list.csv")
    assert b"recipient00@example.test" in csv_bytes and not csv_bytes.lstrip().startswith(b"<!doctype html")
    pdf_bytes = _download(page, f"{BASE_URL}/mailing/dashboard/report.pdf", tmp_path / "report.pdf")
    assert pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) > 1_000

    # Manual suppression lifecycle.
    page.goto(BASE_URL + "/mailing/suppression")
    page.locator('input[name="email"]').fill("manual-suppression@example.test")
    page.locator('input[name="note"]').fill("E2E")
    page.get_by_role("button", name="Sperren").click()
    expect(page.get_by_text("manual-suppression@example.test", exact=True)).to_be_visible()
    _screenshot(page, "suppression-list.png")
    page.get_by_role("button", name="Entfernen").click()
    expect(page.get_by_text("manual-suppression@example.test", exact=True)).to_have_count(0)

    assert not page_errors, f"JavaScript page errors: {page_errors}"
    assert not console_errors, f"Browser console errors: {console_errors}"
