"""Plan-Editor: Web-Vertrag, Folgepositionen und Drehung ohne Netzabfragen."""
import json
from pathlib import Path

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokumentGeoref, ObjektDokumentSeite, ObjektKartenVorschlag
from app.services import objekt_georef_service as geo
from app.services import objekt_karte_vorschlag_service as vs
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte
from tests.test_objekt_karte_freigabe_routes import _login
from tests.test_objekt_karte_rotation import _post


def test_web_dry_run_validierung_revision_und_verwerfen(client, plan):
    _login(client, plan)
    url = f'/objekte/{plan["objekt_id"]}/karte/georef'
    daten = dict(dokument_id=plan["dokument_id"], seite=1, punkte=punkte(), expected_revision=0)
    assert _post(client, url, daten | {"pruefen": True}).json()["georeferenz"]["status"] == "gueltig"
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        assert db.query(ObjektDokumentGeoref).count() == 0
        assert db.query(ObjektKartenVorschlag).count() == 0
    for falsch in ({"punkte": "falsch"}, {"punkte": [dict(punkte()[0], x=2)]}, {"seite": True},
                   {"punkte": punkte()[:2]}, {"modell": "unbekannt"}, {"pruefen": "true"}):
        assert _post(client, url, daten | falsch).status_code == 422
    trocken = _post(client, url, daten | {"punkte": punkte()[:2], "pruefen": True})
    assert trocken.status_code == 200 and trocken.json()["georeferenz"]["ablehngrund"]
    assert client.post(url, json=daten).status_code == 403
    assert _post(client, url, []).status_code == 422
    assert client.post(url, content="{", headers={"Content-Type": "application/json",
                       "X-CSRF-Token": client.cookies.get("ec_csrf")}).status_code == 422
    assert _post(client, url, daten).status_code == 200
    assert _post(client, url, daten).status_code == 409
    gelesen = client.get(url + f'.json?dokument_id={plan["dokument_id"]}&seite=1').json()
    assert gelesen["bildgroesse"] == [2000, 1000]
    assert len(gelesen["georeferenz"]["residuen"]) == 6
    assert client.get(gelesen["seitenbild_url"]).status_code == 200
    verworfen = _post(client, url + "/verwerfen", daten | {"expected_revision": 1})
    assert verworfen.json()["georeferenz"]["status"] == "abgelehnt"


@pytest.mark.parametrize("rolle", ["readonly", "objekt_verwalter"])
def test_web_rechte_und_mandant(client, plan, rolle):
    fremd = _seed("georef-editor-" + rolle, {"u": rolle})
    _login(client, fremd)
    for oid, status in ((plan["objekt_id"], 404), (fremd["objekt_id"], 422)):
        url = f'/objekte/{oid}/karte'
        erwartet = 403 if rolle == "readonly" else status
        assert client.get(url + f'/georef.json?dokument_id={plan["dokument_id"]}&seite=1').status_code == erwartet
        for pfad in ("/georef", "/georef/verwerfen"):
            assert _post(client, url + pfad, dict(dokument_id=plan["dokument_id"], seite=1)).status_code == erwartet
    if rolle == "readonly":
        assert client.get(f'/objekte/{fremd["objekt_id"]}/karte/gebaeude.json').status_code == 403
    else:
        assert client.get(f'/objekte/{plan["objekt_id"]}/karte/gebaeude.json').status_code == 404


def test_gebaeude_netzfehler_und_klickziele(client, plan, monkeypatch):
    from app.services import objekt_gebaeude_service
    from app.services.objekt_karte_service import ObjektKartenFehler

    async def fehler(*args):
        raise ObjektKartenFehler("Overpass derzeit nicht erreichbar")

    monkeypatch.setattr(objekt_gebaeude_service, "gebaeude_umrisse", fehler)
    _login(client, plan)
    url = f'/objekte/{plan["objekt_id"]}/karte/gebaeude.json'
    response = client.get(url)
    assert response.status_code == 422 and "Overpass" in response.json()["detail"]

    async def ecken(*args):
        return {"gebaeude": [{"osm_id": 123, "ecken": [{"nummer": 1, "lat": 47, "lng": 9}]}]}

    monkeypatch.setattr(objekt_gebaeude_service, "gebaeude_umrisse", ecken)
    assert client.get(url).json()["gebaeude"][0]["ecken"][0]["nummer"] == 1


