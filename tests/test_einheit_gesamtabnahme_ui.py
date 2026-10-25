"""Phasen 1–6 zusammen: echte Bedienung, API, Service Worker und EL-Projektionen.

Keine externen SMS/Pushs; der lokale WS-Transport prüft Reconnect mit API-Abgleich.
Die produktive WS-Zugriffstrennung prüfen zusätzlich die bestehenden Routertests.
"""

import re
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from app.core.security import sign_session
from app.models.major_incident import (
    EinheitAktion,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    MajorIncident,
    SiteLogEntry,
    SiteMedia,
    SitePhase,
    SitePriority,
)
from app.models.master import OrgSettings
from app.services import gk_zugang_service
from tests.test_einheit_api import _als, _db, _h, _png
from tests.test_einheit_offline_ui import DATEN, _gesendet
from tests.test_einheit_offline_ui import medien_im_testverzeichnis as medien_im_testverzeichnis  # noqa: F401
from tests.test_einheit_offline_ui import offline_browser as offline_browser  # noqa: F401
from tests.test_einheit_uebersicht import _bestand
from tests.test_einheit_uebersicht import browser as browser  # noqa: F401
from tests.test_gsl_fuehrung_einheiten import _broadcasts, _post


@pytest.fixture
def leitung(client, monkeypatch):
    from app.services import gsl_auftrag_events

    async def kein_externer_versand(_):
        pass

    monkeypatch.setattr(gsl_auftrag_events, "sende_nach_commit", kein_externer_versand)
    board = TestClient(client.app)
    yield board
    board.close()


def _disponieren(leitung, ids, site_id, art, text, reihenfolge):
    response = _post(leitung, f"/lage/{ids['lage']}/stellen/{site_id}/einheit-disponieren", {
        "einheit_id": ids["e1"], "auftragsart": art, "auftrag": text, "reihenfolge": reihenfolge,
    })
    assert response.status_code == 200, response.text
    with _db() as db:
        return db.query(EinheitSiteDispatch).filter_by(einheit_id=ids["e1"], site_id=site_id).one().id


def _vorbereiten(client, leitung, profil, uebung):
    ids = _bestand(client)
    _als(leitung, sign_session(ids["admin"]))
    with _db() as db:
        lage = db.get(MajorIncident, ids["lage"])
        lage.is_exercise = uebung
        # Geodaten und Gefahren aus dem gemeinsamen Bestand; Zuweisungen erfolgen über die EL-API.
        auftraege = [(d.site_id, d.auftragsart, d.auftrag, d.reihenfolge) for d in (
            db.get(EinheitSiteDispatch, ident) for ident in ids["offen"]
        )]
        for dispatch in db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id.in_([ids["a"], ids["b"]])):
            if dispatch.einheit_id != ids["e1"]:
                dispatch.withdrawn_at = datetime.now(UTC)
        for ident in ids["offen"]:
            db.delete(db.get(EinheitSiteDispatch, ident))
        db.add(SiteLogEntry(incident_site_id=ids["a"], kind="lagemeldung", text="Fremde Einheit vertraulich",
                            einheit_id=db.get(EinheitSiteDispatch, ids["d2"]).einheit_id))
        db.get(IncidentSite, ids["a"]).phase = SitePhase.eingegangen
        db.commit()
    ids["offen"] = [_disponieren(leitung, ids, *auftrag) for auftrag in auftraege]
    ids["d1"] = ids["offen"][0]
    if profil == "simulation":
        _als(client, sign_session(ids["admin"]))
    elif profil in {"gk", "qr"}:
        with _db() as db:
            lage, einheit = db.get(MajorIncident, ids["lage"]), db.get(LageEinheit, ids["e1"])
            db.add(OrgSettings(org_id=lage.org_id, gk_zugang_aktiv=True, gk_zugang_sms_pin=False,
                               gk_qr_aktiv=True, gk_qr_pin=False))
            if profil == "gk":
                leader = LageEinheitLeader(einheit_id=einheit.id, person_name="Abnahme GK",
                                           phone_e164="+436641234567", phone_version=1,
                                           start_at=datetime.now(UTC), rolle="fuehrer")
                db.add(leader)
                db.flush()
                einheit.leader_assignment_id = leader.id
            db.flush()
            ausstellen = (gk_zugang_service.stelle_zugang_aus if profil == "gk"
                           else gk_zugang_service.stelle_qr_zugang_aus)
            neu = ausstellen(db, lage, einheit, user_id=ids["admin"], grund="Gesamtabnahme")
            token = neu.link.rsplit("#", 1)[1]
            db.commit()
        client.cookies.clear()
        client.get("/gk")
        response = client.post("/gk/einloesen", headers=_h(client), json={"token": token})
        assert response.status_code == 200, response.text
    return ids


