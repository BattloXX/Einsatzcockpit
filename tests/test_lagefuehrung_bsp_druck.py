"""P5a: manueller BSP-Druck und geschuetzter unverzerrter Originalausschnitt."""
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.objekt import BspOverlayEinsatz, ObjektBspOverlay, ObjektDokument, ObjektEinsatz
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_lagefuehrung_bsp_overlays import einsatz_plan as einsatz_plan
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte, plan as plan
from tests.test_objekt_karte_freigabe_routes import _login


def _druck(client, p, **parameter):
    return client.get(f'/einsatz/{p["incident_id"]}/lagefuehrung/karte/druck',
                      params={"min_lat": 47, "min_lng": 14, "max_lat": 48, "max_lng": 15, **parameter})


def _ausschnitt(p):
    return f'/objekt-medien/bsp-overlay/{p["overlay_id"]}/ausschnitt'


@pytest.mark.parametrize("modus", [None, "unbekannt", "map_with_bsp"])
def test_default_und_unbekannter_modus_nur_karte(client, einsatz_plan, modus):
    p = einsatz_plan
    _login(client, p)
    r = _druck(client, p, **({"bsp_modus": modus} if modus else {}))
    assert r.status_code == 200
    assert 'id="druck-karte"' in r.text
    assert 'class="bsp-seite' not in r.text
    assert "BSP-Overlay wird gedruckt: nein" in r.text
    assert "setTimeout(function () { window.print()" not in r.text


@pytest.mark.parametrize("parameter", [
    {"bsp_deckkraft": 0}, {"bsp_deckkraft": 1.01}, {"bsp_deckkraft": "nan"},
    {"bsp_deckkraft": "inf"}, {"bsp_ids": "x"}, {"bsp_ids": "1,,2"},
    {"bsp_ids": "-1"}, {"bsp_ids": "0"}, {"bsp_ids": "1.5"},
])
def test_strikte_parameter_validierung(client, einsatz_plan, parameter):
    _login(client, einsatz_plan)
    assert _druck(client, einsatz_plan, **parameter).status_code == 422


@pytest.mark.parametrize("modus,hat_karte,seiten", [
    ("MAP_ONLY", True, 0), ("MAP_WITH_BSP", True, 0),
    ("BSP_EXCERPT_ONLY", False, 1), ("MAP_AND_BSP_SEPARATE", True, 1),
])
def test_seitenstruktur_und_freigabenachweis(client, einsatz_plan, modus, hat_karte, seiten):
    p = einsatz_plan
    _login(client, p)
    r = _druck(client, p, bsp_modus=modus, bsp_ids=str(p["overlay_id"]), bsp_deckkraft=0.4)
    assert r.status_code == 200, r.text
    assert ('id="druck-karte"' in r.text) is hat_karte
    assert r.text.count('class="bsp-seite') == seiten
    if modus != "MAP_ONLY":
        assert "BSP-Overlay wird gedruckt: ja" in r.text
        assert "Freigabe-Nr. 1 vom " in r.text
        assert "Musterbetrieb" in r.text and "Seite 1, Version" in r.text
    if modus == "MAP_WITH_BSP":
        assert 'data-bsp-nachweis>BSP-Overlay: Lageplan' in r.text
        assert 'bildLayer(map, o, 0.4)' in r.text
        assert f'/objekt-medien/bsp-overlay/{p["overlay_id"]}/raster?v=1-' in r.text
    if modus == "MAP_AND_BSP_SEPARATE":
        assert 'class="bsp-seite neue-seite"' in r.text
        assert 'data-bsp-nachweis' not in r.text.split('<script>')[0]


@pytest.mark.parametrize("zustand", ["entwurf", "zurueckgezogen", "veraltet", "vorschlag", "raster"])
def test_nur_auslieferbare_hinweis_statt_stillem_wegfall(client, einsatz_plan, zustand):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        overlay = db.get(ObjektBspOverlay, p["overlay_id"])
        if zustand in {"entwurf", "zurueckgezogen"}:
            overlay.status = zustand
        elif zustand == "veraltet":
            db.get(ObjektDokument, p["dokument_id"]).ist_aktuelle_version = False
        elif zustand == "raster":
            (p["pfad"] / overlay.raster_pfad).write_bytes(b"defekt")
        else:
            db.query(ObjektEinsatz).filter_by(incident_id=p["incident_id"]).one().status = "vorschlag"
        db.commit()
    _login(client, p)
    r = _druck(client, p, bsp_modus="MAP_WITH_BSP", bsp_ids=str(p["overlay_id"]))
    assert r.status_code == 200
    assert "BSP-Overlay nicht verfügbar (veraltet oder zurückgezogen): Lageplan" in r.text
    assert "BSP-Overlay wird gedruckt: nein" in r.text
    assert "data-bsp-nachweis>BSP-Overlay:" not in r.text
    assert client.get(_ausschnitt(p)).status_code == (200 if zustand == "vorschlag" else 404)


