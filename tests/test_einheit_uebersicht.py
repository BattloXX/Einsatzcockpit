"""Vereinfachte Startansicht: gleiche Aufträge/Rechte, Gefahren und echte Touch-Bedienung."""

import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.models.major_incident import CrossSiteMarker, EinheitSiteDispatch, IncidentSite, LageEinheit, SitePriority
from tests.test_einheit_api import _daten, _db, _device


def _bestand(client, *, aktuell=False):
    ids = _daten()
    with _db() as db:
        einheit = db.get(LageEinheit, ids["e1"])
        einheit.label = "Gruppe Wolfurt"
        site = db.get(IncidentSite, ids["a"])
        site.bezeichnung, site.strasse = "Schule", "Dorfstraße 1"
        site.priority = SitePriority.sofort
        erster = db.get(EinheitSiteDispatch, ids["d1"])
        erster.auftrag = "Gebäude erkunden und Ergebnis melden."
        erster.auftragsart = "abarbeitung" if aktuell else "erkundung"
        erster.einheit_status = "in_arbeit" if aktuell else "zugewiesen"
        erster.reihenfolge = 1
        if aktuell:
            einheit.incident_site_id = site.id
        zweiter = EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=ids["b"], dispatched_at=datetime.now(UTC),
            auftrag="Zufahrt freimachen.", reihenfolge=2,
        )
        ohne_position = IncidentSite(
            major_incident_id=ids["lage"], org_id=site.org_id, bezeichnung="Ohne Position",
            strasse="Lange Straße " + "SehrlangerAdresszusatz" * 8,
        )
        db.add(ohne_position)
        db.flush()
        dritter = EinheitSiteDispatch(
            einheit_id=einheit.id, site_id=ohne_position.id, dispatched_at=datetime.now(UTC),
            auftrag="Material bereitstellen.", reihenfolge=3,
        )
        db.add_all([zweiter, dritter])
        for lage_id, title, lat, lng, status in [
            (ids["lage"], "Stromleitung am Boden", 47.0001, 9, "aktiv"),
            (ids["lage"], "Gasgeruch", 47, 9.0001, "aktiv"),
            (ids["lage"], "Bereits behoben", 47, 9, "behoben"),
            (ids["lage"], "Zu weit entfernt", 46, 8, "aktiv"),
            (db.get(LageEinheit, ids["fe"]).lage_id, "Fremde Lage geheim", 47, 9, "aktiv"),
        ]:
            db.add(CrossSiteMarker(
                major_incident_id=lage_id, title=title, lat=lat, lng=lng, status=status, marker_type="sonstiges",
            ))
        db.commit()
        ids["offen"] = [erster.id, zweiter.id, dritter.id]
    _device(client, ids)
    return ids


def test_gefahren_der_startseite_entsprechen_dem_detail_und_aendern_etag(client, setup_db):
    ids = _bestand(client)
    response = client.get("/einheit/api/zustand")
    data = response.json()
    auftraege = data["kategorien"]["weitere"]
    assert [a["dispatch_id"] for a in auftraege] == ids["offen"]
    assert data["kopf"]["offen"] == 3
    assert auftraege[0]["gefahren"] == client.get(f"/einheit/api/auftrag/{ids['d1']}").json()["gefahren"]
    assert {g["title"] for g in auftraege[0]["gefahren"]} == {"Stromleitung am Boden", "Gasgeruch"}
    assert auftraege[0]["gefahren_verfuegbar"] is True
    assert auftraege[1]["gefahren"] == [] and auftraege[1]["gefahren_verfuegbar"] is True
    assert auftraege[2]["gefahren"] == [] and auftraege[2]["gefahren_verfuegbar"] is False
    assert "Fremde Lage geheim" not in response.text
    with _db() as db:
        marker = db.query(CrossSiteMarker).filter_by(major_incident_id=ids["lage"], title="Gasgeruch").one()
        marker.status = "behoben"
        db.commit()
    refreshed = client.get("/einheit/api/zustand", headers={"If-None-Match": response.headers["etag"]})
    assert refreshed.status_code == 200
    assert refreshed.headers["etag"] != response.headers["etag"]
    assert [g["title"] for g in refreshed.json()["kategorien"]["weitere"][0]["gefahren"]] == ["Stromleitung am Boden"]
    with _db() as db:
        auftrag = db.get(EinheitSiteDispatch, ids["d1"])
        assert auftrag.einheit_status == "zugewiesen" and auftrag.version == 1
    assert client.get(f"/einheit/api/auftrag/{ids['d2']}").status_code == 404


