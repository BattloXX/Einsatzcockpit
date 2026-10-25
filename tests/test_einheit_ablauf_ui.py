"""Geführte Bedienung mit echtem Alpine, IndexedDB und der bestehenden Einheiten-API."""
import pytest

from app.models.major_incident import EinheitSiteDispatch, SiteLogEntry, SitePhase
from tests.test_einheit_api import _als, _db, _device, _png
from tests.test_einheit_uebersicht import _bestand, _csrf_im_browser
from tests.test_einheit_uebersicht import browser as browser
from tests.test_einheit_uebersicht import seite as seite


@pytest.fixture(autouse=True)
def bilddateien_im_testverzeichnis(tmp_path, monkeypatch):
    from app.services import lage_media_service

    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "lage_media"))


def _vorbereiten(client, art="abarbeitung", status="zugewiesen"):
    ids = _bestand(client)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.auftragsart, d.einheit_status = art, status
        d.site.phase = SitePhase.disponiert
        if status in {"anfahrt", "vor_ort", "in_arbeit"}:
            d.einheit.incident_site_id = d.site_id
        db.commit()
    return ids


def _oeffnen(page, client, ids):
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    return page.locator("[data-einheit-hauptaktion]")


def test_browser_ergebnisdialog_oeffnet_auch_bei_hintergrundabgleich_waehrend_des_tipps(
    client, setup_db, seite,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, art="erkundung", status="vor_ort")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Erkundungsergebnis erfassen")
    expect(haupt).to_be_enabled()
    gehalten = []
    page.route(f"**/einheit/api/auftrag/{ids['d1']}", lambda route: gehalten.append(route))

    # Der automatische Abgleich beginnt zwischen Drücken und Loslassen.
    # Ein bereits geladener Auftrag muss dabei weiter bedienbar bleiben.
    haupt.scroll_into_view_if_needed()
    box = haupt.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.evaluate(f"void Alpine.$data(document.querySelector('.einheit-page')).oeffne({ids['d1']}, false, true)")
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).detailLaden")
    page.mouse.up()

    try:
        dialog = page.get_by_role("dialog")
        expect(dialog).to_have_accessible_name("Erkundungsergebnis")
        expect(dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)")).to_be_enabled()
        assert len(gehalten) == 1 and not state["posts"] and not errors
    finally:
        page.unroute(f"**/einheit/api/auftrag/{ids['d1']}")
        for route in gehalten:
            route.fallback()