def _auftrag(page, dispatch_id):
    page.get_by_role("tab", name="Einsätze", exact=True).click()
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{dispatch_id}"] button').click()
    page.wait_for_function("id => location.pathname === '/einheit/auftrag/' + id", arg=dispatch_id)


def _status(page, text, danach):
    from playwright.sync_api import expect

    haupt = page.locator("[data-einheit-hauptaktion]")
    expect(haupt).to_have_text(text)
    expect(haupt).to_be_enabled()
    box = haupt.bounding_box()
    assert box["width"] >= 48 and box["height"] >= 48
    pfad = page.url
    haupt.evaluate("button => { button.click(); button.click(); }")
    expect(haupt).to_have_text(danach)
    assert page.url == pfad
    _gesendet(page)


def _projektionen(leitung, ids, site_id, status, phase, ampel):
    board = leitung.get(f"/lage/{ids['lage']}")
    card = leitung.get(f"/lage/{ids['lage']}/stellen/{site_id}/card")
    ressourcen = leitung.get(f"/lage/{ids['lage']}/ressourcen/kraefteuebersicht")
    assert board.status_code == card.status_code == ressourcen.status_code == 200
    if phase in {SitePhase.bewertet, SitePhase.fertig_gemeldet}:
        # Fertige Einheiten werden automatisch abgezogen: kein aktiver Chip mehr, Abschluss bleibt im Detail.
        assert f"Gruppe Wolfurt · {status}" not in card.text
        assert ("Erkundet – disponieren?" if phase == SitePhase.bewertet else "Einsatz abschließen") in card.text
        detail = leitung.get(f"/lage/{ids['lage']}/stellen/{site_id}")
        assert detail.status_code == 200 and status in detail.text
    else:
        assert f"Gruppe Wolfurt · {status}" in card.text
    einheit = re.split(r'<div class="einh-card(?:[ "]|--)',
                       ressourcen.text.split(f'data-einheit-id="{ids["e1"]}"', 1)[1], maxsplit=1)[0]
    assert f'data-ampel="{ampel}"' in einheit
    with _db() as db:
        assert db.get(IncidentSite, site_id).phase == phase


