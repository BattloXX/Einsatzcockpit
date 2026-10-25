"""Mobile Meldungserfassung mit echtem Alpine, IndexedDB und Einheiten-API."""
import pytest

from app.models.major_incident import EinheitSiteDispatch, SiteLogEntry, SiteMedia
from tests.test_einheit_ablauf_ui import _oeffnen, _vorbereiten
from tests.test_einheit_api import _db, _png
from tests.test_einheit_uebersicht import _csrf_im_browser
from tests.test_einheit_uebersicht import browser as browser
from tests.test_einheit_uebersicht import seite as seite


@pytest.fixture(autouse=True)
def bilddateien(tmp_path, monkeypatch):
    from app.services import lage_media_service
    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "lage_media"))


def _dialog(page):
    return page.locator(".einheit-page").get_by_role("dialog")


@pytest.mark.parametrize("art,label", [
    ("erkundung", "Maßnahmen erforderlich"), ("erkundung", "Keine Maßnahmen erforderlich"),
    ("erkundung", "Akute Gefahr festgestellt"), ("erkundung", "Weitere Erkundung erforderlich"),
    ("abarbeitung", "Arbeit läuft"), ("abarbeitung", "Unterstützung benötigt"),
])
@pytest.mark.parametrize("breite,hoehe", [(360, 800), (1024, 768)])
def test_normale_schnellmeldung_in_vier_eingaben_ohne_freitext(
    client, setup_db, seite, art, label, breite, hoehe, tmp_path,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, art)
    page, state, errors = seite
    page.set_viewport_size({"width": breite, "height": hoehe})
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    page.get_by_role("tab", name="Meldungen", exact=True).click()  # 1
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()  # 2
    dialog = _dialog(page)
    expect(dialog.get_by_role("heading", name="Meldung an Einsatzleitung", exact=True)).to_be_visible()
    expect(dialog.get_by_label("Einsatz", exact=True)).to_have_value(str(ids["d1"]))
    taste = dialog.get_by_role("button", name=label, exact=True)
    expect(taste).to_be_enabled()
    box = taste.bounding_box()
    assert box["width"] >= 48 and box["height"] >= 48
    taste.click()  # 3
    expect(taste).to_have_attribute("aria-pressed", "true")
    if label == "Maßnahmen erforderlich":
        page.screenshot(path=str(tmp_path / f"meldung-{breite}.png"), full_page=True, animations="disabled")
    senden = dialog.get_by_role("button", name="Erfassen", exact=True)
    expect(senden).to_be_enabled()
    box = senden.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= hoehe
    senden.evaluate("b => { b.click(); b.click(); }")  # 4, mit Doppeltippen.
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    assert state["posts"] == [f"/einheit/api/auftrag/{ids['d1']}/meldung"]
    with _db() as db:
        entry = db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").one()
        assert entry.text == label
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "zugewiesen"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors


def test_ergebnis_pflicht_und_offline_abschluss_mit_fotos_ueberlebt_neuladen(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung", "vor_ort")
    page, state, errors = seite
    haupt = _oeffnen(page, client, ids)
    expect(haupt).to_be_enabled()
    haupt.click()
    dialog = _dialog(page)
    fertig = dialog.get_by_role("button", name="Erkundung abschließen", exact=True)
    expect(fertig).to_be_disabled()
    dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("akute_gefahr")
    expect(fertig).to_be_disabled()
    dialog.get_by_label("Lage vor Ort", exact=True).fill("Gasgeruch im Keller")
    dialog.get_by_label("Fotos (optional)").set_input_files({
        "name": "gefahr.png", "mimeType": "image/png", "buffer": _png(),
    })
    expect(fertig).to_be_enabled()
    state["schreiben_offline"] = True
    fertig.evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    paket = page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).outboxListe")
    assert [e["typ"] for e in paket] == ["meldung", "foto", "status"]
    assert paket[-1]["payload"]["ergebnis_client_uuid"] == paket[0]["client_uuid"]
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "vor_ort"
    page.reload()
    page.wait_for_url("https://ecp.test/einheit")
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    expect(haupt).to_have_text("Übermittlung ausstehend")
    state["schreiben_offline"] = False
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    page.get_by_role("button", name="Jetzt synchronisieren", exact=True).click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "abgeschlossen"
        assert db.query(SiteMedia).filter_by(incident_site_id=ids["a"]).count() == 1
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").one().text == (
            "Akute Gefahr festgestellt\nGasgeruch im Keller"
        )
    assert not errors