@pytest.mark.parametrize("art", ["abarbeitung", "erkundung"])
@pytest.mark.parametrize("breite,hoehe", [(360, 800), (1024, 768)])
def test_browser_vollstaendige_gefuehrte_folge_bleibt_im_auftrag(
    client, setup_db, seite, art, breite, hoehe, tmp_path,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, art)
    page, state, errors = seite
    page.set_viewport_size({"width": breite, "height": hoehe})
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_count(1)
    pfad = page.url
    assert not state["posts"]
    for text in ["Auftrag übernehmen", "Anfahrt melden", "Vor Ort melden"]:
        expect(haupt).to_have_text(text)
        expect(haupt).to_be_enabled()
        haupt.click()
        assert page.url == pfad
        expect(page.locator(".einheit-page").get_by_role("dialog")).not_to_be_visible()
    if art == "abarbeitung":
        expect(haupt).to_have_text("In Arbeit setzen")
        expect(haupt).to_be_enabled()
        haupt.click()
        expect(haupt).to_have_text("Fertig melden")
    else:
        expect(haupt).to_have_text("Erkundungsergebnis erfassen")
    expect(haupt).to_be_enabled()
    page.locator(".einheit-auftrag").scroll_into_view_if_needed()
    box = haupt.bounding_box()
    assert box and box["y"] >= 0 and box["y"] + box["height"] <= hoehe
    assert box["width"] >= 48 and box["height"] >= 56
    page.screenshot(path=str(tmp_path / f"ablauf-{art}-{breite}.png"), full_page=True, animations="disabled")
    vorher = len(state["posts"])
    haupt.click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    expect(dialog).to_be_visible()
    assert len(state["posts"]) == vorher  # Öffnen von Ergebnis/Bestätigung schreibt keinen Status.
    if art == "erkundung":
        expect(dialog.get_by_role("heading", name="Erkundungsergebnis", exact=True)).to_be_visible()
        dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("massnahmen_erforderlich")
        dialog.get_by_label("Lage vor Ort", exact=True).fill("Alle Räume kontrolliert")
        dialog.get_by_label("Notwendige Maßnahmen (optional)", exact=True).fill("Wasser abpumpen")
        dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
            "name": "erkundung.png", "mimeType": "image/png", "buffer": _png(),
        })
    abschluss = dialog.get_by_role(
        "button", name="Erkundung abschließen" if art == "erkundung" else "Fertig melden", exact=True,
    )
    expect(abschluss).to_be_enabled()
    abschluss.evaluate("button => { button.click(); button.click(); }")
    expect(haupt).to_have_text("Erkundet" if art == "erkundung" else "Fertig gemeldet")
    expect(haupt).to_be_disabled()
    assert page.url == pfad
    status_url = f"/einheit/api/auftrag/{ids['d1']}/status"
    assert state["posts"].count(status_url) == (4 if art == "erkundung" else 5)
    if art == "erkundung":
        page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
        assert state["posts"].count(f"/einheit/api/auftrag/{ids['d1']}/meldung") == 1
        assert state["posts"].count(f"/einheit/api/auftrag/{ids['d1']}/foto") == 1
    assert all(url.startswith(f"/einheit/api/auftrag/{ids['d1']}/") for url in state["posts"])
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "abgeschlossen"
        if art == "erkundung":
            assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors


def test_browser_doppeltippen_erzeugt_nur_einen_schritt_und_keinen_folgeschritt(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_be_enabled()
    haupt.evaluate("button => { button.click(); button.click(); }")
    expect(haupt).to_have_text("Anfahrt melden")
    expect(haupt).to_be_enabled()
    assert state["posts"] == [f"/einheit/api/auftrag/{ids['d1']}/status"]
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "bestaetigt"
    assert not errors


def test_browser_offline_trennt_serverstand_von_lokal_vorgemerktem_schritt(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_be_enabled()
    pfad = page.url
    state["schreiben_offline"] = True
    haupt.click()
    expect(haupt).to_have_text("Übermittlung ausstehend")
    expect(haupt).to_be_disabled()
    stand = page.locator(".einheit-status-aktuell")
    expect(stand).to_contain_text("Auftrag: Zugewiesen")
    expect(stand).to_contain_text("Lokal vorgemerkt: Übernommen / bestätigt – noch nicht übermittelt")
    expect(stand).to_contain_text("Einheitenstatus:")
    haupt.evaluate("button => { button.click(); button.click(); }")
    assert page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length") == 1
    assert page.url == pfad
    page.reload()
    expect(haupt).to_have_text("Übermittlung ausstehend")
    state["schreiben_offline"] = False
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    page.get_by_role("button", name="Erneut senden", exact=True).click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    expect(haupt).to_have_text("Anfahrt melden")
    assert not errors


def test_browser_ergebnis_speicherfehler_bewahrt_eingaben_und_wiederholung_speichert_ein_paket(
    client, setup_db, seite,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung", "vor_ort")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Erkundungsergebnis erfassen")
    haupt.click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    lage = dialog.get_by_label("Lage vor Ort", exact=True)
    dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("massnahmen_erforderlich")
    lage.fill("Ergebnis bleibt erhalten")
    dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
        "name": "beweis.png", "mimeType": "image/png", "buffer": _png(),
    })
    page.evaluate("""() => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        window.originalErfassenPaket = c.outbox.erfassenPaket;
        c.outbox.erfassenPaket = async () => { throw new Error('QuotaExceededError'); };
    }""")
    speichern = dialog.get_by_role("button", name="Erkundung abschließen", exact=True)
    speichern.evaluate("button => { button.click(); button.click(); }")
    expect(dialog.get_by_role("alert")).to_contain_text("Lokaler Speicher")
    expect(lage).to_have_value("Ergebnis bleibt erhalten")
    assert dialog.locator("#erkundung-fotos").evaluate("input => input.files.length") == 1
    assert page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length") == 0
    assert not state["posts"]
    state["schreiben_offline"] = True
    page.evaluate("""() => {
        Alpine.$data(document.querySelector('.einheit-page')).outbox.erfassenPaket = window.originalErfassenPaket;
    }""")
    speichern.evaluate("button => { button.click(); button.click(); }")
    expect(dialog).not_to_be_visible()
    paket = page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).outboxListe")
    assert [item["typ"] for item in paket] == ["meldung", "foto", "status"]
    assert len({item["client_uuid"] for item in paket}) == 3
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "vor_ort"
    assert not errors


