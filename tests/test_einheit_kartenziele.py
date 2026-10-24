"""Zielhinweise: vorhandene Geodaten, Freigaben und sichere Adressnavigation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.major_incident import IncidentSite, LageEinheit, MajorIncident
from app.models.master import OrgSettings, SystemSettings
from app.models.road_closure import RoadClosureShare
from app.services.einheit_karten_service import ziel_sperren
from tests.test_einheit_api import _db
from tests.test_einheit_uebersicht import _bestand
from tests.test_road_closure_incident_service import closure


def _sperrenbestand(ids):
    with _db() as db:
        org_id = db.get(IncidentSite, ids["a"]).org_id
        flag = db.query(SystemSettings).filter_by(key="strassensperren_module_enabled").first()
        if flag:
            flag.value = "true"
        else:
            db.add(SystemSettings(key="strassensperren_module_enabled", value="true"))
        org = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if org:
            org.strassensperren_modul_aktiv = True
        else:
            db.add(OrgSettings(org_id=org_id, strassensperren_modul_aktiv=True))
        geom = {"type": "LineString", "coordinates": [[9, 47], [9.001, 47.001]]}
        eigene = closure(db, org_id, geom)
        eigene.title = "Zufahrt Schule gesperrt"
        fremd_org = db.get(MajorIncident, db.get(LageEinheit, ids["fe"]).lage_id).org_id
        fremde = closure(db, fremd_org, geom)
        fremde.title = "Fremde Sperre geheim"
        geteilt = closure(db, fremd_org, geom)
        geteilt.title = "Freigegebene Sperre"
        db.add(RoadClosureShare(road_closure_id=geteilt.id, org_id=org_id))
        abgelaufen = closure(db, org_id, geom)
        abgelaufen.title = "Abgelaufene Sperre"
        abgelaufen.valid_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        aufgehoben = closure(db, org_id, geom)
        aufgehoben.title = "Aufgehobene Sperre"
        aufgehoben.cancelled_at = datetime.now(UTC).replace(tzinfo=None)
        fern = closure(db, org_id, {"type": "Point", "coordinates": [12, 49]})
        fern.title = "Entfernte Sperre"
        db.commit()
        return eigene.id


def test_zielhinweise_nur_aktive_freigegebene_sperren_und_lagegefahren(client, setup_db):
    ids = _bestand(client)
    _sperrenbestand(ids)
    response = client.get(f"/einheit/api/auftrag/{ids['d1']}")
    assert response.status_code == 200
    daten = response.json()
    assert daten["strassensperren_status"] == "verfuegbar"
    assert {s["title"] for s in daten["strassensperren"]} == {
        "Zufahrt Schule gesperrt", "Freigegebene Sperre",
    }
    assert daten["strassensperren"][0]["geometry"]["type"] == "LineString"
    assert all(set(s) == {"id", "title", "restriction_label", "geometry"} for s in daten["strassensperren"])
    assert {g["title"] for g in daten["gefahren"]} == {"Stromleitung am Boden", "Gasgeruch"}
    assert all(g["id"] and g["lat"] and g["lng"] for g in daten["gefahren"])
    assert "nicht automatisch" in daten["navigation_hinweis"]
    assert client.get(f"/einheit/api/auftrag/{ids['d2']}").status_code == 404
    assert client.get(f"/einheit/api/auftrag/{ids['fd']}").status_code == 404


@pytest.mark.parametrize("lat,lng", [(None, 9), (47, None), (91, 9), (47, 181), (-91, -181)])
def test_ungueltige_position_nutzt_adresse_ohne_geojson_oder_raeumliche_hinweise(client, setup_db, lat, lng):
    ids = _bestand(client)
    with _db() as db:
        site = db.get(IncidentSite, ids["a"])
        site.lat, site.lng = lat, lng
        db.commit()
    auftrag = client.get("/einheit/api/zustand").json()["kategorien"]["weitere"][0]
    assert auftrag["hat_koordinaten"] is False and auftrag["gefahren_verfuegbar"] is False
    assert "destination=Dorfstra%C3%9Fe%201" in auftrag["nav_url"]
    daten = client.get(f"/einheit/api/auftrag/{ids['d1']}").json()
    assert daten["strassensperren_status"] == "keine_koordinaten"
    assert daten["gefahren"] == [] and daten["strassensperren"] == []
    features = client.get("/einheit/api/karte").json()["features"]
    assert ids["d1"] not in [f["properties"].get("dispatch_id") for f in features]


def test_sperrenfehler_und_deaktiviertes_modul_sind_keine_freie_zufahrt(client, setup_db):
    ids = _bestand(client)
    assert client.get(f"/einheit/api/auftrag/{ids['d1']}").json()["strassensperren_status"] == "inaktiv"
    _sperrenbestand(ids)
    with (_db() as db,
          patch("app.services.road_closure_incident_service.relevant_closures", side_effect=ValueError("test"))):
        site = db.get(IncidentSite, ids["a"])
        assert ziel_sperren(db, site.org_id, site)["strassensperren_status"] == "nicht_verfuegbar"


def test_defekte_geometrie_entfernt_keinen_bekannten_warntext(client, setup_db):
    from app.models.road_closure import RoadClosure

    ids = _bestand(client)
    sperre_id = _sperrenbestand(ids)
    with _db() as db:
        db.get(RoadClosure, sperre_id).geometry_geojson = '{"type":"Point","coordinates":[999,999]}'
        db.commit()
    daten = client.get(f"/einheit/api/auftrag/{ids['d1']}").json()
    sperre = next(s for s in daten["strassensperren"] if s["id"] == sperre_id)
    assert sperre["title"] == "Zufahrt Schule gesperrt" and sperre["geometry"] is None


@pytest.mark.parametrize("zugang", ["gk", "qr"])
def test_gk_und_qr_zielhinweise_bleiben_auf_eigene_auftraege_beschraenkt(client, setup_db, zugang):
    if zugang == "qr":
        from tests.test_gk_qr_zugang import _eingeloest

        ids = _eingeloest(client)
        eigener, fremder = ids["eigener"], ids["fremder"]
    else:
        from app.services import gk_zugang_service
        from tests.test_einheit_zugang import _zugang_mit_auftrag

        cookie, eigener, _, _ = _zugang_mit_auftrag()
        client.cookies.set(gk_zugang_service.COOKIE, cookie)
        fremder = _bestand_fremder_auftrag()
    assert client.get(f"/einheit/api/auftrag/{eigener}").status_code == 200
    assert client.get(f"/einheit/api/auftrag/{fremder}").status_code == 404
    features = client.get("/einheit/api/karte").json()["features"]
    assert all(f["properties"].get("dispatch_id") != fremder for f in features)


def _bestand_fremder_auftrag():
    from tests.test_einheit_api import _daten

    return _daten()["d1"]
