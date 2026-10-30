"""JSON-Review: Rechte, CSRF, Tenant-Grenzen und Fehlercodes."""
import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektKartenObjekt
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _seed
from tests.test_objekt_karte_rotation import _post
from tests.test_objekt_karte_vorschlaege import _neu


def _login(client, seed):
    client.get("/login")
    response = client.post("/login", data={"username": seed["users"]["u_login"], "password": "Test1234!",
                                         "_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert response.status_code == 302


def test_json_korrigieren_freigeben_ablehnen_und_csrf(client, karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        a = _neu(db, karte)
        b = _neu(db, karte, label="Ablehnen")
        db.commit()
    _login(client, karte)
    url = f'/objekte/{karte["objekt_id"]}/karte/vorschlaege'
    assert len(client.get(url + ".json").json()["vorschlaege"]) == 2
    assert client.post(url + "/freigeben", json={"ids": [a["id"]]}).status_code == 403
    assert _post(client, url + "/freigeben", {"ids": [a["id"]]}).status_code == 422
    korr = _post(client, f'{url}/{a["id"]}/korrigieren', {"lat": 47.101, "lng": 9.201})
    assert korr.status_code == 200 and korr.json()["position_methode"] == "manuell_korrigiert"
    result = _post(client, url + "/freigeben", {"ids": [a["id"]],
                   "bestaetigte_warnungen": {str(a["id"]): ["position_manuell"]}})
    assert result.status_code == 200
    assert _post(client, url + "/freigeben", {"ids": [a["id"]]}).status_code == 409
    assert _post(client, f'{url}/{b["id"]}/ablehnen', {"grund": "Unsicher"}).status_code == 200


@pytest.mark.parametrize("rolle", ["readonly", "objekt_verwalter"])
def test_rechte_tenant_404(client, karte, rolle):
    fremd = _seed("freigabe-route-" + rolle, {"u": rolle})
    _login(client, fremd)
    url = f'/objekte/{karte["objekt_id"]}/karte/vorschlaege'
    assert client.get(url + ".json").status_code == 404
    assert _post(client, url + "/freigeben", {"ids": [1]}).status_code == (404 if rolle == "objekt_verwalter" else 403)
    eigen = f'/objekte/{fremd["objekt_id"]}/karte/vorschlaege'
    assert client.get(eigen + ".json").status_code == 200
    if rolle == "readonly":
        for pfad in ("freigeben", "1/korrigieren", "1/ablehnen"):
            assert _post(client, eigen + "/" + pfad, {}).status_code == 403


def test_revisionskonflikt_409(client, karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        m = db.query(ObjektKartenObjekt).filter_by(objekt_id=karte["objekt_id"], typ="tor").one()
        v = _neu(db, karte, operation="aendern", basis_marker_id=m.id, expected_revision=1, lat=47.101, lng=9.201)
        m.revision = 2
        db.commit()
    _login(client, karte)
    response = _post(client, f'/objekte/{karte["objekt_id"]}/karte/vorschlaege/freigeben', {"ids": [v["id"]]})
    assert response.status_code == 409 and response.json()["detail"]["ergebnisse"][0]["konflikt"]


def test_review_get_persistiert_veraltet(client, karte, tmp_path, monkeypatch):
    from app.config import settings
    from app.models.objekt import ObjektDokument, ObjektKartenVorschlag

    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    pfad = tmp_path / "plan.pdf"
    pfad.write_bytes(b"v1")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        d = ObjektDokument(org_id=karte["org_id"], objekt_id=karte["objekt_id"],
                           pfad="plan.pdf", dateiname_original="plan.pdf")
        db.add(d)
        db.flush()
        v = _neu(db, karte, lat=47.101, lng=9.201, quelle_dokument_id=d.id)
        db.commit()
    pfad.write_bytes(b"v2")
    _login(client, karte)
    antwort = client.get(f'/objekte/{karte["objekt_id"]}/karte/vorschlaege.json')
    assert antwort.status_code == 200 and antwort.json()["vorschlaege"][0]["position_status"] == "veraltet"
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert db.get(ObjektKartenVorschlag, v["id"]).position_status == "veraltet"


@pytest.mark.parametrize("kritisch,bestaetigt,status", [(False, True, 200), (True, False, 422), (True, True, 200)])
def test_korrektur_und_freigabe_atomare_aktion(client, karte, kritisch, bestaetigt, status):
    from app.models.objekt import ObjektChange, ObjektKartenVorschlag

    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        v = _neu(db, karte, symbol_code="bmz" if kritisch else "tor", lat=47.11, lng=9.21)
        db.commit()
        vorher_audit = db.query(ObjektChange).filter_by(objekt_id=karte["objekt_id"]).count()
    _login(client, karte)
    codes = ["position_manuell"]
    if kritisch and bestaetigt:
        codes.append("einsatzkritisch_freigabe_erforderlich")
    antwort = _post(client, f'/objekte/{karte["objekt_id"]}/karte/vorschlaege/{v["id"]}/korrigieren', {
        "lat": 47.102, "lng": 9.202, "rotation": 90, "label": "Geprüft",
        "freigeben": True, "bestaetigte_warnungen": {str(v["id"]): codes},
    })
    assert antwort.status_code == status, antwort.json()
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        aktuell = db.get(ObjektKartenVorschlag, v["id"])
        if status == 200:
            assert aktuell.freigabe_status == "angenommen"
            marker = db.get(ObjektKartenObjekt, antwort.json()["ergebnisse"][0]["marker_id"])
            assert (marker.lat, marker.lng, marker.rotation, marker.label) == (47.102, 9.202, 90, "Geprüft")
            assert marker.position_status == "bestaetigt"
        else:
            assert aktuell.freigabe_status == "offen"
            assert (aktuell.lat, aktuell.lng) == (47.11, 9.21)
            assert aktuell.position_methode == v["position_methode"]
            assert db.query(ObjektChange).filter_by(objekt_id=karte["objekt_id"]).count() == vorher_audit


def test_korrektur_freigabe_arbeitskopie_rollt_zurueck(client, karte):
    from app.models.objekt import Objekt, ObjektKartenVorschlag
    from app.services.objekt_service import erstelle_arbeitskopie

    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        v = _neu(db, karte, lat=47.11, lng=9.21)
        erstelle_arbeitskopie(db, db.get(Objekt, karte["objekt_id"]), karte["users"]["u"])
        db.commit()
    _login(client, karte)
    antwort = _post(client, f'/objekte/{karte["objekt_id"]}/karte/vorschlaege/{v["id"]}/korrigieren', {
        "lat": 47.102, "lng": 9.202, "freigeben": True,
        "bestaetigte_warnungen": {str(v["id"]): ["position_manuell"]},
    })
    assert antwort.status_code == 409
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        aktuell = db.get(ObjektKartenVorschlag, v["id"])
        assert (aktuell.lat, aktuell.lng, aktuell.freigabe_status) == (47.11, 9.21, "offen")
