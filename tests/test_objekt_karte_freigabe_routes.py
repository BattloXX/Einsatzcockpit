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