def test_meldung_und_fotos_bleiben_beim_ausgewaehlten_einsatz_auch_waehrend_kompression(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "abarbeitung", "in_arbeit")
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    dialog.get_by_role("button", name="Arbeit läuft", exact=True).click()
    dialog.get_by_label("Fotos (optional)").set_input_files({
        "name": "arbeit.png", "mimeType": "image/png", "buffer": _png(),
    })
    expect(dialog.get_by_label("Einsatz", exact=True)).to_be_disabled()
    page.evaluate("""() => {
        window.compressFilesInPlace = () => new Promise(resolve => { window.fotoWeiter = resolve; });
    }""")
    dialog.get_by_role("button", name="Erfassen", exact=True).evaluate("b => { b.click(); b.click(); }")
    page.wait_for_function("Boolean(window.fotoWeiter)")
    page.evaluate("""async id => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        await c.oeffne(id, false);
        window.fotoWeiter();
    }""", ids["offen"][1])
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    assert sorted(state["posts"]) == sorted([
        f"/einheit/api/auftrag/{ids['d1']}/meldung", f"/einheit/api/auftrag/{ids['d1']}/foto",
    ])
    with _db() as db:
        assert db.query(SiteMedia).filter_by(incident_site_id=ids["a"]).count() == 1
        assert db.query(SiteMedia).filter_by(incident_site_id=ids["b"]).count() == 0
        assert db.query(SiteLogEntry).filter_by(kind="lagemeldung", incident_site_id=ids["a"]).count() == 1
    assert not errors


def test_speicherfehler_erhaelt_meldung_und_offline_wiederholung_erzeugt_nur_einen_eintrag(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung")
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    expect(dialog.get_by_role("button", name="Erfassen", exact=True)).to_be_disabled()
    dialog.get_by_role("button", name="Weitere Erkundung erforderlich", exact=True).click()
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Dachboden prüfen")
    page.evaluate("""() => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        window.paketOriginal = c.outbox.erfassenPaket;
        c.outbox.erfassenPaket = async () => { throw new Error('QuotaExceededError'); };
    }""")
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("Lokaler Speicher")
    expect(dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)")).to_have_value("Dachboden prüfen")
    assert not state["posts"]
    state["schreiben_offline"] = True
    page.evaluate("""() => {
        Alpine.$data(document.querySelector('.einheit-page')).outbox.erfassenPaket = window.paketOriginal;
    }""")
    dialog.get_by_role("button", name="Erfassen", exact=True).evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    page.reload()
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.get_by_role("button", name="Erneut senden", exact=True)).to_have_count(1)
    state["schreiben_offline"] = False
    page.get_by_role("button", name="Jetzt synchronisieren", exact=True).click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(kind="lagemeldung", incident_site_id=ids["a"]).one().text == (
            "Weitere Erkundung erforderlich\nDachboden prüfen"
        )
    assert not errors


@pytest.mark.parametrize("ziel", ["Auftrag erledigt", "Auftrag nicht durchführbar"])
def test_status_schnellmeldungen_nutzen_den_bestehenden_bestaetigungsdialog(client, setup_db, seite, ziel):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "abarbeitung", "vor_ort")
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    dialog.get_by_role("button", name=ziel, exact=True).click()
    assert not state["posts"]
    if ziel == "Auftrag erledigt":
        taste = dialog.get_by_role("button", name="Fertig melden", exact=True)
        status = "abgeschlossen"
    else:
        taste = dialog.get_by_role("button", name="Als nicht durchführbar melden", exact=True)
        expect(taste).to_be_disabled()
        dialog.get_by_placeholder("Grund (Pflichtfeld)").fill("Brücke gesperrt")
        status = "nicht_durchfuehrbar"
    expect(taste).to_be_enabled()
    taste.evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    assert state["posts"] == [f"/einheit/api/auftrag/{ids['d1']}/status"]
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == status
    assert not errors