def test_fehlende_id_verhindert_teilweisen_bsp_druck(client, einsatz_plan):
    p = einsatz_plan
    _login(client, p)
    r = _druck(client, p, bsp_modus="MAP_WITH_BSP", bsp_ids=f'{p["overlay_id"]},999999999')
    assert "BSP-Overlay wird gedruckt: nein" in r.text
    assert "ID 999999999" in r.text
    assert "data-bsp-nachweis>BSP-Overlay:" not in r.text


def test_bildschirm_off_beeinflusst_druck_nicht_alle_oder_einzelner(client, einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        db.add(BspOverlayEinsatz(org_id=p["org_id"], incident_id=p["incident_id"],
                                overlay_id=p["overlay_id"], sichtbarkeit="OFF", deckkraft=0.1))
        db.commit()
    _login(client, p)
    for ids in ("", str(p["overlay_id"])):
        r = _druck(client, p, bsp_modus="MAP_WITH_BSP", bsp_ids=ids, bsp_deckkraft=0.8)
        assert "BSP-Overlay wird gedruckt: ja" in r.text
        assert "bildLayer(map, o, 0.8)" in r.text


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_ausschnitt_original_unverzerrt_privater_cache(client, einsatz_plan, rotation):
    # Die Freigabe muss auf der gedrehten Seite entstehen, nicht nachtraeglich gedreht werden.
    from app.models.objekt import Objekt, ObjektDokumentSeite
    from app.models.user import User
    from app.services import objekt_bsp_overlay_service as bsp
    from app.services import objekt_georef_service as geo
    from tests.test_bsp_overlay_routes import _rolle
    from tests.test_objekt_georef import punkte

    p = einsatz_plan
    # Unterschiedliche Pixel erlauben Vergleich der tatsaechlichen Drehung und des Ausschnitts.
    original = Image.new("RGB", (2000, 1000), "red")
    original.paste("blue", (1000, 0, 2000, 1000))
    original.save(p["pfad"] / "plan.png")
    _rolle(p, "objekt_verwalter")
    with SessionLocal() as db:
        set_tenant_context(db, p["org_id"])
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=p["dokument_id"]).one()
        seite.rotation = rotation
        db.flush()
        geo.referenzpunkte(db, db.get(Objekt, p["objekt_id"]), p["dokument_id"], 1,
                          "setzen", punkte(), "affin", p["users"]["u"])
        overlay = db.get(ObjektBspOverlay, p["overlay_id"])
        bsp.entwurf_aendern(db, overlay, {"ausschnitt": {
            "art": "rechteck", "x0": 0.25, "y0": 0.1, "x1": 0.75, "y1": 0.9}}, p["users"]["u"])
        bsp.freigeben(db, overlay, db.get(User, p["users"]["u"]))
        db.commit()
    _login(client, p)
    r = client.get(_ausschnitt(p))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert r.headers["content-type"] == "image/png"
    bild = original.convert("RGBA").rotate(-rotation, expand=True)
    erwartet = bild.crop((int(bild.width * 0.25), int(bild.height * 0.1),
                         int(bild.width * 0.75), int(bild.height * 0.9)))
    geliefert = Image.open(BytesIO(r.content))
    assert geliefert.size == erwartet.size
    assert geliefert.tobytes() == erwartet.tobytes()
    assert client.get(_ausschnitt(p), headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_fremde_org_404_und_kein_namensleck(client, einsatz_plan):
    p = einsatz_plan
    fremd = _seed("bsp-druck-" + uuid4().hex[:8], {"u": "incident_leader"})
    with SessionLocal() as db:
        set_tenant_context(db, fremd["org_id"])
        db.query(OrgSettings).filter_by(org_id=fremd["org_id"]).one().lagefuehrung_modul_aktiv = True
        db.commit()
    _login(client, fremd)
    assert _druck(client, p, bsp_modus="MAP_WITH_BSP").status_code == 404
    assert client.get(_ausschnitt(p)).status_code == 404


def test_ausschnitt_anmeldung_erforderlich(client, einsatz_plan):
    r = client.get(_ausschnitt(einsatz_plan), follow_redirects=False)
    assert r.status_code in {302, 303, 401, 403, 404}
    assert not r.headers.get("content-type", "").startswith("image/")
