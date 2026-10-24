"""Offline-Replays: Quittungen gehören immer zu Einheit, Auftrag und Aktionsart."""

from uuid import uuid4

import pytest

from app.models.major_incident import EinheitAktion, EinheitSiteDispatch, SiteLogEntry, SiteMedia
from tests.test_einheit_api import _db, _h, _png
from tests.test_einheit_uebersicht import _bestand


@pytest.fixture(autouse=True)
def media_im_testverzeichnis(tmp_path, monkeypatch):
    from app.services import lage_media_service

    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path))


def test_uuid_ist_an_auftrag_und_aktionsart_gebunden(client, setup_db):
    ids = _bestand(client)
    key = str(uuid4())
    status = {"client_uuid": key, "status": "bestaetigt", "auftrag_version": 1, "ausgangsstatus": "zugewiesen"}
    url = f"/einheit/api/auftrag/{ids['d1']}"
    original = client.post(url + "/status", headers=_h(client), json=status)
    assert original.status_code == 200 and original.json()["aktion_id"]
    assert client.post(url + "/status", headers=_h(client), json=status).json() == original.json()
    # Derselbe Schlüssel darf kein Foto oder eine Meldung nur scheinbar bestätigen.
    for response in [
        client.post(url + "/meldung", headers=_h(client),
                    json={"client_uuid": key, "art": "note", "text": "Nicht gespeichert"}),
        client.post(url + "/foto", headers=_h(client),
                    data={"client_uuid": key}, files={"file": ("foto.png", _png(), "image/png")}),
        client.post(f"/einheit/api/auftrag/{ids['offen'][1]}/status", headers=_h(client), json=status),
    ]:
        assert response.status_code == 409 and response.json() == {"code": "client_uuid_vergeben"}
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(client_uuid=key).count() == 1
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"]).count() == 0
        assert db.query(SiteLogEntry).filter_by(incident_site_id=ids["a"], kind="note").count() == 0
        assert db.get(EinheitSiteDispatch, ids["offen"][1]).einheit_status == "zugewiesen"


def test_foto_uuid_bestaetigt_nur_originalfoto_und_auftrag(client, setup_db):
    ids = _bestand(client)
    key = str(uuid4())
    def post(dispatch):
        return client.post(f"/einheit/api/auftrag/{dispatch}/foto", headers=_h(client),
                           data={"client_uuid": key}, files={"file": ("foto.png", _png(), "image/png")})
    original = post(ids["d1"])
    assert original.status_code == 200
    assert post(ids["d1"]).json() == original.json()
    assert post(ids["offen"][1]).status_code == 409
    with _db() as db:
        assert db.query(SiteMedia).filter_by(einheit_id=ids["e1"]).count() == 1


def test_erwarteter_einheitenkontext_verhindert_alte_schreibaktion(client, setup_db):
    ids = _bestand(client)
    falsch = _h(client, **{"X-EC-Einheit-Kontext": str(ids["fe"])})
    assert client.get("/einheit/api/zustand", headers=falsch).json() == {"code": "einheit_gewechselt"}
    response = client.post(f"/einheit/api/auftrag/{ids['d1']}/status", headers=falsch,
                           json={"client_uuid": str(uuid4()), "status": "bestaetigt"})
    assert response.status_code == 409 and response.json() == {"code": "einheit_gewechselt"}
    response = client.post("/einheit/api/ressource/personal", headers=falsch,
                           json={"client_uuid": str(uuid4()), "operation": "setzen", "gesamt": 99})
    assert response.status_code == 409
    with _db() as db:
        assert db.get(EinheitSiteDispatch, ids["d1"]).einheit_status == "zugewiesen"
        assert db.query(EinheitAktion).filter_by(einheit_id=ids["e1"]).count() == 0
    # Vorbedingung ist optional und erteilt keine zusätzliche Berechtigung.
    korrekt = {"X-EC-Einheit-Kontext": str(ids["e1"])}
    assert client.get("/einheit/api/zustand", headers=korrekt).status_code == 200
    assert client.get("/einheit/api/zustand").status_code == 200
    assert client.get(f"/einheit/api/auftrag/{ids['fd']}", headers=korrekt).status_code == 404
