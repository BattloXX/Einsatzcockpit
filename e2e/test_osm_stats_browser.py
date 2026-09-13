"""Browser-Regressionstest für die OSM-Kacheln des Statistik-Infoscreens."""
from __future__ import annotations

from urllib.parse import urlsplit

from playwright.sync_api import Page, expect


def test_statistik_infoscreen_verwendet_nur_den_kanonischen_osm_host(
    angemeldete_seite: Page, base_url: str
) -> None:
    page = angemeldete_seite
    # Der Bootstrap-Systemadmin hat keine feste Organisation; die Seed-Organisation
    # des isolierten E2E-Stacks wird daher explizit ausgewählt.
    page.goto(f"{base_url}/admin/settings/statistik?org_id=1")

    enabled = page.locator('input[name="enabled_raw"]')
    if not enabled.is_checked():
        with page.expect_response(lambda response: "/infoscreen-toggle" in response.url) as toggle:
            enabled.check()
        assert toggle.value.status == 200
        expect(page.locator('input[name="enabled_raw"]')).to_be_checked()

    page.locator('input[name="label"]').fill("OSM Browser-Regression")
    page.get_by_role("button", name="🔑 Token erzeugen & URL anzeigen").click()
    dashboard_url = page.locator("#statsDashUrl").input_value()
    path = urlsplit(dashboard_url).path

    tile_responses: list[tuple[str, int]] = []

    def capture_tile(response) -> None:
        if "tile.openstreetmap.org" in response.url:
            tile_responses.append((response.url, response.status))

    page.on("response", capture_tile)
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    expect(page.locator("#stats-map")).to_be_visible()
    expect(page.locator(".leaflet-control-attribution")).to_contain_text("OpenStreetMap contributors")
    page.wait_for_timeout(2_000)

    assert tile_responses, "Der Statistik-Infoscreen hat keine OSM-Tiles angefordert."
    assert all(urlsplit(url).netloc == "tile.openstreetmap.org" for url, _ in tile_responses)
    assert not any(status in {403, 429} or status >= 500 for _, status in tile_responses)
