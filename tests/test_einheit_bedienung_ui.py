"""Standardoberfläche für wenig geübte Anwender: Touch, Kontrast, Orientierung und sichere Formulare."""

import pytest

from app.config import settings
from app.models.major_incident import EinheitSiteDispatch
from tests.test_einheit_ablauf_ui import _oeffnen, _vorbereiten
from tests.test_einheit_api import _db, _png
from tests.test_einheit_uebersicht import _csrf_im_browser
from tests.test_einheit_uebersicht import browser as browser  # noqa: F401
from tests.test_einheit_uebersicht import seite as seite  # noqa: F401
from tests.test_einheit_vorschau import _plaene

DATEN = "Alpine.$data(document.querySelector('.einheit-page'))"

KONTRAST = """el => {
  const rgba = s => s.match(/[\\d.]+/g).map(Number);
  const mix = (a, b) => a.slice(0, 3).map((v, i) => v * (a[3] ?? 1) + b[i] * (1 - (a[3] ?? 1)));
  const pfad = []; for (let n = el; n; n = n.parentElement) pfad.unshift(n);
  let bg = [255, 255, 255];
  for (const n of pfad) bg = mix(rgba(getComputedStyle(n).backgroundColor), bg);
  const fg = mix(rgba(getComputedStyle(el).color), bg);
  const l = c => c.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  return (Math.max(l(bg), l(fg)) + .05) / (Math.min(l(bg), l(fg)) + .05);
}"""


def _touchpruefung(page, bereich=".einheit-page"):
    klein = page.locator(bereich).evaluate("""root => [...root.querySelectorAll(
      'button, a[href], summary, input:not(.sr-only):not([type=checkbox]):not([type=radio]),'
      + ' select, textarea, label.einheit-checkbox'
    )].filter(el => el.getClientRects().length && !el.closest('[inert]')).map(el => {
      const r = el.getBoundingClientRect();
      return { text: el.textContent.trim().slice(0, 60) || el.getAttribute('aria-label') || el.type,
               width: r.width, height: r.height };
    }).filter(r => r.width < 48 || r.height < 48)""")
    assert klein == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def _eine_hauptaktion(page):
    assert page.locator(".einheit-page").evaluate("""root => [...root.querySelectorAll('.btn--primary,.btn--danger')]
      .filter(el => el.getClientRects().length && !el.closest('[inert]')).length""") == 1