@pytest.mark.parametrize("profil,uebung", [
    ("geraet", False), ("gk", False), ("qr", False),
    ("geraet", True), ("gk", True), ("qr", True), ("simulation", True),
])
@pytest.mark.parametrize("breite,hoehe", [(390, 844), (1024, 768)])
def test_gesamte_einsatzfolge_mit_drei_auftraegen_und_el_abgleich(
    client, setup_db, leitung, offline_browser, monkeypatch, tmp_path, profil, uebung, breite, hoehe,
):
    from playwright.sync_api import expect

    events = _broadcasts(monkeypatch)
    ids = _vorbereiten(client, leitung, profil, uebung)
    context, page, state, errors, starten, ws_trennen = offline_browser
    page.set_viewport_size({"width": breite, "height": hoehe})
    startpfad = f"/einheit?sim={ids['e1']}" if profil == "simulation" else "/einheit"
    starten(startpfad)
    kontext_header = {"X-EC-Einheit-Sim": str(ids["e1"])} if profil == "simulation" else {}
    for fremd in (ids["d2"], ids["fd"]):
        assert client.get(f"/einheit/api/auftrag/{fremd}", headers=kontext_header).status_code == 404
    expect(page.locator("[data-einheit-auftrag]")).to_have_count(3)
    if profil == "geraet" and not uebung:
        page.screenshot(path=str(tmp_path / f"gesamt-start-{breite}.png"), full_page=True, animations="disabled")
    # 1–3: Alle Aufträge vollständig öffnen, ohne Statusänderung; zwei davon übernehmen.
    for dispatch_id in ids["offen"]:
        _auftrag(page, dispatch_id)
        expect(page.locator(".einheit-auftrag")).to_be_visible()
    assert state["posts"] == []
    for dispatch_id in ids["offen"][:2]:
        _auftrag(page, dispatch_id)
        _status(page, "Auftrag übernehmen", "Anfahrt melden")
    with _db() as db:
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in ids["offen"]] == [
            "bestaetigt", "bestaetigt", "zugewiesen",
        ]
        assert db.get(LageEinheit, ids["e1"]).incident_site_id is None
    _projektionen(leitung, ids, ids["a"], "Auftrag bestätigt", SitePhase.erkundung, "im_einsatz")
    # 4–6: Erkundung, akute Gefahr mit eindeutig zugeordnetem Foto, aussagekräftiger Abschluss.
    _auftrag(page, ids["d1"])
    _status(page, "Anfahrt melden", "Vor Ort melden")
    _status(page, "Vor Ort melden", "Erkundungsergebnis erfassen")
    _projektionen(leitung, ids, ids["a"], "Vor Ort", SitePhase.erkundung, "am_einsatzort")
    expect(page.locator(".einheit-fuehrung")).to_contain_text("Stromleitung am Boden")
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Akute Gefahr festgestellt", exact=True).click()
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Gasflaschen im Keller. Abstand halten.")
    dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
        "name": "gefahr.png", "mimeType": "image/png", "buffer": _png(),
    })
    dialog.get_by_role("button", name="Erfassen", exact=True).evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    _gesendet(page)
    card = leitung.get(f"/lage/{ids['lage']}/stellen/{ids['a']}/card")
    assert "einheit-meldung--gefahr" in card.text and "Gasflaschen im Keller" in card.text
    page.get_by_role("button", name="Erkundungsergebnis erfassen", exact=True).click()
    dialog.get_by_label("Erkundungsergebnis (Pflichtfeld)").select_option("massnahmen_erforderlich")
    expect(dialog.get_by_role("button", name="Erkundung abschließen", exact=True)).to_be_disabled()
    dialog.get_by_label("Lage vor Ort", exact=True).fill(
        "Gebäude kontrolliert. Gasflaschen gesichert; Keller überflutet.",
    )
    dialog.get_by_label("Notwendige Maßnahmen (optional)", exact=True).fill("Wasser abpumpen.")
    dialog.get_by_role("button", name="Erkundung abschließen", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.wait_for_function("location.pathname === '/einheit'")
    expect(page.locator(".einheit-auftrag")).not_to_be_visible()
    if profil == "simulation":
        assert page.url.endswith(f"/einheit?sim={ids['e1']}")
    _gesendet(page)
    _projektionen(leitung, ids, ids["a"], "Erkundet", SitePhase.bewertet, "im_einsatz")
    # 7: Bereits bestätigte Abarbeitung aktivieren, keine zweite primäre Bearbeitung.
    _auftrag(page, ids["offen"][1])
    _status(page, "Anfahrt melden", "Vor Ort melden")
    _status(page, "Vor Ort melden", "In Arbeit setzen")
    _status(page, "In Arbeit setzen", "Fertig melden")
    _projektionen(leitung, ids, ids["b"], "In Arbeit", SitePhase.in_arbeit, "am_einsatzort")
    # 8–9: EL disponiert dringend und ändert einen anderen Auftrag über ihre echten Routen.
    with _db() as db:
        site = db.get(IncidentSite, ids["a"])
        dringend = IncidentSite(major_incident_id=ids["lage"], org_id=site.org_id,
                                bezeichnung="Menschenrettung", strasse="Bahnhofstraße 9",
                                priority=SitePriority.sofort, lat=47.001, lng=9.001)
        db.add(dringend)
        db.commit()
        dringend_id = dringend.id
        dritter_site = db.get(EinheitSiteDispatch, ids["offen"][2]).site_id
    dringend_dispatch = _disponieren(leitung, ids, dringend_id, "abarbeitung", "Person aus Keller retten.", 1)
    response = _post(leitung, f"/lage/{ids['lage']}/stellen/{dritter_site}/einheit/{ids['offen'][2]}/auftrag", {
        "auftrag": "Material am Südtor bereitstellen. Nördliche Zufahrt gesperrt.", "reihenfolge": 3,
    })
    assert response.status_code == 200
    ws_trennen()  # WebSocket-Reconnect muss Änderungen nachladen, ohne die Bearbeitung zu wechseln.
    page.wait_for_function(f"{DATEN}.kopf.offen === 3")
    page.wait_for_function(f"{DATEN}.weitere.some(a => a.auftrag.includes('Südtor'))")
    assert page.evaluate(f"{DATEN}.aktuell.dispatch_id") == ids["offen"][1]
    weitere = page.evaluate(f"{DATEN}.weitere")
    assert [a["dispatch_id"] for a in weitere] == [dringend_dispatch, ids["offen"][2]]
    assert weitere[0]["priority"] == "sofort"
    expect(page.locator("[data-einheit-hauptaktion]")).to_have_text("Fertig melden")
    if profil == "geraet" and not uebung:
        page.screenshot(path=str(tmp_path / f"gesamt-auftrag-{breite}.png"), full_page=True, animations="disabled")
    # 10–12: Tatsächlicher Netzausfall, lokale Meldung, Offline-Neustart und automatische Übertragung.
    context.set_offline(True)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog.get_by_role("button", name="Unterstützung benötigt", exact=True).click()
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Weitere Pumpe am Südtor benötigt.")
    dialog.get_by_role("button", name="Erfassen", exact=True).evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    _gesendet(page, 1)
    expect(page.locator(".einheit-meta")).to_contain_text("1 noch nicht übermittelt")
    expect(page.locator('[data-einheit-verlauf="stelle"]')).to_contain_text("nur lokal gespeichert")
    expect(page.locator(".einheit-meta")).not_to_contain_text("Alles übermittelt")
    with _db() as db:
        assert db.query(SiteLogEntry).filter_by(
            einheit_id=ids["e1"], incident_site_id=ids["b"], kind="lagemeldung",
        ).count() == 0
    page.reload()
    _gesendet(page, 1)
    context.set_offline(False)
    _gesendet(page)
    expect(page.locator(".einheit-meta")).to_contain_text("Alles übermittelt")
    card = leitung.get(f"/lage/{ids['lage']}/stellen/{ids['b']}/card")
    assert "einheit-meldung--unterstuetzung" in card.text and "Weitere Pumpe" in card.text
    # 13–14: Fertigmeldung ist kein endgültiger EL-Abschluss und macht andere Aufträge nicht fertig.
    page.get_by_role("button", name="Fertig melden", exact=True).click()
    dialog.get_by_role("button", name="Fertig melden", exact=True).evaluate("b => { b.click(); b.click(); }")
    expect(dialog).not_to_be_visible()
    expect(page.locator("[data-einheit-hauptaktion]")).to_have_text("Fertig gemeldet")
    _gesendet(page)
    _projektionen(leitung, ids, ids["b"], "Auftrag abgeschlossen", SitePhase.fertig_gemeldet, "im_einsatz")
    with _db() as db:
        assert db.get(LageEinheit, ids["e1"]).incident_site_id is None
        assert [db.get(EinheitSiteDispatch, d).einheit_status for d in ids["offen"]] == [
            "abgeschlossen", "abgeschlossen", "zugewiesen",
        ]
        assert [db.get(EinheitSiteDispatch, d).reihenfolge for d in ids["offen"]] == [1, 2, 3]
        assert db.get(EinheitSiteDispatch, ids["offen"][2]).version == 2
        assert db.get(EinheitSiteDispatch, dringend_dispatch).einheit_status == "zugewiesen"
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"], incident_site_id=ids["a"]).count() == 1
        assert db.query(SiteLogEntry).filter_by(einheit_id=ids["e1"], kind="lagemeldung").count() == 3
        aktionen = db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).all()
        assert len(aktionen) == 13 and len({a.client_uuid for a in aktionen}) == 13
    page.get_by_role("tab", name="Meldungen", exact=True).click()
    verlauf = page.locator('[data-einheit-verlauf="zentral"]')
    unterstuetzung = verlauf.locator("li").filter(has_text="Weitere Pumpe am Südtor benötigt.")
    expect(unterstuetzung).to_have_count(1)
    expect(unterstuetzung).to_contain_text("Unterstützung benötigt")
    expect(verlauf).not_to_contain_text("Fremde Einheit vertraulich")
    expect(verlauf).to_contain_text("Übermittelt")
    response = client.get("/einheit/api/meldungen", headers=kontext_header)
    assert response.status_code == 200 and "Fremde Einheit vertraulich" not in response.text
    meldungen = [r for r in response.json()["eintraege"] if r["art"] == "meldung" and r["eigene"]]
    assert len(meldungen) == 3 and all(r["status"] == "uebermittelt" for r in meldungen)
    assert {r["dispatch_id"] for r in meldungen} == set(ids["offen"][:2])
    assert len(state["posts"]) == 13 and all(status == 200 for _, status in state["posts"])
    assert {"site:card_changed", "einheit:changed", "site_phase_changed"} <= {e["type"] for e in events}
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors


