"""P2: Overlay-Verwaltung, Medienrechte, Freigabe und Georef-Editor."""
import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektBspOverlay, ObjektDokumentGeoref, ObjektDokumentSeite
from app.models.user import Role, User
from app.services import objekt_bsp_overlay_service as bsp
from app.services import objekt_georef_service as geo
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte, plan as plan, punkte
from tests.test_objekt_karte_freigabe_routes import _login
from tests.test_objekt_karte_rotation import _post


@pytest.fixture
def overlay_plan(umgebung):
    db, overlay, user, plan = umgebung
    bsp.raster_erzeugen(db, overlay, user.id)
    seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one()
    daten = plan | {"overlay_id": overlay.id, "seite_id": seite.id}
    db.commit()
    return daten


def _basis(plan):
    return f'/objekte/{plan["objekt_id"]}/bsp-overlays'


def _aktion(client, plan, aktion, daten=None):
    return _post(client, f'{_basis(plan)}/{plan["overlay_id"]}/{aktion}', daten or {})


def _raster(plan):
    return f'/objekt-medien/bsp-overlay/{plan["overlay_id"]}/raster'


def _rolle(plan, rolle):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        user = db.get(User, plan["users"]["u"])
        user.user_roles[0].role = db.query(Role).filter_by(code=rolle).one()
        db.commit()


def test_leser_darf_lesen_aber_nicht_schreiben_oder_freigeben(client, overlay_plan):
    p = overlay_plan
    _rolle(p, "readonly")
    _login(client, p)
    assert client.get(_basis(p)).status_code == 200
    for aktion in ("aendern", "rastern", "freigeben", "zurueckziehen", "loeschen"):
        assert _aktion(client, p, aktion).status_code == 403
    assert _post(client, _basis(p), {"seite_id": p["seite_id"], "name": "Neu"}).status_code == 403


@pytest.mark.parametrize("aktion", [None, "aendern", "rastern", "freigeben", "zurueckziehen", "loeschen"])
def test_csrf_fehlt_403(client, overlay_plan, aktion):
    p = overlay_plan
    _login(client, p)
    url = _basis(p) if aktion is None else f'{_basis(p)}/{p["overlay_id"]}/{aktion}'
    assert client.post(url, json={}).status_code == 403


def test_fremde_org_404(client, overlay_plan):
    p = overlay_plan
    fremd = _seed("bsp-routen-fremd", {"u": "objekt_verwalter"})
    _login(client, fremd)
    for url in (_basis(p), _basis(p) + f'?seite_id={p["seite_id"]}', _raster(p), _raster(p) + "?vorschau=1"):
        assert client.get(url).status_code == 404
    for aktion in ("aendern", "rastern", "freigeben", "zurueckziehen", "loeschen"):
        assert _aktion(client, p, aktion).status_code == 404
    assert _post(client, _basis(p), {"seite_id": p["seite_id"], "name": "Fremd"}).status_code == 404
    assert client.get(_basis(fremd) + f'?seite_id={p["seite_id"]}').status_code == 404
    assert _post(client, _basis(fremd), {"seite_id": p["seite_id"], "name": "Fremd"}).status_code == 404
    assert _post(client, f'{_basis(fremd)}/{p["overlay_id"]}/aendern', {"name": "Fremd"}).status_code == 404


def test_entwurf_normale_rasterroute_404(client, overlay_plan):
    _login(client, overlay_plan)
    assert client.get(_raster(overlay_plan)).status_code == 404
    assert client.get(_raster(overlay_plan), headers={"If-None-Match": "*"}).status_code == 404


@pytest.mark.parametrize("rolle, erwartet", [("readonly", 403), ("objekt_verwalter", 200),
                                              ("org_admin", 200), ("admin", 200), ("system_admin", 200)])
def test_vorschau_nur_verwalter(client, overlay_plan, rolle, erwartet):
    _rolle(overlay_plan, rolle)
    _login(client, overlay_plan)
    org = f'&org={overlay_plan["org_id"]}' if rolle == "system_admin" else ""
    assert client.get(_raster(overlay_plan) + "?vorschau=1" + org).status_code == erwartet