def test_diktat_uebernimmt_text_ins_eingabemodell(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    page.add_init_script("""window.SpeechRecognition = class {
        start() { this.onresult({results: [[{transcript: 'Keller kontrolliert'}]]}); this.onend(); }
        stop() {}
    };""")
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    dialog.get_by_role("button", name="Diktieren", exact=True).click()
    expect(dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)")).to_have_value("Keller kontrolliert")
    expect(dialog.get_by_role("button", name="Erfassen", exact=True)).to_be_enabled()
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(
            kind="lagemeldung", incident_site_id=ids["a"],
        ).one().text == "Keller kontrolliert"
    assert not errors


def test_zielwechsel_laesst_textentwuerfe_und_auftragsarten_beim_richtigen_einsatz(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "erkundung")
    page, state, errors = seite
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    ziel = dialog.get_by_label("Einsatz", exact=True)
    expect(ziel).to_be_enabled()
    ziel.select_option(str(ids["offen"][1]))
    unterstuetzung = dialog.get_by_role("button", name="Unterstützung benötigt", exact=True)
    expect(unterstuetzung).to_be_enabled()
    unterstuetzung.click()
    text = dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)")
    text.fill("Weitere Pumpe benötigt")
    page.evaluate("""() => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        window.entwurfOriginal = c.outbox.entwurfSpeichern;
        c.outbox.entwurfSpeichern = async () => { throw new Error('QuotaExceededError'); };
    }""")
    ziel.select_option(str(ids["d1"]))
    expect(dialog.get_by_role("alert")).to_contain_text("Lokaler Speicher")
    expect(ziel).to_have_value(str(ids["offen"][1]))
    expect(text).to_have_value("Weitere Pumpe benötigt")
    page.evaluate("""() => {
        Alpine.$data(document.querySelector('.einheit-page')).outbox.entwurfSpeichern = window.entwurfOriginal;
    }""")
    ziel.select_option(str(ids["d1"]))
    expect(dialog.get_by_role("button", name="Akute Gefahr festgestellt", exact=True)).to_be_enabled()
    expect(text).to_have_value("")
    ziel.select_option(str(ids["offen"][1]))
    expect(text).to_have_value("Weitere Pumpe benötigt")
    expect(unterstuetzung).to_have_attribute("aria-pressed", "true")
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    assert state["posts"] == [f"/einheit/api/auftrag/{ids['offen'][1]}/meldung"]
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["b"], kind="lagemeldung").one().text == (
            "Unterstützung benötigt\nWeitere Pumpe benötigt"
        )
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").count() == 0
    assert not errors


def test_spaete_start_detailantwort_verdeckt_den_bewusst_gewaehlten_meldungsbereich_nicht(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    _csrf_im_browser(page, client)
    gehalten = []
    url = f"**/einheit/api/auftrag/{ids['d1']}"
    page.route(url, lambda route: gehalten.append(route))
    page.goto(f"https://ecp.test/einheit/auftrag/{ids['d1']}")
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).detailLaden")
    assert gehalten
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.get_by_role("button", name="Jetzt synchronisieren", exact=True)).to_be_visible()
    gehalten[0].fulfill(json=client.get(f"/einheit/api/auftrag/{ids['d1']}").json())
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(page.get_by_role("button", name="Jetzt synchronisieren", exact=True)).to_be_visible()
    assert page.evaluate("Alpine.$data(document.querySelector('.einheit-page')).detail") is None
    assert page.url.endswith("/einheit") and not state["posts"] and not errors


@pytest.mark.parametrize("aktion,status", [
    ("Auftrag erledigt", "abgeschlossen"), ("Auftrag nicht durchführbar", "nicht_durchfuehrbar"),
])
def test_statusmeldung_uebernimmt_ergaenzung_und_fotos_ohne_vorzeitigen_versand(
    client, setup_db, seite, aktion, status,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, "abarbeitung", "vor_ort")
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = _dialog(page)
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Keller kontrolliert")
    dialog.get_by_label("Fotos (optional)").set_input_files({
        "name": "abschluss.png", "mimeType": "image/png", "buffer": _png(),
    })
    dialog.get_by_role("button", name=aktion, exact=True).click()
    expect(dialog.get_by_label("Ergänzung (optional)")).to_have_value("Keller kontrolliert")
    expect(dialog.get_by_text("1 Foto(s) aus der Meldung übernommen", exact=True)).to_be_visible()
    dialog.get_by_label("Ergänzung (optional)").fill("Keller kontrolliert. Foto beigefügt.")
    if status == "nicht_durchfuehrbar":
        dialog.get_by_placeholder("Grund (Pflichtfeld)").fill("Zufahrt gesperrt")
    assert not state["posts"]
    taste = dialog.get_by_role("button", name=(
        "Fertig melden" if status == "abgeschlossen" else "Als nicht durchführbar melden"
    ), exact=True)
    expect(taste).to_be_enabled()
    taste.evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).outboxListe.length === 0")
    assert sorted(state["posts"]) == sorted([
        f"/einheit/api/auftrag/{ids['d1']}/meldung", f"/einheit/api/auftrag/{ids['d1']}/foto",
        f"/einheit/api/auftrag/{ids['d1']}/status",
    ])
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == status
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="lagemeldung").one().text == (
            "Keller kontrolliert. Foto beigefügt."
        )
        assert db.query(SiteMedia).filter_by(incident_site_id=ids["a"]).count() == 1
    assert not errors
