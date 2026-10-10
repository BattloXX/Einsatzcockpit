"""QR-Credential: gezielte Service- und Einlösungsregressionen."""

import json

import pytest

from app.models.major_incident import LageEinheitZugang
from app.services import gk_zugang_service as service
from tests.test_gk_zugang_service import _daten, _session


def _qr(db, *, pin=False):
    org, lage, einheit, leader = _daten(db)
    # The settings row exists from _daten; mutate the mapped row rather than a copy.
    from app.models.master import OrgSettings
    row = db.query(OrgSettings).filter_by(org_id=org.id).one()
    row.gk_qr_aktiv, row.gk_qr_pin = True, pin
    neu = service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None, grund="test")
    return org, lage, einheit, leader, neu


def test_qr_idempotent_rotation_und_session_widerruf():
    with _session() as db:
        _, lage, einheit, _, first = _qr(db)
        second = service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None, grund="erneut")
        assert second == first
        zugang = db.get(LageEinheitZugang, first.zugang_id)
        cookie, _ = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        rotated = service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None, grund="neu", neu=True)
        assert rotated.generation == first.generation + 1
        assert service.sitzung_pruefen(db, cookie, "qr") is None


def test_qr_pin_und_status_sind_geheimnisfrei(client):
    with _session() as db:
        _, _, einheit, _, neu = _qr(db, pin=True)
        einheit_id = einheit.id
        token = neu.link.rsplit("#", 1)[1]
        pin = service.qr_pin_fuer_fuehrung(db.get(LageEinheitZugang, neu.zugang_id))
        db.commit()
    csrf = client.get("/gk").cookies.get("ec_csrf")
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/gk/einloesen", json={"token": token, "pin": "000000"}, headers=headers).status_code == 403
    response = client.post("/gk/einloesen", json={"token": token, "pin": pin}, headers=headers)
    assert response.status_code == 200 and "ec_qr=" in response.headers["set-cookie"]
    with _session() as db:
        from app.models.major_incident import LageEinheit
        status = service.zugang_status(db, db.get(LageEinheit, einheit_id))
        dumped = json.dumps(status, default=str)
        assert status["qr"]["pin_pflicht"] is True
        assert "gkq_" not in dumped and pin not in dumped


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _eingeloest(client, *, pin=False, ohne_leader=True, ressource_pflegen=False):
    """Stellt einen QR-Zugang aus (ohne Telefon), löst ihn ein und liefert die IDs."""
    from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, SitePhase
    from app.models.master import OrgSettings

    with _session() as db:
        org, lage, einheit, leader, neu = _qr(db, pin=pin)
        if ohne_leader:
            einheit.leader_assignment_id = None
            leader.end_at = service._now()
        db.query(OrgSettings).filter_by(org_id=org.id).one().gk_zugang_ressource_pflegen = ressource_pflegen
        fremd = LageEinheit(lage_id=lage.id, label="Fremd", status="bereitgestellt", resource_type="fahrzeug")
        site = IncidentSite(
            major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle", phase=SitePhase.disponiert
        )
        db.add_all([fremd, site])
        db.flush()
        eigener = EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=service._now())
        fremder = EinheitSiteDispatch(einheit_id=fremd.id, site_id=site.id, dispatched_at=service._now())
        db.add_all([eigener, fremder])
        token = neu.link.rsplit("#", 1)[1]
        zugang_pin = service.qr_pin_fuer_fuehrung(db.get(LageEinheitZugang, neu.zugang_id)) if pin else None
        db.commit()
        ids = {
            "token": token, "pin": zugang_pin, "lage": lage.id, "einheit": einheit.id, "org": org.id,
            "eigener": eigener.id, "fremder": fremder.id, "zugang": neu.zugang_id,
        }
    csrf = client.get("/gk").cookies.get("ec_csrf")
    body = {"token": ids["token"]}
    if pin:
        body["pin"] = ids["pin"]
    response = client.post("/gk/einloesen", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    ids["csrf"] = client.cookies.get("ec_csrf")
    return ids


def test_qr_ohne_telefon_nur_eigene_einheit_und_status_traegt_zugang(client):
    from uuid import uuid4

    from app.models.major_incident import EinheitAktion

    ids = _eingeloest(client)
    assert client.get("/einheit/api/zustand").status_code == 200
    assert client.get(f"/einheit/api/auftrag/{ids['eigener']}").status_code == 200
    assert client.get(f"/einheit/api/auftrag/{ids['fremder']}").status_code == 404
    response = client.post(
        f"/einheit/api/auftrag/{ids['eigener']}/status",
        json={"status": "vor_ort", "client_uuid": str(uuid4())},
        headers={"X-CSRF-Token": ids["csrf"]},
    )
    assert response.status_code == 200
    with _session() as db:
        aktion = db.query(EinheitAktion).filter_by(zugang_id=ids["zugang"], einheit_id=ids["einheit"]).one()
        assert aktion.device_token_id is None
    fremd = client.post(
        f"/einheit/api/auftrag/{ids['fremder']}/status",
        json={"status": "vor_ort", "client_uuid": str(uuid4())},
        headers={"X-CSRF-Token": ids["csrf"]},
    )
    assert fremd.status_code in (403, 404)


@pytest.mark.parametrize("pfad", ["personal", "ausstattung"])
def test_qr_darf_nie_ressourcen_pflegen_auch_wenn_org_es_erlaubt(client, pfad):
    from uuid import uuid4

    _eingeloest(client, ressource_pflegen=True)
    response = client.post(
        f"/einheit/api/ressource/{pfad}",
        json={"client_uuid": str(uuid4())},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 403
    assert response.json() == {"code": "zugang_aktion_nicht_erlaubt"}


def test_qr_pin_falsch_sperrt_nach_fehlversuchen(client):
    ids = _eingeloest(client, pin=True)
    client.cookies.clear()
    csrf = client.get("/gk").cookies.get("ec_csrf")
    codes = [
        client.post("/gk/einloesen", json={"token": ids["token"], "pin": "000000"}, headers={"X-CSRF-Token": csrf})
        .status_code
        for _ in range(7)
    ]
    assert 200 not in codes
    richtig = client.post(
        "/gk/einloesen", json={"token": ids["token"], "pin": ids["pin"]}, headers={"X-CSRF-Token": csrf}
    )
    assert richtig.status_code != 200


def test_gkq_token_am_personal_pfad_und_umgekehrt_abgelehnt(client):
    with _session() as db:
        _, lage, einheit, _, qr = _qr(db)
        personal = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        db.flush()
        qr_token, personal_token = qr.link.rsplit("#", 1)[1], personal.link.rsplit("#", 1)[1]
        assert service.token_pruefen(db, qr_token, "personal").zustand != "ok"
        assert service.token_pruefen(db, personal_token, "qr").zustand != "ok"
        assert service.token_pruefen(db, qr_token, "qr").zustand == "ok"
        assert service.token_pruefen(db, personal_token, "personal").zustand == "ok"


def _qr_sitzung(db):
    org, lage, einheit, leader, neu = _qr(db)
    zugang = db.get(LageEinheitZugang, neu.zugang_id)
    cookie, _ = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
    db.flush()
    assert service.sitzung_pruefen(db, cookie, "qr") is not None
    return org, lage, einheit, leader, cookie


def test_qr_ueberlebt_telefonaenderung_aber_nicht_gk_wechsel():
    from app.services.resource_service import setze_gruppenkommandant

    with _session() as db:
        _, lage, einheit, leader, cookie = _qr_sitzung(db)
        setze_gruppenkommandant(db, lage, einheit, person_name=leader.person_name, telefon="+436641111111",
                                modus="auto", user_id=None, author_name="t")
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is not None
        setze_gruppenkommandant(db, lage, einheit, person_name="Anna Neu", telefon="+436642222222",
                                modus="auto", user_id=None, author_name="t")
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is None


def test_qr_widerruf_bei_abgerueckt_lage_zu_notbremse_und_schalter():
    from app.services import resource_service
    from app.services.major_incident_service import close_lage

    with _session() as db:
        _, lage, einheit, _, cookie = _qr_sitzung(db)
        resource_service.set_status(db, einheit.id, lage.id, "abgerueckt", author_name="t", user_id=None)
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is None
    with _session() as db:
        _, lage, einheit, _, cookie = _qr_sitzung(db)
        close_lage(db, lage, closed_by_user_id=None)
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is None
    with _session() as db:
        org, lage, einheit, _, cookie = _qr_sitzung(db)
        service.widerrufe_alle_fuer_org(db, org.id, grund="notbremse")
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is None
    with _session() as db:
        _, lage, einheit, _, cookie = _qr_sitzung(db)
        service.widerrufe(db, einheit.id, grund="manuell", typ="qr")
        db.flush()
        assert service.sitzung_pruefen(db, cookie, "qr") is None


def test_ws_handshake_mit_ec_qr_und_ablehnung_nach_widerruf(client):
    from starlette.websockets import WebSocketDisconnect

    ids = _eingeloest(client)
    with client.websocket_connect("/ws/einheit-zugang"):
        pass
    with _session() as db:
        service.widerrufe(db, ids["einheit"], grund="manuell", typ="qr")
        db.commit()
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws/einheit-zugang"):
            pass
    assert error.value.code == 4401
