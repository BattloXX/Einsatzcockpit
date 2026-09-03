"""Gemeinsame Fixtures fuer die explizit gestarteten Board-Browsertests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(__file__).with_name("artifacts")


def _env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env.board-e2e"
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8094")


@pytest.fixture(scope="session")
def zugangsdaten() -> tuple[str, str]:
    values = _env_file()
    user = os.environ.get("BOOTSTRAP_ADMIN_USER", values.get("BOOTSTRAP_ADMIN_USER", ""))
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", values.get("BOOTSTRAP_ADMIN_PASSWORD", ""))
    if not user or not password:
        pytest.skip("Board-E2E-Zugangsdaten fehlen")
    return user, password


def login(page: Page, base_url: str, zugangsdaten: tuple[str, str]) -> None:
    page.goto(base_url + "/login")
    page.locator("#username").fill(zugangsdaten[0])
    page.locator("#password").fill(zugangsdaten[1])
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert not page.url.startswith(base_url + "/login")


@pytest.fixture(scope="session")
def anmeldestatus(browser: Browser, base_url: str, zugangsdaten: tuple[str, str], tmp_path_factory) -> Path:
    context = browser.new_context(service_workers="block")
    page = context.new_page()
    login(page, base_url, zugangsdaten)
    path = tmp_path_factory.mktemp("board-auth") / "state.json"
    context.storage_state(path=path)
    context.close()
    return path


@pytest.fixture
def angemeldete_seite(
    browser: Browser, anmeldestatus: Path, request: pytest.FixtureRequest
):
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, storage_state=anmeldestatus, service_workers="block"
    )
    page = context.new_page()
    yield page
    if getattr(request.node, "rep_call", None) and request.node.rep_call.failed:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=ARTIFACTS / f"{request.node.name}.png", full_page=True)
    context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "rep_" + report.when, report)


@pytest.fixture
def zweiter_kontext(browser: Browser, anmeldestatus: Path) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": 1280, "height": 800}, storage_state=anmeldestatus, service_workers="block"
    )
    context.new_page()
    yield context
    context.close()
