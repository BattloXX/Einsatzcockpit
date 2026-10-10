"""WebSocket-Regressionsschutz für den Gruppenkommandanten-Zugang."""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.models.major_incident import LageEinheit
from app.services import gk_zugang_service
from app.services.broadcast import broadcast_lage
from app.services.major_incident_service import close_lage
from app.services.resource_service import setze_gruppenkommandant
from tests.test_gk_zugang_service import _ausgestellt, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _zugang_mit_sitzung() -> tuple[str, int, int, int, int]:
    """Legt einen verifizierten Zugang und eine zweite Einheit derselben Lage an."""
    with _session() as db:
        _, lage, einheit, _, neu = _ausgestellt(db)
        fremde_einheit = LageEinheit(lage_id=lage.id, label="Fremd", status="bereitgestellt", resource_type="fahrzeug")
        db.add(fremde_einheit)
        db.flush()
        cookie, sitzung = gk_zugang_service.sitzung_anlegen(
            db, db.get(gk_zugang_service.LageEinheitZugang, neu.zugang_id), user_agent=None, ip=None, verifiziert=True
        )
        db.commit()
        return cookie, lage.id, einheit.id, fremde_einheit.id, sitzung.id


def test_gk_ws_leitet_nur_aenderungen_der_eigenen_einheit_weiter(client):
    cookie, lage_id, eigene_einheit_id, fremde_einheit_id, _ = _zugang_mit_sitzung()
    client.cookies.set("ec_gk", cookie)

    with client.websocket_connect("/ws/einheit-zugang") as websocket:
        client.portal.call(broadcast_lage, lage_id, {"type": "einheit:changed", "einheit_id": fremde_einheit_id})
        client.portal.call(broadcast_lage, lage_id, {"type": "einheit:changed", "einheit_id": eigene_einheit_id})
        assert websocket.receive_json() == {"type": "einheit:changed", "einheit_id": eigene_einheit_id}


@pytest.mark.parametrize("cookie_art", ["fehlt", "ungueltig", "widerrufen"])
def test_gk_ws_lehnt_fehlerhafte_oder_widerrufene_sitzungen_ab(client, cookie_art):
    if cookie_art == "ungueltig":
        client.cookies.set("ec_gk", "ungueltig")
    elif cookie_art == "widerrufen":
        cookie, _, _, _, sitzung_id = _zugang_mit_sitzung()
        with _session() as db:
            sitzung = db.get(gk_zugang_service.LageEinheitZugangSession, sitzung_id)
            sitzung.revoked_at = gk_zugang_service._now()
            db.commit()
        client.cookies.set("ec_gk", cookie)

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws/einheit-zugang"):
            pass
    assert error.value.code == 4401


@pytest.mark.parametrize("widerruf", ["rotation", "gk_wechsel", "lage_ende"])
def test_gk_ws_meldet_widerruf_beim_naechsten_ping(client, widerruf):
    cookie, lage_id, einheit_id, _, _ = _zugang_mit_sitzung()
    client.cookies.set("ec_gk", cookie)

    with client.websocket_connect("/ws/einheit-zugang") as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"
        with _session() as db:
            lage = db.get(gk_zugang_service.MajorIncident, lage_id)
            einheit = db.get(gk_zugang_service.LageEinheit, einheit_id)
            if widerruf == "rotation":
                gk_zugang_service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test_rotation")
            elif widerruf == "gk_wechsel":
                setze_gruppenkommandant(
                    db, lage, einheit, person_name="Andere Führungskraft", telefon="+436641234568", modus="wechsel"
                )
            else:
                close_lage(db, lage)
            db.commit()

        websocket.send_text("ping")
        assert websocket.receive_json() == {"type": "zugang:widerrufen"}
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_text()
        assert error.value.code == 4401


def test_lage_ws_lehnt_gk_cookie_ohne_benutzer_sitzung_ab(client):
    cookie, lage_id, _, _, _ = _zugang_mit_sitzung()
    client.cookies.set("ec_gk", cookie)

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/ws/lage/{lage_id}"):
            pass
    assert error.value.code == 4401