@pytest.mark.parametrize("schema", ["dark", "light"])
@pytest.mark.parametrize("breite,hoehe", [(360, 800), (390, 844), (1024, 600)])
def test_standardansichten_haben_eine_hauptaktion_grosse_touchflaechen_und_lesbaren_kontrast(
    client, setup_db, seite, schema, breite, hoehe, tmp_path,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    page.emulate_media(color_scheme=schema)
    page.set_viewport_size({"width": breite, "height": hoehe})
    _csrf_im_browser(page, client)
    page.goto("https://ecp.test/einheit")
    expect(page.locator('[data-einheit-auftrag]')).to_have_count(3)
    for tab in page.get_by_role("tab").all():
        assert tab.evaluate("""el => [...el.childNodes]
          .filter(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim())
          .every(n => { const r = document.createRange();
            r.setStart(n, n.textContent.search(/\\S/)); r.setEnd(n, n.textContent.trimEnd().length);
            return r.getClientRects().length === 1; })""")  # Jeder Bereichsname bleibt in einer Zeile lesbar.
    _eine_hauptaktion(page)
    _touchpruefung(page)
    expect(page.get_by_role("link", name="Gesamtansicht", exact=True)).to_have_count(0)
    page.get_by_text("Weitere Funktionen", exact=True).click()
    expect(page.get_by_role("link", name="Gesamtansicht", exact=True)).to_have_count(0)
    _touchpruefung(page)
    page.get_by_text("Weitere Funktionen", exact=True).click()
    page.locator(f'[data-einheit-auftrag][data-dispatch-id="{ids["d1"]}"] button').click()
    expect(page.locator('[data-einheit-hauptaktion]')).to_be_enabled()
    _eine_hauptaktion(page)
    _touchpruefung(page)
    expect(page.get_by_role("button", name="Notiz", exact=True)).not_to_be_visible()
    page.get_by_text("Weitere Aktionen", exact=True).click()
    for name in ["Lagemeldung", "Maßnahme", "Notiz", "Nicht durchführbar"]:
        expect(page.get_by_role("button", name=name, exact=True)).to_be_visible()
    _eine_hauptaktion(page)
    _touchpruefung(page)
    for selector in ["[data-einheit-hauptaktion]", ".einheit-detailaktionen .btn--secondary", ".einheit-gefahrenleiste",
                     ".einheit-status-aktuell .einheit-sub", ".einheit-detailgefahren li strong"]:
        for node in page.locator(selector).all():
            assert node.evaluate(KONTRAST) >= 4.5, (schema, selector, node.evaluate(KONTRAST))
    page.screenshot(path=str(tmp_path / f"bedienung-{schema}-{breite}.png"), animations="disabled")
    for bereich in ["Karte", "Meldungen"]:
        page.get_by_role("tab", name=bereich, exact=True).click()
        if bereich == "Karte":
            expect(page.get_by_role("article", name="Gewähltes Kartenziel")).to_contain_text("Schule")
            page.wait_for_function(f"!{DATEN}.kartenLaden")
        _eine_hauptaktion(page)
        _touchpruefung(page)
    assert not state["posts"] and errors == []


@pytest.mark.parametrize("breite,hoehe", [(360, 800), (1024, 600)])
def test_langer_auftrag_haelt_einsatz_und_alle_bekannten_gefahren_sichtbar(
    client, setup_db, seite, tmp_path, monkeypatch, breite, hoehe,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client, status="bestaetigt")
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    _plaene(ids, tmp_path)
    langtext = "Zugang prüfen und Kellerbereich kontrollieren. " * 180 + "Auftragsende: Ergebnis an Einsatzleitung."
    with _db() as db:
        db.get(EinheitSiteDispatch, ids["d1"]).auftrag = langtext
        db.commit()
    page, state, errors = seite
    page.emulate_media(color_scheme="dark")
    page.set_viewport_size({"width": breite, "height": hoehe})
    haupt = _oeffnen(page, client, ids)
    expect(page.locator('.einheit-auftrag')).to_have_text(langtext)
    expect(page.get_by_role("region", name="Gefahren am Einsatzort")).to_contain_text("Im Keller")
    assert page.locator('#einheit-gefahren').evaluate("""el => Boolean(el.compareDocumentPosition(
      document.querySelector('.einheit-auftrag')) & Node.DOCUMENT_POSITION_FOLLOWING)""")
    page.locator('.einheit-auftrag').evaluate("el => el.scrollIntoView({ block: 'end' })")
    leiste = page.locator('.einheit-fuehrung')
    for text in ["Schule", "Stromleitung am Boden", "Gasgeruch", "Gasflaschen"]:
        expect(leiste).to_contain_text(text)
    box = leiste.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] < hoehe - 72
    expect(haupt).to_be_visible()
    page.screenshot(path=str(tmp_path / f"langer-auftrag-{breite}.png"), animations="disabled")
    page.locator('.einheit-gefahrenleiste').click()
    gefahr_box = page.locator('#einheit-gefahren').bounding_box()
    assert gefahr_box["y"] >= leiste.bounding_box()["y"] + leiste.bounding_box()["height"] - 1
    assert not state["posts"] and page.url.endswith(f"/auftrag/{ids['d1']}#einheit-gefahren") and errors == []


