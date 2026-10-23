"""Einheiten-Tablets oeffnen die GSL zuerst in der Einheitenansicht, die Gesamtansicht danach per Link."""
from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, UserRole
from tests.test_einheit_api import _als, _daten, _device


def _geraet_mit_leserecht(x):
    """Echte Geraete erhalten ihre Rollen bei der Anlage (hier: readonly fuer die Gesamtansicht)."""
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.add(UserRole(user_id=x["u1"], role_id=db.query(Role).filter_by(code="readonly").one().id))
        db.commit()


def test_einheiten_tablet_landet_zuerst_in_der_einheitenansicht(client, setup_db):
    x = _daten()
    _geraet_mit_leserecht(x)
    _device(client, x)
    r = client.get(f"/lage/{x['lage']}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/einheit"


def test_gesamtansicht_per_link_und_rueckweg(client, setup_db):
    x = _daten()
    _geraet_mit_leserecht(x)
    _device(client, x)
    r = client.get(f"/lage/{x['lage']}?gesamt=1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/lage/{x['lage']}"
    assert "ec_gesamt" in r.headers["set-cookie"]
    board = client.get(f"/lage/{x['lage']}", follow_redirects=False)
    assert board.status_code == 200 and 'href="/einheit"' in board.text and "Einheitenansicht" in board.text
    # Rueckkehr in die Einheitenansicht setzt die Wahl zurueck
    zurueck = client.get("/einheit")
    assert zurueck.status_code == 200 and "ec_gesamt" in zurueck.headers.get("set-cookie", "")
    client.cookies.delete("ec_gesamt")
    assert client.get(f"/lage/{x['lage']}", follow_redirects=False).status_code == 302


def test_einheitenansicht_verlinkt_gesamtansicht_mit_parameter(client, setup_db):
    x = _daten()
    _geraet_mit_leserecht(x)
    _device(client, x)
    assert f'href="/lage/{x["lage"]}?gesamt=1"' in client.get("/einheit").text


def test_normale_benutzer_sehen_das_board_direkt(client, setup_db):
    x = _daten()
    _als(client, sign_session(x["admin"]))
    assert client.get(f"/lage/{x['lage']}", follow_redirects=False).status_code == 200
