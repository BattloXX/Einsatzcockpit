"""Lesbarer Verlauf mit echten Browser-, API- und IndexedDB-Zuständen."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.major_incident import EinheitSiteDispatch, SiteLogEntry
from tests.test_einheit_ablauf_ui import _oeffnen, _vorbereiten
from tests.test_einheit_api import _db, _h, _png
from tests.test_einheit_uebersicht import browser as browser
from tests.test_einheit_uebersicht import seite as seite


@pytest.fixture(autouse=True)
def bilddateien(tmp_path, monkeypatch):
    from app.services import lage_media_service
    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "media"))


def _daten(client, anzahl=0):
    ids = _vorbereiten(client, "abarbeitung", "vor_ort")
    jetzt = datetime.now(UTC)
    with _db() as db:
        d = db.get(EinheitSiteDispatch, ids["d1"])
        d.dispatched_at = jetzt - timedelta(hours=4)
        d.geaendert_at, d.version = jetzt - timedelta(hours=3), 2
        for minute, text, unit, kind in [
            (12, "Ist die Zufahrt frei?", None, "note"),
            (10, "Akute Gefahr festgestellt\nGasgeruch am Keller", ids["e1"], "lagemeldung"),
            (8, "Unterstützung benötigt\nWeitere Pumpe", ids["e1"], "lagemeldung"),
            (6, "Gruppe Wolfurt: Vor Ort", ids["e1"], "einheit"),
            (4, "Interne Debug-Information id=987", None, "status"),
        ]:
            db.add(SiteLogEntry(incident_site_id=ids["a"], ts=jetzt - timedelta(minutes=minute),
                                text=text, einheit_id=unit, kind=kind))
        for i in range(anzahl):
            db.add(SiteLogEntry(incident_site_id=ids["a"], ts=jetzt - timedelta(hours=1),
                                text=f"Ältere Meldung {i:02d}", einheit_id=ids["e1"], kind="note"))
        db.commit()
    return ids


@pytest.mark.parametrize("breite,hoehe", [(360, 800), (768, 1024), (1024, 768)])
def test_mobile_chronologie_hervorhebungen_absender_bild_und_zentrale_uebersicht(
    client, setup_db, seite, breite, hoehe, tmp_path,
):
    from playwright.sync_api import expect

    ids = _daten(client)
    assert client.post(f"/einheit/api/auftrag/{ids['d1']}/foto", headers=_h(client),
                       data={"client_uuid": str(uuid4())}, files={"file": ("keller.png", _png(), "image/png")}
                       ).status_code == 200
    page, state, errors = seite
    page.set_viewport_size({"width": breite, "height": hoehe})
    _oeffnen(page, client, ids)
    verlauf = page.locator('[data-einheit-verlauf="stelle"]')
    expect(verlauf.locator("li")).to_have_count(7)
    zeiten = verlauf.locator("time").evaluate_all("nodes => nodes.map(n => Date.parse(n.dateTime))")
    assert zeiten == sorted(zeiten)
    expect(verlauf).to_contain_text("Einsatzleitung")
    expect(verlauf).to_contain_text("Eigene Einheit")
    expect(verlauf).to_contain_text("Übermittelt")
    expect(verlauf.locator(".einheit-meldung--rueckfrage")).to_contain_text("Ist die Zufahrt frei?")
    expect(verlauf.locator(".einheit-meldung--gefahr")).to_contain_text("Gasgeruch am Keller")
    expect(verlauf.locator(".einheit-meldung--unterstuetzung")).to_contain_text("Weitere Pumpe")
    expect(verlauf.locator(".einheit-verlauf__eintrag--kompakt")).to_have_count(1)
    expect(verlauf.locator("img")).to_have_count(1)
    expect(verlauf).not_to_contain_text("Debug")
    expect(verlauf).not_to_contain_text("Foto hochgeladen")
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    zentral = page.locator('[data-einheit-verlauf="zentral"]')
    expect(zentral.locator(".einheit-meldung--rueckfrage")).to_be_visible()
    expect(zentral.locator(".einheit-verlauf__eintrag--kompakt")).to_have_count(0)
    zeiten = zentral.locator("time").evaluate_all("nodes => nodes.map(n => Date.parse(n.dateTime))")
    assert zeiten == sorted(zeiten, reverse=True)
    for button in zentral.get_by_role("button").all():
        if button.is_visible():
            box = button.bounding_box()
            assert box["height"] >= 48 and box["width"] >= 48
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / f"meldungsverlauf-{breite}.png"), full_page=True, animations="disabled")
    assert not errors


def test_aeltere_meldungen_nachladen_und_live_refresh_ohne_doppelte_eintraege(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _daten(client, anzahl=45)
    page, state, errors = seite
    anfragen = []
    page.on("request", lambda r: anfragen.append(r.url) if "/api/meldungen?" in r.url else None)
    _oeffnen(page, client, ids)
    verlauf = page.locator('[data-einheit-verlauf="stelle"]')
    expect(verlauf.locator("li")).to_have_count(30)
    verlauf.get_by_role("button", name="Ältere Meldungen laden", exact=True).evaluate("b => { b.click(); b.click(); }")
    expect(verlauf.locator("li")).to_have_count(51)
    assert sum("cursor=" in url for url in anfragen) == 1
    with _db() as db:
        db.add(SiteLogEntry(incident_site_id=ids["a"], kind="note", text="Neue Rückfrage der Einsatzleitung?"))
        db.commit()
    pfad = page.url
    verlauf.get_by_role("button", name="Meldungen aktualisieren", exact=True).click()
    expect(verlauf.locator("li")).to_have_count(52)
    expect(verlauf.get_by_text("Neue Rückfrage der Einsatzleitung?", exact=True)).to_have_count(1)
    expect(verlauf.get_by_text("Ältere Meldung 00", exact=True)).to_have_count(1)
    zeiten = verlauf.locator("time").evaluate_all("nodes => nodes.map(n => Date.parse(n.dateTime))")
    assert zeiten == sorted(zeiten)
    assert page.url == pfad and not errors


def test_offline_verlauf_foto_und_spaetere_bestaetigung_ohne_doppelte_meldungen(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _daten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    verlauf = page.locator('[data-einheit-verlauf="stelle"]')
    expect(verlauf.get_by_text("Gasgeruch am Keller", exact=False)).to_be_visible()
    state["offline"] = state["schreiben_offline"] = True
    verlauf.get_by_role("button", name="Meldungen aktualisieren", exact=True).click()
    expect(verlauf).to_contain_text("Offline – zuletzt geladener Verlauf")
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    dialog.get_by_role("button", name="Arbeit läuft", exact=True).click()
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Offlinebericht zur Pumpe")
    dialog.get_by_label("Fotos (optional)").set_input_files({
        "name": "pumpe.png", "mimeType": "image/png", "buffer": _png(),
    })
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(verlauf.get_by_text("Arbeit läuft\nOfflinebericht zur Pumpe", exact=True)).to_have_count(1)
    expect(verlauf.locator('img[src^="blob:"]')).to_have_count(1)
    expect(verlauf).to_contain_text("lokal gespeichert")
    page.reload()
    expect(verlauf).to_contain_text("Offline – zuletzt geladener Verlauf")
    expect(verlauf.get_by_text("Arbeit läuft\nOfflinebericht zur Pumpe", exact=True)).to_have_count(1)
    expect(verlauf.get_by_text("Akute Gefahr festgestellt\nGasgeruch am Keller", exact=True)).to_be_visible()
    expect(verlauf.locator('img[src^="blob:"]')).to_have_count(1)
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    state["offline"] = state["schreiben_offline"] = False
    page.get_by_role("button", name="Jetzt synchronisieren", exact=True).click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    zentral = page.locator('[data-einheit-verlauf="zentral"]')
    zentral.get_by_role("button", name="Meldungen aktualisieren", exact=True).click()
    eintrag = zentral.locator("li").filter(has=page.get_by_text("Arbeit läuft\nOfflinebericht zur Pumpe", exact=True))
    expect(eintrag).to_have_count(1)
    expect(eintrag).to_contain_text("Übermittelt")
    expect(zentral.locator('img[src^="blob:"]')).to_have_count(0)
    expect(zentral.locator('img[src*="/einheit/medien/thumb/"]')).to_have_count(1)
    assert not errors


def test_uebertragungsfehler_werden_ohne_technische_codes_angezeigt_und_bleiben_bedienbar(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _daten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.route(f"**/einheit/api/auftrag/{ids['d1']}/meldung", lambda route: route.fulfill(
        status=422, json={"code": "debug_interner_fehler_123"},
    ))
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.locator(".einheit-page").get_by_role("dialog")
    dialog.get_by_role("button", name="Unterstützung benötigt", exact=True).click()
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog).not_to_be_visible()
    verlauf = page.locator('[data-einheit-verlauf="stelle"]')
    expect(verlauf.locator(".einheit-verlauf__eintrag--fehler")).to_contain_text("Übertragung fehlgeschlagen")
    expect(verlauf).not_to_contain_text("debug_interner_fehler_123")
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    expect(page.locator('[data-einheit-verlauf="zentral"] .einheit-verlauf__eintrag--fehler')).to_be_visible()
    page.unroute(f"**/einheit/api/auftrag/{ids['d1']}/meldung")
    page.get_by_role("button", name="Erneut senden", exact=True).click()
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    assert not errors


def test_gesperrter_verlauf_zeigt_keine_alten_cache_inhalte(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _daten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    verlauf = page.locator('[data-einheit-verlauf="stelle"]')
    expect(verlauf).to_contain_text("Gasgeruch am Keller")
    page.route("**/einheit/api/meldungen?*", lambda route: route.fulfill(status=403, json={"code": "gesperrt"}))
    verlauf.get_by_role("button", name="Meldungen aktualisieren", exact=True).click()
    expect(verlauf).to_contain_text("Meldungsverlauf nicht mehr freigegeben")
    expect(verlauf).not_to_contain_text("Gasgeruch am Keller")
    assert not errors


def test_offline_start_uebernimmt_keinen_zustand_einer_frueheren_anderen_einheit(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _daten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    expect(page.locator('[data-einheit-verlauf="stelle"]')).to_contain_text("Gasgeruch am Keller")
    page.wait_for_function("""async () => Boolean(
        (await Alpine.$data(document.querySelector('.einheit-page')).outbox.zustandLaden())?.data
    )""")
    page.evaluate("""async () => {
        const c = Alpine.$data(document.querySelector('.einheit-page'));
        const stand = await c.outbox.zustandLaden();
        stand.data.kopf.einheit_id += 100000;
        stand.data.kopf.einheit_label = 'Fremde Einheit geheim';
        await c.outbox.zustandSpeichern(stand);
    }""")
    state["offline"] = True
    page.goto("https://ecp.test/einheit")
    page.wait_for_function("Alpine.$data(document.querySelector('.einheit-page')).ladeFehler === 'laden'")
    expect(page.locator(".einheit-page")).not_to_contain_text("Fremde Einheit geheim")
    assert not errors