@pytest.mark.parametrize("breite,hoehe", [(390, 844), (1024, 768)])
def test_simulation_im_echteinsatz_bleibt_lesend(client, setup_db, leitung, offline_browser, breite, hoehe):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, leitung, "simulation", False)
    _, page, state, errors, starten, _ = offline_browser
    page.set_viewport_size({"width": breite, "height": hoehe})
    starten(f"/einheit?sim={ids['e1']}")
    _auftrag(page, ids["d1"])
    expect(page.locator("[data-einheit-hauptaktion]")).to_be_disabled()
    assert page.evaluate(f"{DATEN}.schreibbar") is False
    assert state["posts"] == [] and not errors


def test_freitext_und_gefahrenangaben_bleiben_nach_offline_neustart_erhalten(
    client, setup_db, offline_browser,
):
    from playwright.sync_api import expect

    ids = _bestand(client)
    context, page, state, errors, starten, _ = offline_browser
    starten()
    _auftrag(page, ids["d1"])
    context.set_offline(True)
    page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Ergänzung (optional bei Schnellmeldung)").fill("Keller kontrolliert.")
    dialog.locator("summary").filter(has_text="Strukturierte Angaben").click()
    dialog.get_by_placeholder("Festgestellte Gefahren", exact=True).fill("Offene Stromleitung im Wasser.")
    dialog.get_by_role("button", name="Erfassen", exact=True).click()
    expect(dialog).not_to_be_visible()
    _gesendet(page, 1)
    page.reload()
    _gesendet(page, 1)
    context.set_offline(False)
    _gesendet(page)
    with _db() as db:
        meldung = db.query(SiteLogEntry).filter_by(einheit_id=ids["e1"], kind="lagemeldung").one()
        assert meldung.text == "Keller kontrolliert.\nFestgestellte Gefahren: Offene Stromleitung im Wasser."
    expect(page.locator('[data-einheit-verlauf="stelle"]')).to_contain_text("Offene Stromleitung im Wasser.")
    assert len(state["posts"]) == 1 and errors == []
