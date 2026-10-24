"""Quittungsanzeige muss auch bei verzögertem Lesen der lokalen Outbox wahr bleiben."""

from app.models.major_incident import EinheitAktion
from tests.test_einheit_ablauf_ui import _oeffnen, _vorbereiten
from tests.test_einheit_api import _db, _png
from tests.test_einheit_uebersicht import browser as browser  # noqa: F401
from tests.test_einheit_uebersicht import seite as seite  # noqa: F401

DATEN = "Alpine.$data(document.querySelector('.einheit-page'))"


def test_neues_offline_foto_zeigt_bei_verzoegertem_snapshot_nie_alte_erfolgsmeldung(client, setup_db, seite):
    from playwright.sync_api import expect

    ids = _vorbereiten(client)
    page, state, errors = seite
    _oeffnen(page, client, ids)
    page.get_by_role("button", name="Auftrag übernehmen", exact=True).click()
    expect(page.locator('.einheit-meta')).to_contain_text("Alles übermittelt")
    state["schreiben_offline"] = True
    page.evaluate(f"""() => {{ const d = {DATEN};
      window.originalListe61 = d.outbox.liste;
      d.outbox.liste = () => new Promise(() => {{}});
    }}""")
    page.locator('[x-ref=fotoDirekt]').set_input_files({
        "name": "lokal.png", "mimeType": "image/png", "buffer": _png(),
    })
    page.wait_for_function(f"!{DATEN}.fotoSpeichern")
    expect(page.locator('.einheit-meta')).not_to_contain_text("Alles übermittelt")
    expect(page.locator('.einheit-meta')).to_contain_text("1 noch nicht übermittelt")
    page.evaluate(f"""async () => {{ const d = {DATEN}; d.outbox.liste = window.originalListe61;
      await d.outboxAktualisieren(); }}""")
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).count() == 1
    assert errors == []
