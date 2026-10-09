"""HTML-Huelle und Einstieg des GSL-Einheitenmodus."""

import importlib.util
from pathlib import Path

from app.core.security import sign_session
from app.models.major_incident import MajorIncident

_spec = importlib.util.spec_from_file_location("einheit_api_testhilfe", Path(__file__).with_name("test_einheit_api.py"))
assert _spec and _spec.loader
_hilfe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hilfe)
_daten = _hilfe._daten
_db = _hilfe._db


def _als(client, cookie):
    client.cookies.clear()
    client.cookies.set("session", cookie)


def test_einheit_geraet_mit_kontext(client, setup_db):
    data = _daten()
    _als(client, sign_session(data["u1"], device=True, device_token_id=data["t1"]))
    response = client.get("/einheit")
    assert response.status_code == 200
    assert 'einheit_modus.js' in response.text
    assert f'data-lage-id="{data["lage"]}"' in response.text
    assert f'data-einheit-id="{data["e1"]}"' in response.text
    # Alpine-Konfiguration ist im Attribut HTML-escaped (doppelte Anführungszeichen).
    assert f'&#34;lage_id&#34;: {data["lage"]}' in response.text


def test_einheit_geraet_ohne_lage(client, setup_db):
    data = _daten()
    db = _db()
    db.get(MajorIncident, data["lage"]).status = "closed"
    db.commit()
    db.close()
    _als(client, sign_session(data["u1"], device=True, device_token_id=data["t1"]))
    assert "Keine aktive Großschadenslage" in client.get("/einheit").text


def test_simulation_und_normaler_redirect(client, setup_db):
    data = _daten(exercise=True)
    _als(client, sign_session(data["admin"]))
    assert "SIMULATION" in client.get(f'/einheit?sim={data["e1"]}').text
    _als(client, sign_session(data["recorder"]))
    assert client.get("/einheit", follow_redirects=False).status_code == 302


def test_fahrtenbuch_partial_gerendert(client, setup_db):
    response = client.get("/fahrtenbuch/neu", follow_redirects=False)
    assert response.status_code == 302


def test_einheit_template_und_javascript_sind_lesbar():
    root = Path(__file__).parents[1]
    javascript = (root / "app/static/js/einheit_modus.js").read_text()
    template = (root / "app/templates/einheit/einheit.html").read_text()
    for token in ("unterbrechen", "naechsterVersuchIn", "popstate", "massnahmen"):
        assert token in javascript
    assert max(map(len, javascript.splitlines())) <= 160
    assert max(map(len, template.splitlines())) <= 200