def test_qr_gefahren_bleiben_lagegebunden_und_pflege_wird_nicht_angeboten(client, setup_db):
    from tests.test_gk_qr_zugang import _eingeloest

    ids = _eingeloest(client, ressource_pflegen=True)
    with _db() as db:
        site = db.get(EinheitSiteDispatch, ids["eigener"]).site
        site.lat, site.lng = 47, 9
        db.add(CrossSiteMarker(
            major_incident_id=ids["lage"], title="Gefahr an eigener Stelle", lat=47, lng=9,
        ))
        db.commit()
    response = client.get("/einheit/api/zustand").json()
    assert response["kann_ressource_pflegen"] is False
    assert [a["dispatch_id"] for a in response["kategorien"]["weitere"]] == [ids["eigener"]]
    assert response["kategorien"]["weitere"][0]["gefahren"][0]["title"] == "Gefahr an eigener Stelle"
    assert client.get(f"/einheit/api/auftrag/{ids['fremder']}").status_code == 404


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch(executable_path=os.environ.get("ECP_PLAYWRIGHT_EXECUTABLE"))
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Playwright Chromium nicht installiert")
            raise
        yield browser
        browser.close()


@pytest.fixture
def seite(client, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    context.add_init_script("""
        // Live-Transport separat getestet; hier laufen echte Templates, Alpine, Leaflet und IndexedDB.
        window.WebSocket = class extends EventTarget {
            static OPEN = 1;
            constructor() { super(); this.readyState = 1; }
            send() {}
            close() { this.readyState = 3; }
        };
    """)
    page = context.new_page()
    state = {"offline": False, "schreiben_offline": False, "posts": [], "zustand": 0}
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    static = Path("app/static").resolve()

    def handle(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "ecp.test":
            route.abort()
        elif url.path.startswith("/static/"):
            asset = (static / url.path.removeprefix("/static/")).resolve()
            if asset.is_relative_to(static) and asset.is_file():
                route.fulfill(path=str(asset))
            else:
                route.fulfill(status=404)
        elif url.path.startswith("/karten/"):
            route.fulfill(content_type="image/svg+xml", body=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"></svg>'
            ))
        elif url.path.startswith("/einheit/api/") and (
            state["offline"] or (state["schreiben_offline"] and request.method == "POST")
        ):
            route.abort()
        else:
            if request.method == "POST":
                state["posts"].append(url.path)
            if url.path == "/einheit/api/zustand":
                state["zustand"] += 1
            headers = {k: v for k, v in request.headers.items()
                       if k in {"if-none-match", "x-ec-einheit", "x-ec-einheit-sim", "x-csrf-token", "content-type",
                                "x-ec-einheit-kontext"}}
            response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                      headers=headers, content=request.post_data_buffer)
            route.fulfill(status=response.status_code, headers={
                k: v for k, v in response.headers.items() if k.lower() not in {"content-encoding", "content-length"}
            }, body=response.content)

    page.route("**/*", handle)
    yield page, state, errors
    context.close()


