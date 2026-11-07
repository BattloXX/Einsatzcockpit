"""S4-Smoke mit isolierter SQLite-DB; kein externer Server erforderlich."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def serve(directory: Path, port: int):
    os.environ.update(
        DATABASE_URL=f"sqlite:///{directory / 'agent.db'}",
        DEBUG="true",
        TESTING="true",
        SECRET_KEY="agent-browser-only-test-secret-key-32-zeichen",
        COOKIE_SECURE="false",
        REDIS_URL="",
        NACHSCHLAGEWERK_SYNC_ENABLED="false",
        MAIL_IMAP_POLL_ENABLED="false",
        MAIL_GRAPH_POLL_ENABLED="false",
    )
    import uvicorn
    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    from app.core.security import hash_password
    from app.core.tenant import set_tenant_context
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models.infoscreen_agent import InfoscreenSchedule
    from app.models.master import FireDept, OrgSettings
    from app.models.user import Role, User, UserRole
    from app.seed_data import _upsert_roles

    @compiles(BigInteger, "sqlite")
    def bigint(element, compiler, **kwargs):
        return "INTEGER"

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        _upsert_roles(db)
        db.add(Role(code="org_admin", label="Org-Admin"))
        org = FireDept(slug="agent-browser", name="Agent Browser Test")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id))
        user = User(
            username="agent-admin",
            display_name="Browser Admin",
            org_id=org.id,
            password_hash=hash_password("AgentBrowser123!"),
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="org_admin").one().id))
        db.add(InfoscreenSchedule(org_id=org.id, name="Browser-Plan", timezone="Europe/Vienna", ruhemodus="aus"))
        db.commit()
    uvicorn.run(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")


@pytest.fixture(scope="module")
def agent_browser():
    with sync_playwright() as playwright:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def agent_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("agent-browser")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    log = directory / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(directory), str(port)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 40
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(log.read_text())
                    try:
                        if client.get(base + "/login").status_code == 200:
                            break
                    except httpx.TransportError:
                        time.sleep(0.1)
                else:
                    pytest.fail(log.read_text())
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("width", [1280, 390])
def test_admin_dialog_polling_and_mobile(agent_browser, agent_server, width):
    page = agent_browser.new_page(viewport={"width": width, "height": 844})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(agent_server + "/login")
        page.locator("#username").fill("agent-admin")
        page.locator("#password").fill("AgentBrowser123!")
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url(lambda url: "/login" not in str(url))
        page.goto(agent_server + "/admin/infoscreen-agent")
        expect(page.get_by_role("heading", name="Geraete & Plaene")).to_be_visible()
        page.locator('form[hx-post$="/geraete/neu"] input[name="name"]').fill(f"Browser Halle {width} " + "W" * 60)
        page.get_by_role("button", name="Geraet hinzufuegen", exact=True).click()
        modal = page.locator("#agent-dialog")
        expect(modal).to_be_visible()
        expect(modal.get_by_text("Installer noch nicht freigeschaltet", exact=True)).to_be_visible()
        command = modal.locator("[data-agent-command]").input_value()
        assert "--pairing-code " in command
        modal.locator("[data-agent-env]").check()
        assert "EC_PAIRING_CODE=" in modal.locator("[data-agent-command]").input_value()
        page.evaluate(
            "Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => { window.agentCopied = text; }}})"
        )
        modal.get_by_role("button", name="Einzeiler kopieren", exact=True).click()
        expect(modal.locator("[data-agent-copy-status]")).to_have_text("Einzeiler kopiert.")
        assert page.evaluate("window.agentCopied") == modal.locator("[data-agent-command]").input_value()
        page.evaluate(
            "Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async () => { throw new Error('denied'); }}})"
        )
        modal.get_by_role("button", name="Einzeiler kopieren", exact=True).click()
        expect(modal.locator("[data-agent-copy-status]")).to_contain_text("Text markiert")
        assert modal.locator("[data-agent-command]").evaluate(
            "field => field.selectionEnd - field.selectionStart === field.value.length"
        )
        code = command.split("--pairing-code ")[1].split()[0]
        requests = []
        list_requests = []
        page.on(
            "request", lambda request: list_requests.append(request.url) if request.url.endswith("/geraete") else None
        )
        page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/status") else None)
        response = page.request.post(agent_server + "/api/v1/infoscreen-agent/pair", data={"code": code})
        assert response.status == 200, response.text()
        expect(modal.locator("#agent-pairing-status")).to_contain_text("gekoppelt", timeout=9000)
        expect(modal.locator("[data-agent-copy]")).to_be_disabled()
        page.wait_for_timeout(200)
        assert len(list_requests) < 5, "Status-Swap loest wiederholte Listen-Requests aus"
        assert not modal.locator("[data-agent-command]").input_value()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        modal.get_by_role("button", name="Schliessen", exact=True).click()
        expect(modal).not_to_be_visible()
        count = len(requests)
        page.wait_for_timeout(5500)
        assert len(requests) == count, "Polling lief bei geschlossenem Dialog weiter"
        for section in ("displays", "plaene"):
            page.goto(agent_server + "/admin/infoscreen-agent/" + section)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        plan = page.locator("section.sys-card").filter(
            has=page.get_by_role("heading", name="Browser-Plan (Europe/Vienna)", exact=True)
        )
        plan.get_by_text("Slot hinzufuegen", exact=True).click()
        slot = plan.locator('form[hx-post$="/slots/0/speichern"]')
        slot.locator('[name="label"]').fill(f"Testslot{width}")
        slot.locator('[name="priority"]').fill("0" if width == 1280 else "1")
        slot.locator('[name="start_time"]').fill("18:00")
        slot.get_by_role("button", name="Seite anhaengen", exact=True).click()
        with page.expect_response(lambda response: "/slots/0/speichern" in response.url) as failed:
            slot.get_by_role("button", name="Slot speichern", exact=True).click()
        assert failed.value.status == 422, (failed.value.status, failed.value.text(), errors)
        assert not errors, errors
        expect(slot.locator("[data-agent-form-error]")).to_contain_text("Ende muss sich vom Start unterscheiden")
        expect(slot.locator('[name="label"]')).to_have_value(f"Testslot{width}")
        slot.locator('[name="end_time"]').fill("06:00")
        slot.get_by_role("button", name="Slot speichern", exact=True).click()
        expect(
            plan.get_by_text(
                f"Testslot{width}: 18:00:00 - 06:00:00 / Prioritaet " + ("0" if width == 1280 else "1"), exact=True
            )
        ).to_be_visible()
        plan.get_by_text("Ausnahmetag hinzufuegen", exact=True).click()
        exception = plan.locator('form[hx-post$="/ausnahmen/0/speichern"]')
        exception.locator('[name="datum"]').fill("2026-12-24" if width == 1280 else "2026-12-25")
        exception.locator('[name="label"]').fill(f"Ausnahme{width}")
        exception.get_by_role("button", name="Ersatz-Slot hinzufuegen", exact=True).click()
        replacement = exception.locator("[data-replacement-row]")
        replacement.locator('[data-replacement-field="start_time"]').fill("10:00")
        replacement.get_by_role("button", name="Seite anhaengen", exact=True).click()
        exception.get_by_role("button", name="Ausnahmetag speichern", exact=True).click()
        saved = plan.locator("details").filter(has=page.locator("summary", has_text=f"Ausnahme{width}")).first
        expect(saved.locator("summary").first).to_contain_text("Ersatz-Slots")
        saved.locator("summary").first.click()
        expect(saved.locator('[data-replacement-field="start_time"]')).to_have_value("10:00:00")
        expect(saved.locator("[data-rotation-rows]")).to_contain_text("Entfernen")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
    finally:
        page.close()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), int(sys.argv[2]))
