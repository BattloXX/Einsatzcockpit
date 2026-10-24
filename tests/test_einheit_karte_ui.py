"""Echte Leaflet-/Alpine-Bedienung mit mehreren eigenen Zielen und lokaler Standortfreigabe."""

import re
from datetime import UTC, datetime

import pytest

from app.models.major_incident import EinheitSiteDispatch
from tests.test_einheit_api import _db
from tests.test_einheit_kartenziele import _sperrenbestand
from tests.test_einheit_uebersicht import _bestand
from tests.test_einheit_uebersicht import browser as browser  # noqa: F401
from tests.test_einheit_uebersicht import seite as seite  # noqa: F401

DATEN = "Alpine.$data(document.querySelector('.einheit-page'))"


def _start(page):
    page.goto("https://ecp.test/einheit")
    page.get_by_role("tab", name="Karte", exact=True).click()
    return page.get_by_role("article", name="Gewähltes Kartenziel")


@pytest.mark.parametrize("width,height,aktuell", [(390, 844, True), (768, 1024, False), (1024, 768, True)])
def test_karte_mehrere_ziele_touch_navigation_und_adressfallback(
    client, setup_db, seite, tmp_path, width, height, aktuell,
):
    from playwright.sync_api import expect

    ids = _bestand(client, aktuell=aktuell)
    _sperrenbestand(ids)
    page, state, errors = seite
    page.set_viewport_size({"width": width, "height": height})
    ziel = _start(page)
    expect(ziel).to_contain_text("Aktueller Auftrag" if aktuell else "Nächster Auftrag")
    expect(ziel).to_contain_text("Schule")
    expect(ziel).to_contain_text("Stromleitung am Boden")
    expect(ziel).to_contain_text("Gasgeruch")
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    expect(ziel).not_to_contain_text("Fremde Sperre geheim")
    expect(page.locator('[x-ref=uebersichtKarte] .einheit-kartenmarker')).to_have_count(2)
    expect(page.locator('[x-ref=uebersichtKarte] .einheit-gefahrmarker')).to_have_count(2)
    expect(page.locator('[x-ref=uebersichtKarte] path[stroke="#d83030"]')).to_have_count(2)
    # Die Beschriftungen der beiden räumlich getrennten Ziele bleiben auch am Smartphone lesbar.
    assert page.locator('[x-ref=uebersichtKarte] .einheit-marker__beschriftung').evaluate_all("""nodes => {
      const [a, b] = nodes.map(n => n.getBoundingClientRect());
      return a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
    }""")
    expect(ziel.get_by_role("link", name="Navigation starten")).to_have_attribute("href", "https://www.google.com/maps/dir/?api=1&destination=47,9")
    for element in [ziel.get_by_role("link", name="Navigation starten"),
                    page.locator('[x-ref=uebersichtKarte] .leaflet-control-zoom-in'),
                    page.get_by_role("button", name="Zur Einsatzübersicht", exact=True)]:
        box = element.bounding_box()
        assert box["width"] >= 48 and box["height"] >= 48
    page.screenshot(path=str(tmp_path / f"einheitenkarte-{width}.png"), full_page=True, animations="disabled")
    liste = page.get_by_role("list", name="Aufträge auf der Karte")
    expect(liste.locator("li")).to_have_count(3)
    liste.get_by_role("button", name="Ziel wählen: Ohne Position", exact=True).click()
    expect(ziel).to_contain_text("Keine Kartenposition")
    expect(ziel).to_contain_text("Lange Straße")
    expect(ziel).not_to_contain_text("Zufahrt Schule gesperrt")
    expect(ziel.get_by_role("link", name="Navigation starten")).to_have_attribute(
        "href", re.compile("destination=Lange%20Stra"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not state["posts"]  # Zielauswahl bestätigt keinen Auftrag und ändert keinen Status.
    page.get_by_role("button", name="Zur Einsatzübersicht", exact=True).click()
    expect(page.locator("[data-einheit-auftrag]")).to_have_count(3)
    assert errors == []


def test_ueberlagerte_auftraege_gemeinsam_oeffnen_und_polling_behaelt_popup(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client, aktuell=True)
    with _db() as db:
        db.add(EinheitSiteDispatch(einheit_id=ids["e1"], site_id=ids["a"], dispatched_at=datetime.now(UTC),
                                  auftragsart="erkundung", auftrag="Zusätzliche Erkundung", reihenfolge=4))
        db.commit()
    page, state, errors = seite
    _start(page)
    marker = page.locator('[x-ref=uebersichtKarte] .einheit-kartenmarker--in_arbeit')
    expect(marker.locator('.einheit-marker__punkt')).to_have_css("width", "56px")
    expect(marker.locator('.einheit-marker__anzahl')).to_have_text("2")
    marker.click(position={"x": 28, "y": 28})
    popup = page.locator(".einheit-kartenpopup")
    expect(popup).to_contain_text("2 Aufträge an dieser Position")
    expect(popup.locator('.einheit-artchip')).to_have_text(["AUFTRAG", "ERKUNDUNG"])
    page.evaluate(f"async () => {{ const d = {DATEN}; d.lastLoad = 0; await d.laden(); }}")
    expect(popup).to_be_visible()
    expect(popup.get_by_role("button", name="Auftrag öffnen")).to_have_count(2)
    popup.get_by_role("button", name="Auftrag öffnen").last.click()
    expect(page.locator('.einheit-detail__art')).to_contain_text("ERKUNDUNG")
    expect(page.locator('.einheit-auftrag')).to_have_text("Zusätzliche Erkundung")
    assert not state["posts"]
    assert errors == []


def test_standort_nur_auf_wunsch_kein_umsortieren_und_freigabe_beenden(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    page.add_init_script("""
      window.geo = { starts: 0, stops: 0, callback: null };
      Object.defineProperty(navigator, 'geolocation', { value: {
        watchPosition(callback) { geo.starts++; geo.callback = callback; return geo.starts; },
        clearWatch() { geo.stops++; }
      }});
    """)
    _start(page)
    assert page.evaluate("geo.starts") == 0
    liste = page.get_by_role("list", name="Aufträge auf der Karte")
    reihe = liste.locator("li strong").all_text_contents()
    page.get_by_role("button", name="Mein Standort", exact=True).click()
    assert page.evaluate("geo.starts") == 1
    page.evaluate("geo.callback({ coords: { latitude: 48, longitude: 10, accuracy: 25 } })")
    expect(page.locator('.einheit-standortmarker')).to_have_count(1)
    assert liste.locator("li strong").all_text_contents() == reihe
    assert page.evaluate(f"{DATEN}.uebersichtKarte.auftraege.size") == 2
    page.get_by_role("button", name="Standort aus", exact=True).click()
    assert page.evaluate("geo.stops") == 1
    page.evaluate("geo.callback({ coords: { latitude: 49, longitude: 11, accuracy: 25 } })")
    expect(page.locator('.einheit-standortmarker')).to_have_count(0)  # Verspäteter Callback bleibt wirkungslos.
    page.get_by_role("button", name="Mein Standort", exact=True).click()
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    assert page.evaluate("geo.stops") == 2
    assert not state["posts"]
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "zugewiesen"
    assert errors == []


def test_offline_zielhinweise_pro_auftrag_und_letzter_stand(client, setup_db, seite):
    from playwright.sync_api import expect

    _sperrenbestand(_bestand(client))
    page, state, errors = seite
    ziel = _start(page)
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    # Warten auf den tatsächlich gespeicherten Cache, dann API-Verbindung verlieren.
    page.wait_for_function(f"!{DATEN}.kartenLaden")
    state["offline"] = True
    page.evaluate(f"{DATEN}.kartenZielLaden(true)")
    expect(ziel).to_contain_text("Offline – Sperren vom letzten geladenen Stand")
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    page.get_by_role("button", name="Ziel wählen: B", exact=True).click()
    expect(ziel).to_contain_text("Sperren am Ziel nicht verfügbar")
    expect(ziel).not_to_contain_text("Zufahrt Schule gesperrt")
    page.get_by_role("button", name="Ziel wählen: Schule", exact=True).click()
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    page.reload()
    page.get_by_role("tab", name="Karte", exact=True).click()
    expect(ziel).to_contain_text("Offline – Sperren vom letzten geladenen Stand")
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    assert errors == []


def test_zurueckgezogenes_ziel_verschwindet_ohne_fremde_oder_alte_hinweise(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    ziel = _start(page)
    page.get_by_role("button", name="Ziel wählen: B", exact=True).click()
    expect(ziel.get_by_role("heading")).to_have_text("B")
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["offen"][1]).withdrawn_at = datetime.now(UTC)
        db.commit()
    page.evaluate(f"async () => {{ const d = {DATEN}; d.lastLoad = 0; await d.laden(); }}")
    expect(ziel.get_by_role("heading")).to_have_text("Schule")
    expect(page.locator('[x-ref=uebersichtKarte] .einheit-kartenmarker')).to_have_count(1)
    expect(page.get_by_role("list", name="Aufträge auf der Karte").locator("li")).to_have_count(2)
    assert not state["posts"] and errors == []


def test_verzoegerte_zielantwort_ueberschreibt_keine_neue_auswahl(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    _sperrenbestand(ids)
    page, _, errors = seite
    gehalten = []
    page.route(f"**/einheit/api/auftrag/{ids['d1']}", lambda route: gehalten.append(route))
    ziel = _start(page)
    expect(ziel).to_contain_text("Sperren am Ziel werden geladen")
    page.get_by_role("button", name="Ziel wählen: B", exact=True).click()
    expect(ziel.get_by_role("heading")).to_have_text("B")
    page.wait_for_function(f"!{DATEN}.kartenLaden")
    response = client.get(f"/einheit/api/auftrag/{ids['d1']}")
    for route in gehalten:
        route.fulfill(status=200, content_type="application/json", body=response.content)
    expect(ziel).not_to_contain_text("Zufahrt Schule gesperrt")
    expect(ziel.get_by_role("heading")).to_have_text("B")
    assert errors == []


def test_sperren_aendern_sich_auch_bei_unveraendertem_auftrags_etag(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.models.road_closure import RoadClosure

    ids = _bestand(client)
    sperre_id = _sperrenbestand(ids)
    page, _, errors = seite
    ziel = _start(page)
    expect(ziel).to_contain_text("Zufahrt Schule gesperrt")
    page.wait_for_function(f"!{DATEN}.kartenLaden")
    etag = client.get("/einheit/api/zustand").headers["etag"]
    with _db() as db:
        db.get(RoadClosure, sperre_id).cancelled_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    assert client.get("/einheit/api/zustand", headers={"If-None-Match": etag}).status_code == 304
    page.evaluate(f"async () => {{ const d = {DATEN}; d.lastLoad = 0; await d.laden(); }}")
    expect(ziel).not_to_contain_text("Zufahrt Schule gesperrt")
    expect(ziel).to_contain_text("Freigegebene Sperre")
    assert errors == []


def test_standort_abgelehnt_und_ziel_ohne_adresse_bleiben_bedienbar(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.models.major_incident import IncidentSite

    ids = _bestand(client)
    with _db() as db:
        site = db.get(IncidentSite, ids["a"])
        site.lat = site.lng = None
        site.strasse = site.hausnr = site.ort = None
        db.commit()
    page, _, errors = seite
    page.add_init_script("""
      window.geoDenied = { starts: 0, stops: 0 };
      Object.defineProperty(navigator, 'geolocation', { value: {
        watchPosition(_callback, error) { geoDenied.starts++; setTimeout(error, 0); return 1; },
        clearWatch() { geoDenied.stops++; }
      }});
    """)
    ziel = _start(page)
    expect(ziel).to_contain_text("Navigation nicht verfügbar")
    expect(ziel.get_by_role("link", name="Navigation starten")).not_to_be_visible()
    assert page.evaluate("geoDenied.starts") == 0
    page.get_by_role("button", name="Mein Standort", exact=True).click()
    expect(page.get_by_text("Standort nicht verfügbar – Karte funktioniert weiter", exact=True)).to_be_visible()
    assert page.evaluate("geoDenied.starts === 1 && geoDenied.stops === 1")
    ziel.get_by_role("button", name="Auftrag öffnen", exact=True).click()
    expect(page.locator(".einheit-detail__art")).to_contain_text("ERKUNDUNG")
    assert errors == []