def test_etag_304_und_leserauslieferung(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    assert _aktion(client, p, "freigeben").status_code == 200
    _rolle(p, "readonly")
    r = client.get(_raster(p))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=0, must-revalidate"
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        assert r.headers["etag"] == f'"{db.get(ObjektBspOverlay, p["overlay_id"]).raster_sha256}"'
    for etag in (r.headers["etag"], "W/" + r.headers["etag"], '*', '"anders", ' + r.headers["etag"]):
        cached = client.get(_raster(p), headers={"If-None-Match": etag})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["cache-control"] == r.headers["cache-control"]
    assert client.get(_raster(p), headers={"If-None-Match": '"alt"'}).status_code == 200


def test_freigabe_warnungen_einzeln_bestaetigen(client, overlay_plan):
    p = overlay_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        referenzen = punkte()[:3]
        for punkt in referenzen:
            punkt["quelle"] = "manuell"
        geo.referenzpunkte(db, db.get(Objekt, p["objekt_id"]), p["dokument_id"], 1,
                          "setzen", referenzen, "aehnlichkeit", p["users"]["u"])
        db.commit()
    _login(client, p)
    liste = client.get(_basis(p)).json()["overlays"][0]
    assert set(liste["warnungen"]) == {"wenige_punkte", "nur_freie_klicks"}
    assert liste["qualitaet"]["punktzahl"] == 3
    assert liste["qualitaet"]["modell"] == "aehnlichkeit"
    assert liste["qualitaet"]["rmse_m"] < 0.01
    for bestaetigt in ([], ["wenige_punkte"], ["nur_freie_klicks"]):
        r = _aktion(client, p, "freigeben", {"bestaetigte_warnungen": bestaetigt})
        assert r.status_code == 400 and "bestaetigen" in r.json()["detail"]
        assert client.get(_raster(p)).status_code == 404
    assert _aktion(client, p, "freigeben", {"bestaetigte_warnungen": liste["warnungen"]}).status_code == 200


def test_veraltet_nach_neuer_georef_revision(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    assert _aktion(client, p, "freigeben").status_code == 200
    etag = client.get(_raster(p)).headers["etag"]
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        geo.referenzpunkte(db, db.get(Objekt, p["objekt_id"]), p["dokument_id"], 1,
                          "setzen", punkte(), "aehnlichkeit", p["users"]["u"])
        db.commit()
    o = client.get(_basis(p)).json()["overlays"][0]
    assert o["status"] == "veraltet" and "neu prüfen" in o["hinweis"]
    assert client.get(_raster(p), headers={"If-None-Match": etag}).status_code == 404
    assert _aktion(client, p, "rastern").json()["status"] == "entwurf"
    assert _aktion(client, p, "freigeben").json()["status"] == "freigegeben"


def test_loeschen_nur_entwurf_und_zurueckziehen(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    assert _aktion(client, p, "freigeben").status_code == 200
    assert _aktion(client, p, "loeschen").status_code == 409
    assert _aktion(client, p, "zurueckziehen").json()["status"] == "zurueckgezogen"
    assert client.get(_raster(p)).status_code == 404
    assert _aktion(client, p, "loeschen").status_code == 409
    assert _aktion(client, p, "aendern", {"name": "Neuer Entwurf"}).status_code == 200
    assert _aktion(client, p, "loeschen").json() == {"geloescht": True}
    assert _aktion(client, p, "rastern").status_code == 404
    assert client.get(_raster(p) + "?vorschau=1").status_code == 404


def test_anlegen_aendern_seitenfilter_und_validierung(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    daten = {"seite_id": p["seite_id"], "name": "Halle", "plan_art": "hallenbereich", "bereich": "Nord",
             "geschoss": "EG", "standard_sichtbar": False, "standard_deckkraft": 0.4, "empfohlen": True,
             "autodruck": True, "druck_deckkraft": 0.8,
             "ausschnitt": {"art": "rechteck", "x0": 0.2, "y0": 0.3, "x1": 0.8, "y1": 0.9}}
    neu = _post(client, _basis(p), daten)
    assert neu.status_code == 200
    for k, v in daten.items():
        if k != "seite_id":
            assert neu.json()[k] == v
    assert len(client.get(_basis(p) + f'?seite_id={p["seite_id"]}').json()["overlays"]) == 2
    for falsch in ({"standard_deckkraft": 0}, {"ausschnitt": {"art": "rechteck"}}, {"plan_art": "falsch"}):
        assert _aktion(client, p, "aendern", falsch).status_code == 400
    assert _aktion(client, p, "aendern", {"name": "Neu", "ausschnitt": {"art": "seite"}}).status_code == 200
    assert client.get(_raster(p) + "?vorschau=1").status_code == 404
    assert _aktion(client, p, "rastern").json()["bounds"]
    assert client.get(_raster(p) + "?vorschau=1").status_code == 200
    assert _aktion(client, p, "freigeben", {"bestaetigte_warnungen": "falsch"}).status_code == 422


def test_ausschnitt_nach_drehung_konflikt(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.get(ObjektDokumentSeite, p["seite_id"]).rotation = 90
        db.commit()
    daten = {"expected_rotation": 0, "ausschnitt": {"art": "seite"}, "name": "Alter Bildstand"}
    assert _aktion(client, p, "aendern", daten).status_code == 409
    assert _post(client, _basis(p), daten | {"seite_id": p["seite_id"]}).status_code == 409
    assert _aktion(client, p, "aendern", daten | {"expected_rotation": True}).status_code == 422
    assert _aktion(client, p, "aendern", daten | {"expected_rotation": 90}).status_code == 200


def test_georef_editor_rendert_bsp_bereich_und_sperre(client, overlay_plan):
    p = overlay_plan
    _login(client, p)
    r = client.get(f'/objekte/{p["objekt_id"]}/dokumente/seite/{p["seite_id"]}/georef')
    assert r.status_code == 200 and 'id="bsp-overlay"' in r.text and "BSP-Overlay" in r.text
    assert f'data-seite-id="{p["seite_id"]}"' in r.text
    assert client.get(_basis(p) + f'?seite_id={p["seite_id"]}').json()["georef_gueltig"] is True
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.query(ObjektDokumentGeoref).first().status = "abgelehnt"
        db.commit()
    assert client.get(_basis(p) + f'?seite_id={p["seite_id"]}').json()["georef_gueltig"] is False
    assert _aktion(client, p, "rastern").status_code == 400
