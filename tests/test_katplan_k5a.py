"""K5a: optionale Verortung, Pruefstatus und Karteneditor."""

import json

import pytest
from shapely.geometry import Point, shape

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanElement, KatplanVersionElement
from app.services import geocoding
from app.services.katplan_geo_service import ableite_punkt, pruefe_geometrie
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte

PUNKT = {"type": "Point", "coordinates": [9.74, 47.46]}
POLYGON = {"type": "Polygon", "coordinates": [
    [[9, 47], [10, 47], [10, 48], [9.5, 47.3], [9, 48], [9, 47]],
]}


def speichern(client, info, element, geom, **werte):
    return post(client, basis(info) + f"/elemente/{element.id}", **element_form(
        element, geometry_json=json.dumps(geom) if geom is not None else "", **werte))


def test_punkt_pruefen_aendern_entfernen_und_protokoll(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    assert speichern(client, umgebung, e, PUNKT).status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    assert (e.lat, e.lng, e.geo_status) == (47.46, 9.74, "ungeprueft")
    assert json.loads(e.geometry_json) == PUNKT
    assert speichern(client, umgebung, e, PUNKT, geo_geprueft="1").status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    assert e.geo_status == "geprueft"
    r = client.get(basis(umgebung) + f"/elemente/{e.id}")
    assert 'name="geo_geprueft" value="1" checked' in r.text
    neu = {"type": "Point", "coordinates": [9.8, 47.5]}
    assert speichern(client, umgebung, e, neu).status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    assert (e.lat, e.lng, e.geo_status) == (47.5, 9.8, "ungeprueft")
    assert speichern(client, umgebung, e, None, geo_geprueft="1").status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    assert (e.geometry_json, e.lat, e.lng, e.geo_status) == (None, None, None, "keine")
    protokoll = objekte(umgebung, KatplanAenderung)
    assert {"geometrie", "lat", "lng", "geo_status"} <= {a.feld for a in protokoll}
    assert all(len(wert) <= 200 for a in protokoll if a.feld == "geometrie"
               for wert in (a.alt, a.neu) if wert)


def test_polygon_representativer_punkt_und_kuerzung(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung, typ="gefahrenbereich")
    assert speichern(client, umgebung, e, POLYGON).status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    assert shape(POLYGON).contains(Point(e.lng, e.lat))
    assert (e.lat, e.lng) == ableite_punkt(POLYGON)
    # Ein langer Linienzug darf das feldgenaue Protokoll nicht aufblaehen.
    linie = {"type": "LineString", "coordinates": [[9 + i / 1000, 47] for i in range(80)]}
    assert speichern(client, umgebung, e, linie).status_code == 200
    eintrag = [a for a in objekte(umgebung, KatplanAenderung) if a.feld == "geometrie"][-1]
    assert len(eintrag.neu) == 200


@pytest.mark.parametrize("typ,geom,meldung", [
    ("kontrollstelle", POLYGON, "nur einen Punkt"),
    ("objektverweis", POLYGON, "nur einen Punkt"),
    ("ablauf", PUNKT, "Dieser Elementtyp wird nicht verortet."),
    ("kontrollstelle", {"type": "Point", "coordinates": [181, 47]}, "außerhalb"),
    ("kontrollstelle", {"type": "Point", "coordinates": [9, 91]}, "außerhalb"),
    ("kontrollstelle", {"type": "Polygon", "coordinates": []}, "Koordinaten"),
])
def test_unerlaubte_geometrie_ohne_mutation(client, umgebung, typ, geom, meldung):
    login(client, umgebung)
    e = anlegen(client, umgebung, typ=typ)
    r = speichern(client, umgebung, e, geom)
    assert r.status_code == 422 and meldung in r.text
    gespeichert = objekte(umgebung, KatplanElement)[0]
    assert gespeichert.version == e.version and gespeichert.geometry_json is None


def test_ungueltiges_json(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    r = post(client, basis(umgebung) + f"/elemente/{e.id}", **element_form(e, geometry_json="{kaputt"))
    assert r.status_code == 422 and "Ungültiges GeoJSON" in r.text


@pytest.mark.parametrize("typ", ["info", "massnahme", "gefahrenbereich", "ressource"])
@pytest.mark.parametrize("geom", [PUNKT, POLYGON,
    {"type": "LineString", "coordinates": [[9, 47], [10, 48]]},
    {"type": "MultiLineString", "coordinates": [[[9, 47], [10, 48]]]},
    {"type": "MultiPolygon", "coordinates": [POLYGON["coordinates"]]},
])
def test_erlaubte_typen(typ, geom):
    assert pruefe_geometrie(typ, json.dumps(geom)) == geom
    lat, lng = ableite_punkt(geom)
    assert shape(geom).covers(Point(lng, lat))
    assert pruefe_geometrie(typ, "  ") is None
    assert pruefe_geometrie(typ, None) is None


def test_adresssuche_rechte_und_json(client, umgebung, monkeypatch):
    aufrufe = []
    async def suche(q, limit):
        aufrufe.append((q, limit))
        return [{"display_name": "Hauptstraße 1", "lat": 47.46, "lng": 9.74}]
    monkeypatch.setattr(geocoding, "search_addresses", suche)
    # Seit K8 duerfen auch Leser Adressen suchen (Umkreissuche in der Plansuche).
    login(client, umgebung, "readonly")
    assert client.get('/plaene/adress-suche?q=Hauptstraße').status_code == 200
    aufrufe.clear()
    login(client, umgebung)
    r = client.get('/plaene/adress-suche', params={"q": "Hauptstraße"})
    assert r.status_code == 200
    assert r.json() == {"treffer": [{"display_name": "Hauptstraße 1", "lat": 47.46, "lng": 9.74}]}
    assert aufrufe == [("Hauptstraße", 6)]


def test_snapshot_und_anzeige(client, umgebung):
    login(client, umgebung, "katplan_freigeber")
    e = anlegen(client, umgebung)
    assert speichern(client, umgebung, e, PUNKT, geo_geprueft="1", strasse="Testweg", ort="Wolfurt").status_code == 200
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        version = db.query(KatplanVersionElement).filter_by(element_uuid=e.uuid).one()
    assert (version.lat, version.lng, version.geo_status) == (47.46, 9.74, "geprueft")
    assert json.loads(version.geometry_json) == PUNKT
    e = objekte(umgebung, KatplanElement)[0]
    assert speichern(client, umgebung, e, None).status_code == 200
    login(client, umgebung, "readonly")
    r = client.get(basis(umgebung))
    assert r.status_code == 200 and "Ort geprüft" in r.text and "Testweg" in r.text
    assert basis(umgebung) + f"/karte#element-{e.uuid}" in r.text
    assert "Auf Karte zeigen" in r.text


def test_editor_assets_und_optionale_karte(client, umgebung):
    login(client, umgebung)
    r = client.get(basis(umgebung) + "/bearbeiten")
    assert r.status_code == 200
    for asset in ("leaflet.min.js", "leaflet-geoman.min.js", "katplan_karte.js",
                  "leaflet.min.css", "leaflet-geoman.min.css"):
        assert asset in r.text
    e = anlegen(client, umgebung, typ="ablauf")
    r = client.get(basis(umgebung) + f"/elemente/{e.id}")
    assert 'data-kp-geo=' not in r.text and 'id="kp-geo-karte-' not in r.text
    e = anlegen(client, umgebung)
    r = client.get(basis(umgebung) + f"/elemente/{e.id}")
    assert f'id="kp-geo-karte-{e.id}"' in r.text and 'data-kp-geo="punkt"' in r.text
    assert 'name="geometry_json"' in r.text and 'name="geo_geprueft"' in r.text
