"""Objekt-Lagekarte: Rotation ueber HTTP, Persistenz und Tenant-Isolation."""
import json
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektChange, ObjektKartenObjekt
from app.models.user import Role, User, UserRole


@pytest.fixture
def karte(client):
    kennung = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = db.query(FireDept).first()
        fremd = FireDept(slug=f"karte-{kennung}", name="Andere Org", color="#123456", bos="Feuerwehr")
        db.add(fremd)
        db.flush()
        user = User(username=f"karte-{kennung}", password_hash=hash_password("Test1234!"),
                    display_name="Karten-Test", org_id=org.id, active=True)
        rolle = db.query(Role).filter_by(code="objekt_verwalter").first()
        if rolle is None:
            rolle = Role(code="objekt_verwalter", name="Objektverwalter")
            db.add(rolle)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=rolle.id))
        system = db.get(SystemSettings, "objekt_module_enabled")
        if system is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            system.value = "true"
        einstellungen = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if einstellungen is None:
            einstellungen = OrgSettings(org_id=org.id)
            db.add(einstellungen)
        einstellungen.objekt_module_enabled = True
        objekte = [Objekt(org_id=org.id, name="Eigene Karte"),
                   Objekt(org_id=org.id, name="Zweite eigene Karte"),
                   Objekt(org_id=fremd.id, name="Fremde Karte")]
        db.add_all(objekte)
        db.flush()
        fremder_marker = ObjektKartenObjekt(org_id=fremd.id, objekt_id=objekte[2].id,
                                          typ="hauptzugang", lat=47, lng=9, rotation=45)
        db.add(fremder_marker)
        db.commit()
        ids = [o.id for o in objekte]
        fremder_marker_id = fremder_marker.id
        username = user.username
    client.get("/login")
    response = client.post("/login", data={"username": username, "password": "Test1234!",
                                         "_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 302
    return client, ids, fremder_marker_id


def _post(client, url, daten):
    return client.post(url, json=daten, headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})


@pytest.mark.parametrize(("eingabe", "erwartet"), [(None, 0), (75, 75), (-15, 345), (810, 90)])
def test_create_update_normalisierung_und_json(karte, eingabe, erwartet):
    client, ids, _ = karte
    basis = f"/objekte/{ids[0]}/karte/objekte"
    daten = {"typ": "hauptzugang", "lat": 47.1, "lng": 9.2, "label": "Eingang"}
    if eingabe is not None:
        daten["rotation"] = eingabe
    response = _post(client, basis, daten)
    assert response.status_code == 200
    marker = response.json()
    assert marker["rotation"] == erwartet
    for winkel, normalisiert in [(-450, 270), (720, 0), (375, 15)]:
        response = _post(client, f"{basis}/{marker['id']}", {"rotation": winkel})
        assert response.status_code == 200
        assert response.json()["rotation"] == normalisiert
        assert response.json()["label"] == "Eingang"
    # Updates anderer Felder duerfen den Winkel nicht zuruecksetzen.
    assert _post(client, f"{basis}/{marker['id']}", {"label": "Zugang"}).json()["rotation"] == 15
    response = client.get(f"{basis}.json")
    assert response.status_code == 200
    assert response.json()["eintraege"][0]["rotation"] == 15
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(ObjektKartenObjekt, marker["id"]).rotation == 15
        aenderung = db.query(ObjektChange).filter_by(objekt_id=ids[0], feld="symbol_geaendert").first()
        assert json.loads(aenderung.before_json)["rotation"] == erwartet
        assert json.loads(aenderung.after_json)["rotation"] == 270


@pytest.mark.parametrize("winkel", [None, "falsch", 1.5, True, [], {}])
def test_ungueltige_rotation_ist_http_400(karte, winkel):
    client, ids, _ = karte
    basis = f"/objekte/{ids[0]}/karte/objekte"
    assert _post(client, basis, {"typ": "fsd", "lat": 47, "lng": 9, "rotation": winkel}).status_code == 400
    marker = _post(client, basis, {"typ": "fsd", "lat": 47, "lng": 9, "rotation": 60}).json()
    assert _post(client, f"{basis}/{marker['id']}", {"rotation": winkel}).status_code == 400
    assert client.get(f"{basis}.json").json()["eintraege"][0]["rotation"] == 60


def test_cross_org_und_falsches_objekt_abgewiesen(karte):
    client, ids, fremder_marker_id = karte
    daten = {"typ": "fsd", "lat": 47, "lng": 9, "rotation": 180}
    fremd = f"/objekte/{ids[2]}/karte/objekte"
    assert client.get(f"{fremd}.json").status_code == 404
    assert _post(client, fremd, daten).status_code == 404
    assert _post(client, f"{fremd}/{fremder_marker_id}", {"rotation": 90}).status_code == 404
    eigen = f"/objekte/{ids[0]}/karte/objekte"
    assert _post(client, f"{eigen}/{fremder_marker_id}", {"rotation": 90}).status_code == 404
    marker = _post(client, eigen, daten).json()
    assert _post(client, f"/objekte/{ids[1]}/karte/objekte/{marker['id']}", {"rotation": 90}).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(ObjektKartenObjekt, fremder_marker_id).rotation == 45
        assert db.get(ObjektKartenObjekt, marker["id"]).rotation == 180


def test_geometrie_update_speichert_gedrehte_koordinaten(karte):
    client, ids, _ = karte
    basis = f"/objekte/{ids[0]}/karte/objekte"
    vorher = {"type": "LineString", "coordinates": [[9, 47], [9.1, 47]]}
    nachher = {"type": "LineString", "coordinates": [[9.05, 46.95], [9.05, 47.05]]}
    marker = _post(client, basis, {"typ": "geometrie", "geometry": vorher}).json()
    response = _post(client, f"{basis}/{marker['id']}", {"geometry": nachher})
    assert response.status_code == 200
    assert response.json()["geometry"] == nachher
    assert client.get(f"{basis}.json").json()["eintraege"][0]["geometry"] == nachher