@pytest.mark.parametrize("breite,hoehe", [(360, 800), (1024, 600)])
def test_formular_bleibt_bei_fehler_offen_und_kehrt_gezielt_zum_auftrag_zurueck(
    client, setup_db, seite, breite, hoehe,
):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    page.set_viewport_size({"width": breite, "height": hoehe})
    _oeffnen(page, client, ids)
    oeffner = page.get_by_role("button", name="Meldung an Einsatzleitung", exact=True)
    oeffner.scroll_into_view_if_needed()
    y = page.evaluate("scrollY")
    pfad = page.url
    oeffner.click()
    dialog = page.locator('.einheit-page').get_by_role("dialog")
    expect(dialog).to_have_accessible_name("Meldung an Einsatzleitung")
    expect(dialog).to_have_accessible_description("AUFTRAG Schule Dorfstraße 1")
    expect(page.locator('.einheit-inhalt')).to_have_attribute("inert", "inert")
    assert page.evaluate("document.activeElement.id") == "einheit-dialog-titel"
    dialog.get_by_role("button", name="Arbeit läuft", exact=True).click()
    text = dialog.get_by_placeholder("Text eingeben oder diktieren")
    text.fill("Lage bleibt erhalten")
    dialog.get_by_label("Fotos (optional)", exact=True).set_input_files({
        "name": "lage.png", "mimeType": "image/png", "buffer": _png(),
    })
    speichern = dialog.get_by_role("button", name="Erfassen", exact=True)
    speichern.focus()
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Schließen", exact=True)).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(speichern).to_be_focused()
    page.keyboard.press("Escape")
    page.mouse.click(2, 2)
    expect(dialog).to_be_visible()
    _touchpruefung(page)
    page.evaluate(f"""() => {{ const d = {DATEN}; window.originalPaket52 = d.outbox.erfassenPaket;
      d.outbox.erfassenPaket = async () => {{ throw new Error('QuotaExceededError'); }}; }}""")
    speichern.click()
    expect(dialog.get_by_role("alert")).to_contain_text("Lokaler Speicher")
    expect(text).to_have_value("Lage bleibt erhalten")
    assert dialog.locator('#meldung-fotos').evaluate("el => el.files[0].name") == "lage.png"
    error_box = dialog.get_by_role("alert").bounding_box()
    assert error_box["y"] >= 0 and error_box["y"] + error_box["height"] < hoehe
    assert page.url == pfad and not state["posts"]
    state["schreiben_offline"] = True
    page.evaluate(f"() => {{ {DATEN}.outbox.erfassenPaket = window.originalPaket52; }}")
    speichern.evaluate("el => { el.click(); el.click(); }")
    expect(dialog).not_to_be_visible()
    expect(oeffner).to_be_focused()
    assert abs(page.evaluate("scrollY") - y) <= 2
    assert page.url == pfad
    assert page.evaluate(f"{DATEN}.detail.auftrag.dispatch_id") == ids["d1"]
    page.wait_for_function(f"{DATEN}.outboxListe.length === 2")
    expect(page.locator('.einheit-meta')).to_contain_text("noch nicht übermittelt")
    assert errors == []


@pytest.mark.parametrize("zugang", ["gk", "qr"])
def test_zugangsaktionen_bleiben_unter_weiteren_funktionen_erreichbar(client, setup_db, seite, zugang):
    from playwright.sync_api import expect

    if zugang == "qr":
        from tests.test_gk_qr_zugang import _eingeloest

        _eingeloest(client)
    else:
        from tests.test_einheit_ressource_pflege import _freigeben
        from tests.test_einheit_zugang import _zugang_mit_auftrag

        cookie, _, _, zugang_id = _zugang_mit_auftrag()
        _freigeben(zugang_id)
        client.cookies.set("ec_gk", cookie)
    page, state, errors = seite
    page.goto("https://ecp.test/einheit")
    expect(page.get_by_role("button", name="Abmelden", exact=True)).not_to_be_visible()
    page.get_by_text("Weitere Funktionen", exact=True).click()
    expect(page.get_by_role("button", name="Abmelden", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Gesamtansicht", exact=True)).to_have_count(0)
    if zugang == "gk":
        page.get_by_text("Meine Einheit – Personal und Ausstattung", exact=True).click()
        expect(page.get_by_role("button", name="Personal speichern", exact=True)).to_be_visible()
        _eine_hauptaktion(page)
    _touchpruefung(page)
    assert not state["posts"] and errors == []


def test_geraeteeinstellungen_aendern_nur_die_darstellung_und_keinen_auftrag(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.wait_for_url(f"**/einheit/auftrag/{ids['d1']}")
    pfad = page.url
    for schema in ["dark", "light", "dark"]:
        page.emulate_media(color_scheme=schema, contrast="more", reduced_motion="reduce")
        expect(page.locator('[data-einheit-hauptaktion]')).to_have_text("Auftrag übernehmen")
        _eine_hauptaktion(page)
        _touchpruefung(page)
        assert page.locator('.einheit-gefahrenleiste').evaluate(KONTRAST) >= 4.5
        assert page.url == pfad and page.evaluate(f"{DATEN}.detail.auftrag.dispatch_id") == ids["d1"]
    assert not state["posts"] and errors == []