def test_neuberechnen_altwerte_und_manuelle_position(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])

        def setzen(p):
            return geo.referenzpunkte(
                db, objekt, plan["dokument_id"], 1, "setzen", p, None, plan["users"]["u"]
            )["georeferenz"]

        g = setzen(punkte())
        ids = []
        for x in (0.2, 0.5, 0.7):
            ids.append(vs.vorschlag_speichern(db, objekt, dict(symbol_code="tor", georef_id=g["id"],
                       bild_x=x, bild_y=0.4), user_id=plan["users"]["u"])["id"])
        a, manuell, fertig = [db.get(ObjektKartenVorschlag, i) for i in ids]
        alt_lat = a.lat
        manuell.position_methode = "manuell_korrigiert"
        manuell.lat, manuell.lng = 48, 10
        fertig.freigabe_status = "angenommen"
        verschoben = punkte()
        for p in verschoben:
            p["lat"] += 0.0001
        neu = setzen(verschoben)
        assert a.lat == pytest.approx(alt_lat + 0.0001) and a.georef_id == neu["id"]
        assert a.position_status == "vorgeschlagen"
        assert manuell.lat == 48 and manuell.georef_id == g["id"]
        assert vs.vorschlag_dict(manuell, db, objekt)["lat"] == 48
        assert fertig.georef_id == g["id"]
        ungenau = setzen(punkte(massstab=500))
        assert a.georef_id == ungenau["id"] and a.position_status == "pruefung_erforderlich"
        assert {"code": "georef_ungenau"} in json.loads(a.warnungen_json)
        lat = a.lat
        geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "verwerfen", None, None, plan["users"]["u"])
        assert a.lat == lat and a.position_status == "pruefung_erforderlich"
        assert {"code": "georef_ungueltig"} in json.loads(a.warnungen_json)
        gelesen = vs.vorschlag_dict(a, db, objekt)
        assert gelesen["lat"] is None and gelesen["altposition"]["lat"] == lat


def test_rotation_route_entwertet_georef_und_behaelt_altposition(client, plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        g = geo.referenzpunkte(
            db, objekt, plan["dokument_id"], 1, "setzen", punkte(), None, plan["users"]["u"]
        )["georeferenz"]
        v = vs.vorschlag_speichern(db, objekt, dict(symbol_code="tor", georef_id=g["id"], bild_x=0.2, bild_y=0.3),
                                   user_id=plan["users"]["u"])
        seite_id = db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one().id
        db.commit()
    _login(client, plan)
    response = client.post(f'/objekte/{plan["objekt_id"]}/dokumente/seite/{seite_id}/drehen',
                           data={"richtung": "rechts", "_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        assert db.get(ObjektDokumentGeoref, g["id"]).status == "veraltet"
        zeile = db.get(ObjektKartenVorschlag, v["id"])
        assert zeile.lat == v["lat"] and zeile.position_status == "pruefung_erforderlich"
        assert {"code": "georef_ungueltig"} in json.loads(zeile.warnungen_json)
    response = client.get(f'/objekte/{plan["objekt_id"]}/karte/georef.json?dokument_id={plan["dokument_id"]}&seite=1')
    assert response.json()["bildgroesse"] == [1000, 2000]
    assert response.json()["georeferenz"]["status"] == "veraltet"


def test_editor_css_und_sichere_texte():
    import re

    js = Path("app/static/js/objekt_karte.js").read_text()
    editor = js[js.index("  function georefEditor("):js.index("  function initReview(")]
    assert "innerHTML" not in editor
    assert "n.textContent = text" in editor
    template = Path("app/templates/objekt/_karte_review.html").read_text()
    verwendet = set(re.findall(r"oks-georef(?:__[a-z-]+)?", editor))
    definiert = set(re.findall(r"\.(oks-georef(?:__[a-z-]+)?)", template))
    assert verwendet <= definiert


def test_bestehende_georef_dry_run_bleibt_unveraendert_und_rotation_konflikt(client, plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        g = geo.referenzpunkte(
            db, objekt, plan["dokument_id"], 1, "setzen", punkte(), None, plan["users"]["u"]
        )["georeferenz"]
        v = vs.vorschlag_speichern(db, objekt, dict(symbol_code="tor", georef_id=g["id"], bild_x=0.2, bild_y=0.3),
                                   user_id=plan["users"]["u"])
        db.commit()
    _login(client, plan)
    url = f'/objekte/{plan["objekt_id"]}/karte/georef'
    p = punkte()
    for punkt in p:
        punkt["lat"] += 0.0001
    daten = dict(dokument_id=plan["dokument_id"], seite=1, punkte=p, pruefen=True,
                 expected_revision=1, expected_rotation=0)
    assert _post(client, url, daten).status_code == 200
    assert _post(client, url + "/verwerfen", daten).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        assert db.query(ObjektDokumentGeoref).count() == 1
        assert db.get(ObjektDokumentGeoref, g["id"]).status == "gueltig"
        assert db.get(ObjektKartenVorschlag, v["id"]).lat == v["lat"]
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one()
        seite.rotation = 180
        geo.seite_veralten(db, db.get(Objekt, plan["objekt_id"]), seite)
        db.commit()
    assert _post(client, url, daten | {"pruefen": False}).status_code == 409
