"""Abnahme der mobilen Bedienung im GSL-Einheitenmodus."""

import importlib.util
from pathlib import Path

from app.core.security import sign_session
from app.models.major_incident import EinheitSiteDispatch

_spec = importlib.util.spec_from_file_location("einheit_api_testhilfe", Path(__file__).with_name("test_einheit_api.py"))
assert _spec and _spec.loader
_hilfe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hilfe)
_daten = _hilfe._daten
_db = _hilfe._db


def _als(client, cookie):
    client.cookies.clear()
    client.cookies.set("session", cookie)


def test_detail_liefert_erlaubte_status_je_auftragsart(client, setup_db):
    data = _daten()
    db = _db()
    dispatch = db.get(EinheitSiteDispatch, data["d1"])
    dispatch.auftragsart = "erkundung"
    db.commit()
    db.close()
    _als(client, sign_session(data["u1"], device=True, device_token_id=data["t1"]))
    erkundung = client.get(f'/einheit/api/auftrag/{data["d1"]}').json()["auftrag"]
    assert "abgeschlossen" in erkundung["erlaubte_status"]
    assert "in_arbeit" not in erkundung["erlaubte_status"]

    db = _db()
    dispatch = db.get(EinheitSiteDispatch, data["d1"])
    dispatch.auftragsart = "abarbeitung"
    dispatch.einheit_status = "bestaetigt"
    db.commit()
    db.close()
    abarbeitung = client.get(f'/einheit/api/auftrag/{data["d1"]}').json()["auftrag"]
    assert "in_arbeit" in abarbeitung["erlaubte_status"]


def test_mobile_template_enthaelt_tabs_aktionen_und_versioniertes_modul(client, setup_db):
    data = _daten()
    _als(client, sign_session(data["u1"], device=True, device_token_id=data["t1"]))
    text = client.get("/einheit").text
    assert 'role="tablist"' in text
    assert 'capture="environment"' in text and "primaererText" in text
    # Die Beschriftung der Hauptaktion je Auftragsart kommt aus einheit_modus.js (primaererText).
    from pathlib import Path

    js = Path("app/static/js/einheit_modus.js").read_text(encoding="utf-8")
    for token in ("Erkundung abschließen", "In Arbeit setzen", "Fertig melden"):
        assert token in js
    assert 'einheit_aenderungen.js?v=' in text
    assert not any(quote in text for quote in "“”„‘’")
