"""GSL-03: Einheitenansicht auf Tablets (Ladefehler sichtbar, Outbox vor erstem Laden) und
Schnellaktionen fuer den Einheitenzugang in der Kraefteuebersicht."""
from __future__ import annotations

from pathlib import Path

from tests.test_ressourcenkarte_routes import _daten, _login


def _text(pfad: str) -> str:
    return Path(pfad).read_text(encoding="utf-8")


def test_outbox_wird_vor_dem_ersten_laden_erzeugt():
    js = _text("app/static/js/einheit_modus.js")
    erzeugen = js.index("this.outbox = createOutbox({")
    erstes_laden = js.index("const erstesLaden = this.laden();")
    assert erzeugen < erstes_laden
    # Fehler beim Erzeugen duerfen die Ansicht nicht leer lassen
    assert "this.outbox = null;" in js and "this.outbox?.onChange" in js


def test_ladefehler_und_abgelaufene_anmeldung_sind_sichtbar():
    js = _text("app/static/js/einheit_modus.js")
    html = _text("app/templates/einheit/einheit.html")
    assert '"anmeldung"' in js and 'this.ladeFehler = "laden"' in js
    assert '["anmeldung", "kontext"].includes(this.ladeFehler)' in js
    assert 'x-show="ladeFehler"' in html
    assert "Die Anmeldung ist abgelaufen oder wurde beendet" in html
    assert "Die Einheitenansicht konnte nicht geladen werden" in html
    assert "Erneut versuchen" in html


def test_outbox_hat_uuid_fallback_fuer_aeltere_webviews():
    js = _text("app/static/js/einheit_outbox.js")
    assert "export function zufallsUuid" in js
    assert "uuid = () => zufallsUuid()," in js
    assert "uuid = () => crypto.randomUUID()" not in js


def test_kraefteuebersicht_bietet_schnellaktionen_fuer_den_zugang(client, setup_db):
    username, lage_id, einheit_id, _ = _daten("admin")
    _login(client, username)
    seite = client.get(f"/lage/{lage_id}/ressourcen")
    assert seite.status_code == 200
    for aktion in ("sms", "link", "qr", "qr-druck"):
        assert f'data-karte-schnell="{aktion}"' in seite.text
    assert f"/einheit?sim={einheit_id}" in seite.text


def test_schnellaktionen_nutzen_vorhandene_zugang_handler():
    js = _text("app/static/js/ressourcen_karte.js")
    for selector in ('[data-zugang-aktion="sms"]', '[data-zugang-aktion="link"]', "[data-qr-zeigen]", "[data-qr-drucken]"):
        assert selector in js
    assert "data-qr-drucken" in _text("app/templates/incident_major/_ressource_karte_zugang.html")