@pytest.mark.parametrize("aktuell", [False, True])
def test_browser_alle_offenen_auftraege_einmal_und_gleiche_navigation(client, setup_db, seite, aktuell, tmp_path):
    from playwright.sync_api import expect

    ids = _bestand(client, aktuell=aktuell)
    page, state, errors = seite
    # Auch mit der alten gespeicherten Kartenauswahl beginnt die neue Startansicht bei Einsätze.
    page.add_init_script("localStorage.setItem('ec-einheit:bereich', 'karte')")
    page.goto("https://ecp.test/einheit")
    expect(page.get_by_role("heading", name="Gruppe Wolfurt")).to_be_visible()
    karten = page.locator("[data-einheit-auftrag]")
    expect(karten).to_have_count(3)
    assert state["zustand"] == 1  # Alpine initialisiert genau einmal.
    assert karten.evaluate_all("nodes => nodes.map(n => Number(n.dataset.dispatchId))") == ids["offen"]
    fokus = page.locator('[data-hervorgehoben="true"]')
    expect(fokus).to_contain_text("Aktueller Auftrag" if aktuell else "Nächster Auftrag")
    expect(fokus).to_contain_text("Stromleitung am Boden")
    expect(fokus).to_contain_text("Gasgeruch")
    expect(fokus).to_contain_text("Dorfstraße 1")
    expect(fokus).to_contain_text("Gebäude erkunden und Ergebnis melden.")
    expect(fokus).to_contain_text("In Arbeit" if aktuell else "Zugewiesen")
    expect(fokus.locator(".einheit-artchip")).to_have_text("AUFTRAG" if aktuell else "ERKUNDUNG")
    expect(fokus.locator(".einheit-artchip")).to_have_css(
        "border-top-color", "rgb(24, 119, 242)" if aktuell else "rgb(230, 126, 34)"
    )
    expect(fokus).to_have_css("border-left-color", "rgb(216, 48, 48)")  # Sofort, unabhängig von der Art.
    expect(page.locator(".einheit-meta")).to_contain_text("3 offene Aufträge")
    expect(page.locator(".einheit-meta")).to_contain_text("Verbunden")
    page.screenshot(path=str(tmp_path / "einheit-smartphone.png"), full_page=True)
    for width, height in [(360, 800), (390, 844), (768, 1024), (900, 600), (1024, 768)]:
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for name in ["Einsätze", "Karte", "Meldungen"]:
            button = page.get_by_role("tab", name=name, exact=True)
            expect(button).to_be_visible()
            box = button.bounding_box()
            assert box["width"] >= 48 and box["height"] >= 48
        for button in page.locator('[data-einheit-auftrag] button').all():
            box = button.bounding_box()
            assert box["width"] >= 48 and box["height"] >= 48
    page.screenshot(path=str(tmp_path / "einheit-tablet.png"), full_page=True)
    for dispatch_id in ids["offen"]:
        page.locator(f'[data-dispatch-id="{dispatch_id}"] button').click()
        expect(page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Foto", exact=True)).to_be_visible()
        page.get_by_role("tab", name="Einsätze", exact=True).click()
        expect(karten).to_have_count(3)
    page.get_by_role("tab", name="Karte", exact=True).click()
    expect(page.get_by_role("button", name="Mein Standort", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.get_by_role("button", name="Jetzt synchronisieren", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Übermittlungen", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    expect(fokus).to_be_visible()
    assert not state["posts"] and not errors
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == ("in_arbeit" if aktuell else "zugewiesen")


def test_browser_offline_gefahren_lokaler_status_und_wiederverbindung_mit_304(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    page.goto("https://ecp.test/einheit")
    expect(page.locator('[data-hervorgehoben="true"]')).to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxVerfuegbar")
    state["offline"] = True
    state["schreiben_offline"] = True
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.locator(".einheit-meta")).to_contain_text("Offline")
    expect(page.get_by_text("Stromleitung am Boden · Sonstige Lageinfo", exact=True)).to_be_visible()
    page.evaluate("""async () => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        await c.outbox.erfassen({typ: 'status', dispatch_id: c.fokusAuftrag.dispatch_id,
            einheit_id: c.einheitId, payload: {status: 'anfahrt'}});
    }""")
    expect(page.locator('[data-hervorgehoben="true"]')).to_contain_text("Anfahrt – noch nicht übermittelt")
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.get_by_role("button", name="Erneut senden", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Verwerfen", exact=True)).to_be_visible()
    # Kein Flush: nur der Leseabgleich wird wieder erlaubt und antwortet unverändert mit 304.
    state["offline"] = False
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.locator(".einheit-meta")).to_contain_text("Verbunden")
    expect(page.locator(".einheit-meta")).to_contain_text("1 noch nicht übermittelt")
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.locator(f'[data-dispatch-id="{ids["d1"]}"] button').click()
    expect(page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True)).to_be_visible()
    assert not errors


def test_browser_leerstand_behaelt_archiv_und_auftragsaktionen(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    with _db() as db:
        for index, dispatch_id in enumerate(ids["offen"]):
            dispatch = db.get(EinheitSiteDispatch, dispatch_id)
            if index == 1:
                dispatch.withdrawn_at = datetime.now(UTC)
            else:
                dispatch.beendet_at = datetime.now(UTC)
                dispatch.einheit_status = "abgeschlossen" if index == 0 else "nicht_durchfuehrbar"
        db.commit()
    page, state, errors = seite
    page.goto("https://ecp.test/einheit")
    expect(page.get_by_text("Keine offenen Aufträge", exact=True)).to_be_visible()
    expect(page.locator("[data-einheit-auftrag]")).to_have_count(0)
    page.get_by_text("Beendet (2)", exact=True).click()
    expect(page.get_by_text("Erkundet", exact=True)).to_be_visible()
    expect(page.locator(".einheit-liste[open]").get_by_text("Nicht durchführbar", exact=True)).to_be_visible()
    page.locator(".einheit-liste[open] button").first.click()
    expect(page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.get_by_text("Zurückgezogen (1)", exact=True).click()
    expect(page.locator(".einheit-liste[open] button")).to_have_count(3)
    assert not state["posts"] and not errors


def test_browser_gk_ressourcenpflege_bleibt_erreichbar(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.models.major_incident import MajorIncident
    from app.models.master import OrgSettings
    from tests.test_einheit_zugang import _zugang_mit_auftrag

    cookie, _, einheit_id, _ = _zugang_mit_auftrag()
    client.cookies.clear()
    client.cookies.set("ec_gk", cookie)
    with _db() as db:
        einheit = db.get(LageEinheit, einheit_id)
        settings = db.query(OrgSettings).filter_by(org_id=db.get(MajorIncident, einheit.lage_id).org_id).one()
        settings.gk_zugang_ressource_pflegen = True
        db.commit()
    page, state, errors = seite
    page.goto("https://ecp.test/einheit")
    page.get_by_text("Meine Einheit – Personal und Ausstattung", exact=True).click()
    expect(page.get_by_role("button", name="Personal speichern", exact=True)).to_be_visible()
    expect(page.get_by_placeholder("Ausstattung hinzufügen", exact=True)).to_be_visible()
    assert not state["posts"] and not errors


@pytest.mark.parametrize("auftragsart,label,farbe", [
    ("erkundung", "ERKUNDUNG", "rgb(230, 126, 34)"),
    ("abarbeitung", "AUFTRAG", "rgb(24, 119, 242)"),
])
def test_browser_art_in_detail_dialog_marker_meldungen_und_archiv(
    client, setup_db, seite, auftragsart, label, farbe, tmp_path,
):
    from playwright.sync_api import expect

    from app.models.major_incident import SiteLogEntry

    ids = _bestand(client)
    with _db() as db:
        erster = db.get(EinheitSiteDispatch, ids["d1"])
        erster.auftragsart = auftragsart
        # Dieselbe Stelle kann Erkundung und Abarbeitung haben: Stellenchronik nicht blind etikettieren.
        db.add(EinheitSiteDispatch(
            einheit_id=erster.einheit_id, site_id=erster.site_id, dispatched_at=datetime.now(UTC),
            auftragsart="abarbeitung" if auftragsart == "erkundung" else "erkundung",
        ))
        db.add(SiteLogEntry(incident_site_id=erster.site_id, einheit_id=erster.einheit_id,
                            kind="note", text="Stellenmeldung ohne eindeutigen Auftragsbezug"))
        db.commit()
    page, state, errors = seite
    page.context.add_cookies([{
        "name": "ec_csrf", "value": client.cookies.get("ec_csrf"), "url": "https://ecp.test",
    }])
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    art = page.locator(".einheit-detail__art .einheit-artchip")
    expect(art).to_have_text(label)
    expect(art).to_have_css("border-top-color", farbe)
    expect(art.locator("svg")).to_have_count(1)
    expect(page.locator("section[x-show=detail] .einheit-prioritaet")).to_have_text("Priorität: Sofort")
    detail_marker = page.locator(".einheit-einsatzkarte__karte .einheit-kartenmarker")
    expect(detail_marker.locator(".einheit-artchip")).to_have_text(label)
    expect(detail_marker.locator(".einheit-marker__punkt")).to_have_css("border-top-color", "rgb(216, 48, 48)")
    beschriftung = detail_marker.locator(".einheit-marker__beschriftung").bounding_box()
    attribution = page.locator(".einheit-einsatzkarte__karte .leaflet-control-attribution").bounding_box()
    assert beschriftung and attribution and beschriftung["y"] + beschriftung["height"] <= attribution["y"]
    page.screenshot(path=str(tmp_path / f"auftragsart-{auftragsart}-detail.png"), full_page=True, animations="disabled")
    expect(page.get_by_text("Stellenmeldung ohne eindeutigen Auftragsbezug", exact=True)).to_be_visible()
    stellenmeldung = page.locator('[data-einheit-verlauf="stelle"] li').filter(
        has=page.get_by_text("Stellenmeldung ohne eindeutigen Auftragsbezug", exact=True),
    )
    assert stellenmeldung.locator(".einheit-artchip").count() == 0
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    expect(dialog.locator(".einheit-artchip")).to_have_text(label)
    state["schreiben_offline"] = True
    dialog.get_by_placeholder("Text eingeben oder diktieren").fill("Neue Lagemeldung zur Schule")
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    meldungen = page.get_by_role("region", name="Meldungen", exact=True)
    expect(meldungen.locator("article .einheit-artchip")).to_have_text(label)
    expect(meldungen.get_by_role("button", name="Erneut senden", exact=True)).to_be_visible()
    state["schreiben_offline"] = False
    meldungen.get_by_role("button", name="Erneut senden", exact=True).click()
    expect(meldungen.locator("article")).to_have_count(0)
    bestaetigte_meldung = meldungen.locator('[data-einheit-verlauf="zentral"] li').filter(
        has=page.get_by_text("Neue Lagemeldung zur Schule", exact=True),
    )
    expect(bestaetigte_meldung.locator(".einheit-artchip")).to_have_text(label)
    page.get_by_role("tab", name="Karte", exact=True).click()
    legende = page.get_by_label("Kartenlegende", exact=True)
    expect(legende.locator(".einheit-artchip")).to_have_text(["ERKUNDUNG", "AUFTRAG"])
    expect(legende).to_contain_text("Rand und Prioritätsangabe = Dringlichkeit")
    # Zweite Stelle ohne explizites auftragsart beim Erstellen bleibt ein normaler Bestandsauftrag.
    kartenliste = page.get_by_role("list", name="Aufträge auf der Karte", exact=True)
    expect(kartenliste).to_contain_text("AUFTRAG")
    marker = page.locator("[x-ref=uebersichtKarte] .einheit-kartenmarker").first
    expect(marker.locator(".einheit-artchip")).to_have_text(label)
    expect(marker.locator(".einheit-marker__punkt")).to_have_css("border-top-color", "rgb(216, 48, 48)")
    # Deckungsgleiche Einsatzorte zeigen alle Aufträge im gemeinsamen Popup.
    marker.click(position={"x": 24, "y": 24})
    popup = page.locator(".einheit-kartenpopup")
    expect(popup.locator(".einheit-artchip")).to_have_text([
        label, "AUFTRAG" if auftragsart == "erkundung" else "ERKUNDUNG",
    ])
    expect(popup).to_contain_text("2 Aufträge an dieser Position")
    expect(popup).to_contain_text("Priorität: Sofort")
    expect(popup).to_have_css("opacity", "1")
    page.screenshot(path=str(tmp_path / f"auftragsart-{auftragsart}-karte.png"), full_page=True, animations="disabled")
    for width, height in [(360, 800), (768, 1024), (1024, 768)]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # Ein beendeter Auftrag behält seine Art; nur die Darstellung, kein neuer Statusablauf.
    with _db() as db:
        erster = db.get(EinheitSiteDispatch, ids["d1"])
        erster.einheit_status, erster.beendet_at = "abgeschlossen", datetime.now(UTC)
        db.commit()
    page.reload()
    page.get_by_text("Beendet (1)", exact=True).click()
    expect(page.locator(".einheit-liste[open] .einheit-artchip")).to_have_text(label)
    assert not errors


def test_browser_fehlende_und_unbekannte_art_wird_nicht_aus_text_oder_label_erraten(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    data = client.get("/einheit/api/zustand").json()
    auftraege = data["kategorien"]["weitere"]
    auftraege[0].pop("auftragsart")
    auftraege[0]["auftragsart_label"] = "Erkundung"
    auftraege[1]["auftragsart"] = "__proto__"
    auftraege[1]["auftrag"] = "Erkundung sofort beginnen"
    auftraege[2]["auftragsart"] = None
    page, state, errors = seite
    page.route("**/einheit/api/zustand", lambda route: route.fulfill(json=data))
    page.goto("https://ecp.test/einheit")
    karten = page.locator("[data-einheit-auftrag]")
    expect(karten.locator(".einheit-artchip")).to_have_text(["ART UNBEKANNT"] * 3)
    expect(karten.first).to_have_css("border-left-color", "rgb(216, 48, 48)")
    for art in karten.locator(".einheit-artchip").all():
        expect(art.locator("svg")).to_have_count(1)
        assert "einheit-art--unbekannt" in art.get_attribute("class").split()
    page.get_by_role("tab", name="Karte", exact=True).click()
    expect(page.locator("[x-ref=uebersichtKarte] .einheit-artchip")).to_have_text(["ART UNBEKANNT"] * 2)
    assert not state["posts"] and not errors
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).auftragsart == "erkundung"


@pytest.mark.parametrize("priority,farbe", [
    (SitePriority.sofort, "rgb(216, 48, 48)"),
    (SitePriority.dringend, "rgb(245, 158, 11)"),
    (SitePriority.normal, "rgb(214, 176, 0)"),
    (SitePriority.aufschiebbar, "rgb(91, 64, 61)"),
])
def test_browser_dringlichkeit_fuer_beide_arten_unabhaengig(client, setup_db, seite, priority, farbe):
    from playwright.sync_api import expect

    ids = _bestand(client)
    with _db() as db:
        site = db.get(IncidentSite, ids["a"])
        site.priority = priority
        db.add(EinheitSiteDispatch(
            einheit_id=ids["e1"], site_id=site.id, dispatched_at=datetime.now(UTC), auftragsart="abarbeitung",
        ))
        db.commit()
    page, _, errors = seite
    page.emulate_media(color_scheme="dark")  # Bisherige Dringlichkeitsfarben im dunklen ECP-Schema.
    page.goto("https://ecp.test/einheit")
    karten = page.locator("[data-einheit-auftrag]").filter(has=page.get_by_role("heading", name="Schule", exact=True))
    expect(karten).to_have_count(2)
    for karte in karten.all():
        expect(karte).to_have_css("border-left-color", farbe)
        expect(karte.locator(".einheit-prioritaet")).to_be_visible()
    expect(karten.locator(".einheit-artchip")).to_have_text(["ERKUNDUNG", "AUFTRAG"])
    page.get_by_role("tab", name="Karte", exact=True).click()
    marker = page.locator("[x-ref=uebersichtKarte] .einheit-kartenmarker").filter(has_text=priority.name.capitalize())
    expect(marker).to_have_count(2)
    for eintrag in marker.locator(".einheit-marker__punkt").all():
        expect(eintrag).to_have_css("border-top-color", farbe)
    assert not errors


def _csrf_im_browser(page, client):
    page.context.add_cookies([{
        "name": "ec_csrf", "value": client.cookies.get("ec_csrf"), "url": "https://ecp.test",
    }])


@pytest.mark.parametrize("width,height", [(360, 800), (1024, 768)])
def test_browser_mehrfachuebernahme_mit_bestehendem_aktiven_auftrag(client, setup_db, seite, width, height, tmp_path):
    from playwright.sync_api import expect

    ids = _bestand(client)
    a, b, aktiv = ids["offen"]
    with _db() as db:
        d = db.get(EinheitSiteDispatch, aktiv)
        d.einheit_status = "in_arbeit"
        d.site.priority = SitePriority.normal
        db.get(LageEinheit, ids["e1"]).incident_site_id = d.site_id
        db.commit()
    page, state, errors = seite
    page.set_viewport_size({"width": width, "height": height})
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    for dispatch_id in (a, b):
        page.locator(f'[data-einheit-auftrag][data-dispatch-id="{dispatch_id}"] button').click()
        uebernehmen = page.get_by_role("button", name="Auftrag übernehmen", exact=True)
        expect(uebernehmen).to_be_enabled()
        expect(page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True)).to_be_visible()
        uebernehmen.click()
        expect(page.get_by_role("button", name="Anfahrt melden", exact=True)).to_be_enabled()
        expect(page.locator(".einheit-status-aktuell")).to_contain_text("Übernommen / bestätigt")
        assert page.locator('.person-flyout-backdrop:visible').count() == 0
        page.get_by_role("tab", name="Einsätze", exact=True).click()
    with _db() as db:
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in ids["offen"]] == [
            "bestaetigt", "bestaetigt", "in_arbeit",
        ]
        assert [db.get(EinheitSiteDispatch, d).reihenfolge for d in ids["offen"]] == [1, 2, 3]
        dringend = IncidentSite(org_id=db.get(IncidentSite, ids["a"]).org_id, major_incident_id=ids["lage"],
                                bezeichnung="Neuer dringender Auftrag", priority=SitePriority.sofort)
        db.add(dringend)
        db.flush()
        db.add(EinheitSiteDispatch(einheit_id=ids["e1"], site_id=dringend.id, dispatched_at=datetime.now(UTC)))
        db.commit()
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.locator('[data-hervorgehoben="true"]')).to_contain_text("Ohne Position")
    expect(page.get_by_text(
        "Neuer Auftrag mit höherer Priorität: Neuer dringender Auftrag", exact=True,
    )).to_be_visible()
    page.screenshot(path=str(tmp_path / f"mehrfachuebernahme-{width}.png"), full_page=True, animations="disabled")
    # Vorschau des bestätigten Auftrags und bewusste Entscheidung vor jedem Wechsel.
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{b}"] button').click()
    page.get_by_role("button", name="Anfahrt melden", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    expect(dialog).to_contain_text("Ohne Position")
    expect(dialog).to_contain_text("B")
    posts_vorher = len(state["posts"])
    dialog.get_by_role("button", name="Nein, Status nicht ändern", exact=True).click()
    assert len(state["posts"]) == posts_vorher
    page.get_by_role("button", name="Anfahrt melden", exact=True).click()
    dialog.get_by_role("button", name="Unterbrechen und wechseln", exact=True).click()
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("Aktiv · Anfahrt")
    with _db() as db:
        assert db.get(EinheitSiteDispatch, aktiv).einheit_status == "bestaetigt"
        assert db.get(EinheitSiteDispatch, b).einheit_status == "anfahrt"
        assert db.get(LageEinheit, ids["e1"]).incident_site_id == ids["b"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors


def test_browser_offline_zwei_uebernahmen_reload_und_spaetere_synchronisation(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    expect(page.locator("[data-einheit-auftrag]")).to_have_count(3)
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxVerfuegbar")
    state["offline"] = True
    for dispatch_id in ids["offen"][:2]:
        page.locator(f'[data-einheit-auftrag][data-dispatch-id="{dispatch_id}"] button').click()
        button = page.get_by_role("button", name="Auftrag übernehmen", exact=True)
        expect(button).to_be_enabled()
        button.click()
        expect(page.locator(".einheit-status-aktuell")).to_contain_text("nur auf diesem Gerät")
        page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.reload()
    expect(page.locator(".einheit-meta")).to_contain_text("2 noch nicht übermittelt")
    with _db() as db:
        assert all(db.get(EinheitSiteDispatch, d).einheit_status == "zugewiesen" for d in ids["offen"])
    state["offline"] = False
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    erneut = page.get_by_role("button", name="Erneut senden", exact=True)
    expect(erneut).to_have_count(2)
    erneut.first.click()
    expect(erneut).to_have_count(1)
    erneut.first.click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    with _db() as db:
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in ids["offen"]] == [
            "bestaetigt", "bestaetigt", "zugewiesen",
        ]
        assert db.get(LageEinheit, ids["e1"]).incident_site_id is None
    assert not errors


def test_browser_geaenderten_auftrag_pruefen_und_rueckzug_im_offenen_detail(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    state["schreiben_offline"] = True
    page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("nur auf diesem Gerät")
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.auftrag, d.version = "Neuer Auftrag – zuerst Nordtor prüfen", 2
        db.commit()
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.locator(".einheit-auftrag")).to_have_text("Neuer Auftrag – zuerst Nordtor prüfen")
    expect(page.get_by_text("Auftrag wurde geändert – bitte prüfen", exact=True)).to_be_visible()
    state["schreiben_offline"] = False
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    page.get_by_role("button", name="Erneut senden", exact=True).click()
    expect(page.locator('[data-einheit-verlauf="zentral"]').get_by_text(
        "Auftrag geändert – bitte aktuellen Auftrag prüfen und neu entscheiden", exact=True,
    )).to_be_visible()
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "zugewiesen"
    page.get_by_role("button", name="Auftrag prüfen", exact=True).click()
    page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("Übernommen / bestätigt")
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).withdrawn_at = datetime.now(UTC)
        db.commit()
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.get_by_text("Auftrag zurückgezogen – keine Statusaktion möglich.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Anfahrt melden", exact=True)).not_to_be_visible()
    assert not errors


def test_browser_verspaetete_vorschau_ersetzt_keine_neuere_auswahl(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    a, b = ids["offen"][:2]
    daten = client.get(f"/einheit/api/auftrag/{a}").json()
    page, _, errors = seite
    gehalten = []
    page.route(f"**/einheit/api/auftrag/{a}", lambda route: gehalten.append(route))
    page.goto("https://ecp.test/einheit")
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).geladen")
    page.evaluate(f"""() => {{
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        void c.oeffne({a}); void c.oeffne({b});
    }}""")
    expect(page.locator(".einheit-auftrag")).to_have_text("Zufahrt freimachen.")
    assert len(gehalten) == 1
    gehalten[0].fulfill(json=daten)
    expect(page.locator(".einheit-auftrag")).to_have_text("Zufahrt freimachen.")
    assert not errors


def test_browser_objekthinweise_zufahrt_und_plan_sind_vor_uebernahme_lesbar(
    client, setup_db, seite, tmp_path, monkeypatch,
):
    from playwright.sync_api import expect

    from app.config import settings
    from tests.test_einheit_vorschau import _plaene

    ids = _bestand(client)
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    _plaene(ids, tmp_path)
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).auftrag = "Vollständiger Auftrag " + "Wichtiger Abschnitt. " * 30
        db.commit()
    page, state, errors = seite
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    expect(page.locator(".einheit-auftrag")).to_have_text("Vollständiger Auftrag " + "Wichtiger Abschnitt. " * 30)
    expect(page.get_by_text("Schlüssel bei der BMZ", exact=True)).to_be_visible()
    expect(page.get_by_text("Zufahrt über Nordtor", exact=True)).to_be_visible()
    gefahren = page.get_by_role("region", name="Gefahren am Einsatzort")
    objektgefahr = gefahren.get_by_role("listitem").filter(has=page.get_by_text("Gasflaschen", exact=True))
    expect(objektgefahr).to_contain_text("Im Keller")
    expect(objektgefahr).to_contain_text("Schulgebäude")
    plan = page.get_by_role("link", name="Brandschutzplan 0", exact=True)
    expect(plan).to_be_visible()
    assert client.get(plan.get_attribute("href")).status_code == 200
    expect(page.get_by_role("button", name="Auftrag übernehmen", exact=True)).to_be_enabled()
    page.screenshot(path=str(tmp_path / "mehrfach-vorschau.png"), full_page=True, animations="disabled")
    assert not state["posts"] and not errors


def test_browser_neuer_serverstand_ersetzt_auch_laufende_aeltere_detailabfrage(client, setup_db, seite):
    from playwright.sync_api import expect

    from tests.test_einheit_mehrfachuebernahme import _status

    ids = _bestand(client)
    dispatch_id = ids["d1"]
    alt = client.get(f"/einheit/api/auftrag/{dispatch_id}").json()
    page, _, errors = seite
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{dispatch_id}"] button').click()
    expect(page.get_by_role("button", name="Auftrag übernehmen", exact=True)).to_be_enabled()
    gehalten = []

    def verzoegern(route):
        if not gehalten:
            gehalten.append(route)
        else:
            route.fallback()

    page.route(f"**/einheit/api/auftrag/{dispatch_id}", verzoegern)
    page.evaluate(f"void Alpine.$data(document.querySelector('.einheit-page')).oeffne({dispatch_id}, false, true)")
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).detailLaden")
    assert _status(client, dispatch_id, "bestaetigt").status_code == 200
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(page.get_by_role("button", name="Anfahrt melden", exact=True)).to_be_enabled()
    assert len(gehalten) == 1
    gehalten[0].fulfill(json=alt)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("Übernommen / bestätigt")
    assert not errors


def test_browser_geaenderter_auftrag_wird_nicht_aus_altem_abschlussdialog_beendet(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _bestand(client)
    page, state, errors = seite
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    page.get_by_text("Weitere Status", exact=True).click()
    page.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.version, d.auftrag = 2, "Zusätzliche Erkundung am Nordtor"
        db.commit()
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(dialog.get_by_text(
        "Auftrag geändert – Dialog schließen und aktuellen Auftrag prüfen.", exact=True,
    )).to_be_visible()
    expect(dialog.get_by_role("button", name="Erkundung abschließen", exact=True)).to_be_disabled()
    assert not state["posts"]
    dialog.locator(".person-flyout__close").click()
    expect(page.locator(".einheit-auftrag")).to_have_text("Zusätzliche Erkundung am Nordtor")
    page.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("keine_massnahmen")
    dialog.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    page.wait_for_url("https://ecp.test/einheit")
    page.wait_for_function("""Alpine.$data(document.querySelector('.einheit-page'))
        .outboxListe.length === 0""")
    expect(page.locator(".einheit-auftrag")).not_to_be_visible()
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "abgeschlossen"
        assert db.get(EinheitSiteDispatch, ids["d1"]).version == 2
    assert not errors
