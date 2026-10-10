"""GK-4.5: Ressourcenpflege ist auf die eigene GK-Einheit begrenzt."""

from uuid import uuid4

import pytest

from app.models.major_incident import LageEinheit, LageEinheitAusstattung, LageEinheitZugang
from app.models.master import OrgSettings
from tests.test_einheit_zugang import _zugang_mit_auftrag
from tests.test_gk_zugang_service import _ausgestellt, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _freigeben(zugang_id: int) -> None:
    with _session() as db:
        zugang = db.get(LageEinheitZugang, zugang_id)
        db.query(OrgSettings).filter_by(org_id=zugang.org_id).one().gk_zugang_ressource_pflegen = True
        db.commit()


def _post(client, path: str, body: dict):
    if not client.cookies.get("ec_csrf"):
        assert client.get("/einheit/api/zustand").status_code == 200
    return client.post(path, json=body, headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})


def test_flag_aus_verbietet_ressourcenpflege(client):
    cookie, _, _, _ = _zugang_mit_auftrag()
    client.cookies.set("ec_gk", cookie)
    response = _post(client, "/einheit/api/ressource/personal", {"client_uuid": str(uuid4()), "gesamt": 3})
    assert response.status_code == 403
    assert response.json()["code"] == "zugang_aktion_nicht_erlaubt"


def test_gk_pflegt_nur_eigene_einheit_und_idempotent(client):
    cookie, _, einheit_id, zugang_id = _zugang_mit_auftrag()
    _freigeben(zugang_id)
    with _session() as db:
        eigene = db.get(LageEinheit, einheit_id)
        fremde = LageEinheit(lage_id=eigene.lage_id, label="Fremde Einheit", status="bereitgestellt")
        db.add(fremde)
        db.commit()
        fremde_id = fremde.id
    with _session() as db:
        _, _, fremde_org_einheit, _, _ = _ausgestellt(db)
        db.commit()
        fremd_org_id = fremde_org_einheit.id
    client.cookies.set("ec_gk", cookie)
    zustand = client.get("/einheit/api/zustand").json()
    assert zustand["kann_ressource_pflegen"] is True
    assert zustand["ressource"]["personal"]["gesamt"] == 0
    key = str(uuid4())
    body = {"client_uuid": key, "gesamt": 4, "fuehrung": 1, "agt": 2, "einheit_id": fremd_org_id}
    assert _post(client, "/einheit/api/ressource/personal", body).status_code == 200
    assert _post(client, "/einheit/api/ressource/personal", body).status_code == 200
    with _session() as db:
        eigene = db.get(LageEinheit, einheit_id)
        fremde = db.get(LageEinheit, fremde_id)
        fremde_org_einheit = db.get(LageEinheit, fremd_org_id)
        assert (eigene.staerke_gesamt, eigene.staerke_fuehrung, eigene.staerke_agt) == (4, 1, 2)
        assert fremde.staerke_gesamt in (None, 0)
        assert fremde_org_einheit.staerke_gesamt in (None, 0)


def test_ausstattung_add_change_remove_und_fremde_id_ist_wirkungslos(client):
    cookie, _, einheit_id, zugang_id = _zugang_mit_auftrag()
    _freigeben(zugang_id)
    client.cookies.set("ec_gk", cookie)
    add = _post(client, "/einheit/api/ressource/ausstattung", {
        "client_uuid": str(uuid4()), "operation": "hinzufuegen", "kategorie": "tauchpumpe",
        "menge": 2, "einheit_id": 123456,
    })
    assert add.status_code == 200
    zeile_id = add.json()["entity_id"]
    assert _post(client, "/einheit/api/ressource/ausstattung", {
        "client_uuid": str(uuid4()), "operation": "aendern", "zeile_id": zeile_id, "menge": 3,
    }).status_code == 200
    with _session() as db:
        zeile = db.get(LageEinheitAusstattung, zeile_id)
        assert zeile.einheit_id == einheit_id and zeile.menge == 3
    assert _post(client, "/einheit/api/ressource/ausstattung", {
        "client_uuid": str(uuid4()), "operation": "entfernen", "zeile_id": zeile_id,
    }).status_code == 200
    with _session() as db:
        assert db.get(LageEinheitAusstattung, zeile_id) is None


def test_widerruf_und_nicht_vorhandene_umbuch_oder_verband_routes(client):
    cookie, _, _, zugang_id = _zugang_mit_auftrag()
    _freigeben(zugang_id)
    client.cookies.set("ec_gk", cookie)
    assert _post(client, "/einheit/api/ressource/personal", {
        "client_uuid": str(uuid4()), "operation": "umbuchen",
    }).status_code == 404
    verband = client.post(
        "/einheit/api/ressource/verband", headers={"X-CSRF-Token": client.cookies.get("ec_csrf")}
    )
    assert verband.status_code == 404
    with _session() as db:
        db.get(LageEinheitZugang, zugang_id).status = "widerrufen"
        db.commit()
    assert _post(client, "/einheit/api/ressource/personal", {
        "client_uuid": str(uuid4()), "gesamt": 1,
    }).status_code == 401