def test_browser_boardstatus_aktualisiert_hauptaktion_ohne_seitenwechsel(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.core.security import sign_session
    from tests.test_gsl_fuehrung_einheiten import _post

    ids = _vorbereiten(client)
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Auftrag übernehmen")
    pfad = page.url
    _als(client, sign_session(ids["admin"]))
    response = _post(client, f"/lage/{ids['lage']}/stellen/{ids['a']}/einheit/{ids['d1']}/status",
                     {"status": "vor_ort"})
    assert response.status_code == 200
    _device(client, ids)
    _csrf_im_browser(page, client)
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    expect(haupt).to_have_text("In Arbeit setzen")
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("Aktiv · Vor Ort")
    assert page.url == pfad and not state["posts"] and not errors


def test_browser_unterbrechen_und_nicht_durchfuehrbar_verlangen_bewusste_entscheidung(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, status="in_arbeit")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Fertig melden")
    page.get_by_text("Weitere Status", exact=True).click()
    page.get_by_role("button", name="Bearbeitung unterbrechen", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    expect(dialog).to_contain_text("Der Auftrag bleibt übernommen")
    assert not state["posts"]
    dialog.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_text("Weitere Aktionen", exact=True).click()
    page.get_by_role("button", name="Nicht durchführbar", exact=True).click()
    bestaetigen = dialog.get_by_role("button", name="Als nicht durchführbar melden", exact=True)
    expect(bestaetigen).to_be_disabled()
    dialog.get_by_placeholder("Grund (Pflichtfeld)").fill("Zufahrt unbefahrbar")
    expect(bestaetigen).to_be_enabled()
    bestaetigen.evaluate("button => { button.click(); button.click(); }")
    expect(haupt).to_have_text("Nicht durchführbar")
    expect(haupt).to_be_disabled()
    assert state["posts"] == [f"/einheit/api/auftrag/{ids['d1']}/status"] and not errors


def test_browser_phasensperre_zeigt_eindeutige_hauptaktion_ohne_verbotenen_uebergang(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, status="vor_ort")
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).site.phase = SitePhase.erkundung
        db.commit()
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Freigabe ausstehend")
    expect(haupt).to_be_disabled()
    expect(page.locator("#einheit-aktionshinweis")).to_contain_text("Bewertung durch die Einsatzleitung")
    page.get_by_text("Weitere Status", exact=True).click()
    expect(page.get_by_role("button", name="In Arbeit setzen", exact=True)).not_to_be_visible()
    expect(page.get_by_role("button", name="Fertig melden", exact=True)).not_to_be_visible()
    haupt.evaluate("button => button.click()")
    assert not state["posts"] and not errors


@pytest.mark.parametrize("terminal", ["abgeschlossen", "zurueckgezogen"])
def test_browser_beendeter_auftrag_hat_nur_eine_gesperrte_hauptaktion(client, setup_db, seite, terminal):
    from datetime import UTC, datetime

    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        if terminal == "abgeschlossen":
            d.einheit_status, d.beendet_at = terminal, datetime.now(UTC)
        else:
            d.withdrawn_at = datetime.now(UTC)
        db.commit()
    page, state, errors = seite
    page.goto(f"https://ecp.test/einheit/auftrag/{ids['d1']}")
    haupt = page.locator("[data-einheit-hauptaktion]")
    expect(haupt).to_have_count(1)
    expect(haupt).to_have_text("Fertig gemeldet" if terminal == "abgeschlossen" else "Auftrag zurückgezogen")
    expect(haupt).to_be_disabled()
    assert not state["posts"] and not errors


def test_browser_lesende_simulation_zeigt_stand_aber_keine_statusaktion(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.core.security import sign_session

    ids = _vorbereiten(client)
    _als(client, sign_session(ids["admin"]))
    page, state, errors = seite
    page.goto(f"https://ecp.test/einheit/auftrag/{ids['d1']}?sim={ids['e1']}")
    haupt = page.locator("[data-einheit-hauptaktion]")
    expect(haupt).to_have_text("Nur lesen")
    expect(haupt).to_be_disabled()
    expect(page.locator(".einheit-status-aktuell")).to_contain_text("Auftrag: Zugewiesen")
    assert not state["posts"] and not errors


def test_browser_ergebnis_entwuerfe_bleiben_beim_richtigen_auftrag(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung", "vor_ort")
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["offen"][1]).auftragsart = "erkundung"
        db.commit()
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Erkundungsergebnis erfassen")
    haupt.click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    dialog.get_by_label("Lage vor Ort", exact=True).fill("Ergebnis gehört zur Schule")
    dialog.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    b = ids["offen"][1]
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{b}"] button').click()
    page.get_by_text("Weitere Status", exact=True).click()
    page.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    expect(dialog.get_by_label("Lage vor Ort", exact=True)).to_have_value("")
    dialog.get_by_role("button", name="Schließen", exact=True).click()
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    haupt.click()
    expect(dialog.get_by_label("Lage vor Ort", exact=True)).to_have_value("Ergebnis gehört zur Schule")
    assert not state["posts"] and not errors


def test_browser_board_aenderung_im_abschlussdialog_verhindert_ueberholten_abschluss(client, setup_db, seite):
    from playwright.sync_api import expect

    from app.core.security import sign_session
    from tests.test_gsl_fuehrung_einheiten import _post

    ids = _vorbereiten(client, status="in_arbeit")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Fertig melden")
    haupt.click()
    _als(client, sign_session(ids["admin"]))
    response = _post(client, f"/lage/{ids['lage']}/stellen/{ids['a']}/einheit/{ids['d1']}/status",
                     {"status": "vor_ort"})
    assert response.status_code == 200
    _device(client, ids)
    _csrf_im_browser(page, client)
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    expect(dialog.get_by_role("button", name="Fertig melden", exact=True)).to_be_disabled()
    expect(dialog).to_contain_text("aktuellen Auftrag prüfen")
    assert not state["posts"]
    dialog.get_by_role("button", name="Schließen", exact=True).click()
    expect(haupt).to_have_text("In Arbeit setzen")
    assert not errors


def test_browser_entzogene_phasenfreigabe_sperrt_auch_geoeffneten_abschlussdialog(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, status="in_arbeit")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_have_text("Fertig melden")
    haupt.click()
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).site.phase = SitePhase.erkundung
        db.commit()
    page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).laden()")
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    bestaetigen = dialog.get_by_role("button", name="Fertig melden", exact=True)
    expect(bestaetigen).to_be_disabled()
    bestaetigen.evaluate("button => button.click()")
    assert not state["posts"]
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "in_arbeit"
    assert not errors
