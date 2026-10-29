"""Browser-Regressionspruefung fuer Layout, Seitenwechsel und Aktualisierung."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app.core.templating import templates


def _html(data, *, modus="infoscreen", show_map=True):
    return templates.env.get_template("road_closure/infoscreen.html").render(
        org=SimpleNamespace(name="Feuerwehr Musterort", logo_path=None, fallback_lat=47.4, fallback_lng=9.7),
        daten=data, modus=modus, external=False, zeige_karte=show_map,
        daten_url="/daten", filter_query="", ASSET_VERSION="test",
    )


def _data(count, rotation=0):
    return {
        "kennzahlen": {"aktiv": count}, "zeige_geplante": True, "refresh_sec": 30, "rotation_sec": rotation,
        "sperren": [{
            "title": f"Sperre {index} " + "Langer Strassenname " * 12,
            "status": "active" if index % 2 == 0 else "planned", "color": "red",
            "restriction_label": "Vollsperre", "abschnitt": "Abschnitt " * 30,
            "reason": "Bauarbeiten " * 50, "exceptions": "Einsatzfahrzeuge " * 20,
            "nachbar": "Nachbargemeinde", "valid_from_local": "29.10.2026 10:00",
            "geometry": {"type": "Point", "coordinates": [9.7 + index * .001, 47.4]},
        } for index in range(count)],
    }


def test_template_verwendet_gemeinsamen_monitorrahmen():
    html = _html(_data(2))
    assert "/static/css/infoscreen.css?" in html
    assert 'class="rc infoscreen-frame bg-background"' in html
    assert 'id="page-indicator"' in html
    assert "<style>" not in html
    assert "overflow:auto" not in html


def test_browser_seiten_rotation_resize_refresh_und_mobil():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path("app/static").resolve()
    state = {"data": _data(25), "modus": "infoscreen", "map": True, "status": 200}
    requests = []
    errors = []

    def handle(route):
        path = urlsplit(route.request.url).path
        if path == "/daten":
            requests.append(path)
            route.fulfill(status=state["status"], content_type="application/json", body=json.dumps(state["data"]))
        elif path.startswith("/static/"):
            asset = (static / path.removeprefix("/static/")).resolve()
            if asset.is_relative_to(static) and asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.fulfill(status=404)
        elif path == "/screen":
            route.fulfill(content_type="text/html",
                          body=_html(state["data"], modus=state["modus"], show_map=state["map"]))
        else:
            route.abort()

    with playwright.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch(executable_path=os.environ.get("ECP_PLAYWRIGHT_EXECUTABLE"))
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Playwright Chromium nicht installiert")
            raise
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", handle)
        page.clock.install()

        def fits():
            dimensions = page.evaluate("""() => ['body', '#list'].map(selector => {
                const node = document.querySelector(selector);
                return [selector, node.scrollHeight, node.clientHeight, node.scrollWidth, node.clientWidth];
            })""")
            assert all(height <= client and width <= client_width
                       for _, height, client, width, client_width in dimensions), dimensions

        for count in (0, 1, 25):
            state["data"] = _data(count)
            for width, height in ((1920, 1080), (3840, 2160), (1280, 720)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto("http://ecp.test/screen")
                page.evaluate("document.fonts.ready")
                page.clock.run_for(100)
                fits()
                if count == 25:
                    capacity = page.locator("#list .card").count()
                    pages = (count + capacity - 1) // capacity
                    assert page.locator("#page-indicator").inner_text() == f"Seite 1/{pages}"
                    seen = set(page.locator("#list h3").all_text_contents())
                    for index in range(1, pages):
                        page.clock.run_for(15000)
                        page.wait_for_function(
                            "(label) => document.querySelector('#page-indicator').textContent === label",
                            arg=f"Seite {index + 1}/{pages}",
                        )
                        fits()
                        seen.update(page.locator("#list h3").all_text_contents())
                    assert len(seen) == count
                    assert requests  # Aktualisierung setzt den Seitenfortschritt nicht zurueck.
                    page.clock.run_for(15000)
                    assert page.locator("#page-indicator").inner_text() == f"Seite 1/{pages}"

        # Rotation mit echter Leaflet-Karte, Detailansichten und Rueckkehr zur Liste.
        state["data"] = _data(2, rotation=5)
        page.goto("http://ecp.test/screen")
        page.clock.run_for(100)
        page.clock.run_for(5000)
        assert page.locator("#road-closure-screen").evaluate("node => node.classList.contains('view-map')")
        page.clock.run_for(5000)
        assert page.locator(".card--gross").count() == 1
        fits()
        page.clock.run_for(10000)
        assert page.locator("#page-indicator").is_visible()

        # Groessenaenderung berechnet Kapazitaet neu; mobil bleiben alle Karten erreichbar.
        state["data"] = _data(25)
        page.goto("http://ecp.test/screen")
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.clock.run_for(100)
        before = page.locator("#list .card").count()
        page.set_viewport_size({"width": 3840, "height": 2160})
        page.clock.run_for(100)
        page.wait_for_function(
            "(count) => document.querySelectorAll('#list .card').length > count", arg=before,
        )
        fits()
        page.set_viewport_size({"width": 760, "height": 800})
        page.clock.run_for(100)
        playwright.expect(page.locator("#list .card")).to_have_count(25)
        assert not page.locator("#page-indicator").is_visible()
        assert page.evaluate("document.body.scrollHeight > innerHeight")

        # Status und ausgeblendete Karte bleiben funktionsfaehig.
        page.set_viewport_size({"width": 1280, "height": 720})
        state.update(modus="status", map=False)
        page.goto("http://ecp.test/screen")
        playwright.expect(page.locator("#list .card")).to_have_count(25)
        assert page.locator("#fullscreen").is_visible()
        state.update(modus="infoscreen", data=_data(2, rotation=5))
        page.goto("http://ecp.test/screen")
        page.clock.run_for(5100)
        assert page.locator(".card--gross").count() == 1
        fits()
        state["status"] = 503
        page.clock.run_for(30000)
        page.wait_for_function("document.querySelector('#road-closure-screen').classList.contains('is-offline')")
        state.update(status=200, data=_data(1))
        page.clock.run_for(30000)
        page.wait_for_function("!document.querySelector('#road-closure-screen').classList.contains('is-offline')")
        fits()
        assert not errors
        browser.close()
