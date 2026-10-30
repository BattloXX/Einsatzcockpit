"""BSP-Rektifizierung, Freigabe und Tenant-Grenzen ohne externe Geodienste."""
import importlib.util
import json
import math
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.objekt import (
    BspOverlayEinsatz, Objekt, ObjektBspOverlay, ObjektChange, ObjektDokument,
    ObjektDokumentGeoref, ObjektDokumentSeite, ObjektEinsatz,
)
from app.models.user import Role, User
from app.services import objekt_bsp_overlay_service as bsp
from app.services import objekt_georef_service as geo
from app.services.objekt_karte_service import ObjektKartenFehler
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte, plan as plan, punkte


@pytest.fixture
def umgebung(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        dokument = db.get(ObjektDokument, plan["dokument_id"])
        dokument.freigabe_status = "freigegeben"
        geo.referenzpunkte(db, objekt, dokument.id, 1, "setzen", punkte(), "aehnlichkeit", plan["users"]["u"])
        overlay = bsp.entwurf_anlegen(db, objekt.id, dokument.id, 1, {"name": "Lageplan"}, plan["users"]["u"])
        user = db.get(User, plan["users"]["u"])
        yield db, overlay, user, plan


@pytest.mark.parametrize("art", ["rechteck", "polygon"])
def test_bounds_und_eck_rueckprojektion_rotation_affin_homographie(umgebung, art):
    # Alle Kombinationen in derselben Transaktion; echte Scherung und Perspektive.
    db, overlay, user, plan = umgebung
    objekt = db.get(Objekt, overlay.objekt_id)
    seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one()
    rot = Image.linear_gradient("L").rotate(90).resize((2400, 1200))
    gruen = Image.linear_gradient("L").resize((2400, 1200))
    original = Image.merge("RGB", (rot, gruen, Image.new("L", (2400, 1200), 50)))
    original.save(plan["pfad"] / "plan.png")
    ausschnitt = {"art": "rechteck", "x0": 0.17, "y0": 0.23, "x1": 0.81, "y1": 0.86}
    if art == "polygon":
        ausschnitt = bsp.ausschnitt_validieren({"art": "polygon", "punkte": [
            {"x": x, "y": y} for x, y in ((0.17, 0.4), (0.35, 0.23), (0.81, 0.23), (0.81, 0.86), (0.17, 0.86))]})
    bsp.entwurf_aendern(db, overlay, {"ausschnitt": ausschnitt}, user.id)
    for rotation in (0, 90, 180, 270):
        seite.rotation = rotation
        breite, hoehe = geo._bildgroesse(seite)
        for modell in ("aehnlichkeit", "affin", "homographie"):
            matrix = ([130, -50, 10, 50, 130, 20, 0, 0] if modell == "aehnlichkeit" else
                      [130, -40, 10, 65, 110, 20, 0.12 if modell == "homographie" else 0,
                       -0.08 if modell == "homographie" else 0])
            p = {"matrix": matrix, "seitenverhaeltnis": breite / hoehe, "lat0": 47, "lng0": 9}
            referenzen = punkte()
            fgrad = geo.ERDRADIUS_M * math.pi / 180
            for punkt in referenzen:
                ost, nord = geo._abbilden(p, punkt["x"], punkt["y"])
                punkt["lat"] = 47 + nord / fgrad
                punkt["lng"] = 9 + ost / (fgrad * math.cos(math.radians(47)))
            geo.referenzpunkte(db, objekt, overlay.dokument_id, 1, "setzen", referenzen, modell, user.id)
            bsp.raster_erzeugen(db, overlay, user.id)
            bounds = json.loads(overlay.raster_bounds_json)
            ecken = [geo._abbilden(p, x, y) for x, y in ((0.17, 0.23), (0.81, 0.23),
                                                        (0.81, 0.86), (0.17, 0.86))]
            assert bounds[0][0] == pytest.approx(47 + min(n for _, n in ecken) / fgrad, abs=1e-8)
            assert bounds[1][0] == pytest.approx(47 + max(n for _, n in ecken) / fgrad, abs=1e-8)
            cos = math.cos(math.radians(47))
            assert bounds[0][1] == pytest.approx(9 + min(e for e, _ in ecken) / (fgrad*cos), abs=1e-8)
            assert bounds[1][1] == pytest.approx(9 + max(e for e, _ in ecken) / (fgrad*cos), abs=1e-8)
            g = db.get(ObjektDokumentGeoref, overlay.georef_id)
            koeff, (w, h), _ = bsp._raster_rechnung(json.loads(g.parameter_json), ausschnitt, breite, hoehe)
            with Image.open(plan["pfad"] / overlay.raster_pfad) as raster:
                assert raster.mode == "RGBA" and max(raster.size) == 2048
                assert raster.getchannel("A").getextrema() == (0, 255)
                gedreht = original.rotate(-rotation, expand=True)
                # Die vier Ecken des Zielrasters ruecktransformieren (auch transparente Bereiche).
                for px, py in ((0, 0), (w, 0), (w, h), (0, h)):
                    a, b, c, d, e, f, gk, hk = koeff
                    nenner = gk * px + hk * py + 1
                    x, y = (a*px+b*py+c)/nenner/breite, (d*px+e*py+f)/nenner/hoehe
                    ost, nord = geo._abbilden(p, x, y)
                    erwartet_ost = ((bounds[0][1] + px/w*(bounds[1][1]-bounds[0][1])) - 9)*fgrad*cos
                    erwartet_nord = ((bounds[1][0] - py/h*(bounds[1][0]-bounds[0][0])) - 47)*fgrad
                    assert math.hypot(ost-erwartet_ost, nord-erwartet_nord) < 0.5
                # Tatsaechliche Pixel pruefen, unabhaengig von den inversen Koeffizienten.
                for x, y in ((0.35, 0.4), (0.65, 0.7)):
                    ost, nord = geo._abbilden(p, x, y)
                    lng, lat = 9+ost/(fgrad*cos), 47+nord/fgrad
                    px = int((lng-bounds[0][1])/(bounds[1][1]-bounds[0][1])*w)
                    py = int((bounds[1][0]-lat)/(bounds[1][0]-bounds[0][0])*h)
                    ist = raster.getpixel((px, py))
                    soll = gedreht.getpixel((int(x*breite), int(y*hoehe)))
                    assert ist[3] == 255
                    assert max(abs(ist[i]-soll[i]) for i in range(3)) <= 2


def test_mercator_gegen_lokale_ebene_300m():
    # Leaflet interpoliert Mercator-Y zwischen den Bounds; maximale Abweichung
    # gegen die lokale lineare Nordachse bei einem 300-m-Objekt in Oesterreich.
    radius = geo.ERDRADIUS_M
    def mercator(lat):
        return radius * math.log(math.tan(math.pi/4 + math.radians(lat)/2))
    sued, nord = 47-150/radius*180/math.pi, 47+150/radius*180/math.pi
    fehler = []
    for i in range(101):
        t = i/100
        y = mercator(sued) + t*(mercator(nord)-mercator(sued))
        lat = math.degrees(2*math.atan(math.exp(y/radius))-math.pi/2)
        fehler.append(abs(lat-(sued+t*(nord-sued)))*radius*math.pi/180)
    assert 0 < max(fehler) < 0.01


@pytest.mark.parametrize("fall", ["georef", "dokumentversion", "dokumentfreigabe", "dokumenthash", "rotation",
                                  "rasterhash", "raster_fehlt", "georef_abgelehnt"])
def test_veralten(umgebung, fall):
    db, overlay, user, plan = umgebung
    bsp.freigeben(db, overlay, user)
    assert bsp.overlay_ist_auslieferbar(db, overlay)
    if fall == "georef":
        geo.referenzpunkte(db, db.get(Objekt, overlay.objekt_id), overlay.dokument_id, 1,
                          "setzen", punkte(), "aehnlichkeit", user.id)
    elif fall == "dokumentversion":
        db.get(ObjektDokument, overlay.dokument_id).ist_aktuelle_version = False
    elif fall == "dokumentfreigabe":
        db.get(ObjektDokument, overlay.dokument_id).freigabe_status = "entwurf"
    elif fall == "dokumenthash":
        (plan["pfad"] / "plan.pdf").write_bytes(b"geaendertes PDF")
    elif fall == "rotation":
        db.query(ObjektDokumentSeite).filter_by(dokument_id=overlay.dokument_id).one().rotation = 90
    elif fall == "georef_abgelehnt":
        db.get(ObjektDokumentGeoref, overlay.georef_id).status = "abgelehnt"
    elif fall == "raster_fehlt":
        (plan["pfad"] / overlay.raster_pfad).unlink()
    else:
        with (plan["pfad"] / overlay.raster_pfad).open("ab") as datei:
            datei.write(b"veraendert")
    assert not bsp.overlay_ist_auslieferbar(db, overlay)


def test_entwurf_zurueckgezogen_und_aenderung_nie_auslieferbar(umgebung):
    db, overlay, user, _ = umgebung
    bsp.raster_erzeugen(db, overlay)
    assert not bsp.overlay_ist_auslieferbar(db, overlay)
    bsp.freigeben(db, overlay, user)
    bsp.zurueckziehen(db, overlay, user.id)
    assert not bsp.overlay_ist_auslieferbar(db, overlay)
    bsp.freigeben(db, overlay, user)
    assert overlay.freigabe_nr == 2
    bsp.entwurf_aendern(db, overlay, {"name": "Neu"}, user.id)
    assert overlay.raster_pfad is None
    assert not bsp.overlay_ist_auslieferbar(db, overlay)


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "objekt_verwalter",
                                   "org_admin", "admin", "system_admin"])
def test_freigabe_nur_verwalter(umgebung, rolle):
    db, overlay, user, _ = umgebung
    user.user_roles[0].role = db.query(Role).filter_by(code=rolle).one()
    db.flush()
    if rolle in {"objekt_verwalter", "org_admin", "admin", "system_admin"}:
        bsp.freigeben(db, overlay, user)
        assert bsp.overlay_ist_auslieferbar(db, overlay)
    else:
        with pytest.raises(ObjektKartenFehler, match="Berechtigung"):
            bsp.freigeben(db, overlay, user)
        assert overlay.freigabe_nr == 0 and overlay.raster_pfad is None


def test_warnungen_einzeln_bestaetigen(umgebung):
    db, overlay, user, _ = umgebung
    p = punkte()[:3]
    for punkt in p:
        punkt["quelle"] = "manuell"
    geo.referenzpunkte(db, db.get(Objekt, overlay.objekt_id), overlay.dokument_id, 1,
                      "setzen", p, "aehnlichkeit", user.id)
    assert set(bsp.freigabe_warnungen(db, overlay)) == {"wenige_punkte", "nur_freie_klicks"}
    for bestaetigt in ([], ["wenige_punkte"], ["nur_freie_klicks"]):
        with pytest.raises(ObjektKartenFehler, match="bestaetigen"):
            bsp.freigeben(db, overlay, user, bestaetigt)
        assert overlay.status == "entwurf" and overlay.freigabe_nr == 0
    bsp.freigeben(db, overlay, user, ["wenige_punkte", "nur_freie_klicks"])
    assert bsp.overlay_ist_auslieferbar(db, overlay)


def test_audit_und_ein_empfohlenes_overlay(umgebung):
    db, overlay, user, _ = umgebung
    bsp.entwurf_aendern(db, overlay, {"empfohlen": True}, user.id)
    zweites = bsp.entwurf_anlegen(db, overlay.objekt_id, overlay.dokument_id, 1,
                                  {"name": "Halle", "empfohlen": True}, user.id)
    assert zweites.empfohlen and not overlay.empfohlen
    bsp.freigeben(db, overlay, user)
    bsp.zurueckziehen(db, overlay, user.id)
    changes = db.query(ObjektChange).filter_by(objekt_id=overlay.objekt_id, bereich="karte").all()
    assert all(c.user_id == user.id for c in changes)
    for feld in ("name", "ausschnitt_json", "raster_sha256", "raster_bounds_json", "freigabe_nr", "empfohlen"):
        assert any(c.feld == f"bsp_overlay.{overlay.id}.{feld}" for c in changes)
    status = [json.loads(c.after_json) for c in changes if c.feld == f"bsp_overlay.{overlay.id}.status"]
    assert "entwurf" in status and "freigegeben" in status and "zurueckgezogen" in status


def test_tenant_isolation(umgebung):
    db, overlay, user, _ = umgebung
    bsp.freigeben(db, overlay, user)
    incident = Incident(primary_org_id=overlay.org_id)
    db.add(incident)
    db.flush()
    incident_id = incident.id
    db.add(ObjektEinsatz(org_id=overlay.org_id, objekt_id=overlay.objekt_id,
                         incident_id=incident_id, status="bestaetigt", quelle="manuell"))
    db.add(BspOverlayEinsatz(org_id=overlay.org_id, overlay_id=overlay.id,
                            incident_id=incident_id, sichtbarkeit="ON"))
    db.commit()
    assert len(bsp.overlays_fuer_einsatz(db, incident_id)) == 1
    _ = overlay.id, overlay.objekt_id, overlay.dokument_id, user.roles
    fremd = _seed("bsp-fremd", {"u": "objekt_verwalter"})
    set_tenant_context(db, fremd["org_id"])
    assert db.query(ObjektBspOverlay).all() == []
    assert db.query(BspOverlayEinsatz).all() == []
    assert not bsp.overlay_ist_auslieferbar(db, overlay)
    for aktion in (lambda: bsp.raster_erzeugen(db, overlay), lambda: bsp.zurueckziehen(db, overlay),
                   lambda: bsp.entwurf_aendern(db, overlay, {"name": "Fremd"}),
                   lambda: bsp.freigeben(db, overlay, user),
                   lambda: bsp.entwurf_anlegen(db, overlay.objekt_id, overlay.dokument_id, 1, {"name": "Fremd"})):
        with pytest.raises(ObjektKartenFehler):
            aktion()
    assert bsp.overlays_fuer_einsatz(db, incident_id) == []


def test_einsatz_vorschlag_und_inherit_on_off(umgebung):
    db, overlay, user, _ = umgebung
    bsp.entwurf_aendern(db, overlay, {"standard_sichtbar": True, "standard_deckkraft": 0.7})
    bsp.freigeben(db, overlay, user)
    incident = Incident(primary_org_id=overlay.org_id)
    db.add(incident)
    db.flush()
    zuordnung = ObjektEinsatz(org_id=overlay.org_id, objekt_id=overlay.objekt_id,
                              incident_id=incident.id, status="vorschlag", quelle="manuell")
    db.add(zuordnung)
    db.flush()
    assert bsp.overlays_fuer_einsatz(db, incident.id) == []
    zuordnung.status = "bestaetigt"
    db.flush()
    daten = bsp.overlays_fuer_einsatz(db, incident.id)[0]
    assert daten["sichtbarkeit"] == "INHERIT" and daten["effektiv_sichtbar"]
    assert daten["standard_deckkraft"] == daten["effektiv_deckkraft"] == 0.7
    einstellung = BspOverlayEinsatz(org_id=overlay.org_id, incident_id=incident.id, overlay_id=overlay.id)
    db.add(einstellung)
    for sichtbar in (True, False):
        overlay.standard_sichtbar = sichtbar
        for modus in ("INHERIT", "ON", "OFF"):
            einstellung.sichtbarkeit = modus
            einstellung.deckkraft = 0.3 if modus == "ON" else None
            db.flush()
            daten = bsp.overlays_fuer_einsatz(db, incident.id)[0]
            assert daten["effektiv_sichtbar"] == (modus == "ON" or (modus == "INHERIT" and sichtbar))
            assert daten["effektiv_deckkraft"] == (0.3 if modus == "ON" else 0.7)
    bsp.zurueckziehen(db, overlay)
    assert bsp.overlays_fuer_einsatz(db, incident.id) == []


@pytest.mark.parametrize("ausschnitt", [
    {"art": "polygon"}, {"art": "rechteck", "x0": -0.1, "y0": 0, "x1": 1, "y1": 1},
    {"art": "rechteck", "x0": 0.8, "y0": 0, "x1": 0.2, "y1": 1},
    {"art": "rechteck", "x0": 0, "y0": 0, "x1": 0.0001, "y1": 1},
    {"art": "rechteck", "x0": 0, "y0": 0.5, "x1": 1, "y1": 0.5},
    {"art": "rechteck", "x0": float("nan"), "y0": 0, "x1": 1, "y1": 1},
    {"art": "seite", "x0": 0.1},
])
def test_ausschnitt_validierung(ausschnitt):
    with pytest.raises(ObjektKartenFehler):
        bsp.ausschnitt_validieren(ausschnitt)


@pytest.mark.parametrize("fall", ["georef_abgelehnt", "georef_fehlt", "dokumentversion", "dokumentfreigabe"])
def test_freigabe_ohne_gueltige_georef_oder_dokument(umgebung, fall):
    db, overlay, user, _ = umgebung
    g = db.query(ObjektDokumentGeoref).one()
    dokument = db.get(ObjektDokument, overlay.dokument_id)
    if fall == "georef_abgelehnt":
        g.status = "abgelehnt"
    elif fall == "georef_fehlt":
        db.delete(g)
        db.flush()
    elif fall == "dokumentversion":
        dokument.ist_aktuelle_version = False
    else:
        dokument.freigabe_status = "entwurf"
    with pytest.raises(ObjektKartenFehler):
        bsp.freigeben(db, overlay, user)
    assert overlay.raster_bounds_json is None


def test_migration_upgrade_downgrade_upgrade(tmp_path):
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0336_objekt_bsp_overlay.py"
    spec = importlib.util.spec_from_file_location("migration_0336", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        for tabelle in ("fire_dept", "objekt", "objekt_dokument", "objekt_dokument_georef", "user", "incident"):
            conn.exec_driver_sql(f'CREATE TABLE "{tabelle}" (id BIGINT PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            inspektor = sa.inspect(conn)
            assert {c["name"] for c in inspektor.get_columns("objekt_bsp_overlay")} == set(
                ObjektBspOverlay.__table__.columns.keys())
            assert {c["name"] for c in inspektor.get_columns("bsp_overlay_einsatz")} == set(
                BspOverlayEinsatz.__table__.columns.keys())
            fks = {fk["constrained_columns"][0]: fk for fk in inspektor.get_foreign_keys("objekt_bsp_overlay")}
            for name, loeschen in (("objekt_id", "CASCADE"), ("dokument_id", "CASCADE"),
                                   ("georef_id", "SET NULL"), ("freigegeben_von", "SET NULL")):
                assert fks[name]["options"]["ondelete"] == loeschen
                assert isinstance(ObjektBspOverlay.__table__.c[name].type, sa.BigInteger)
            assert any(i["column_names"] == ["org_id", "objekt_id"]
                       for i in inspektor.get_indexes("objekt_bsp_overlay"))
            assert inspektor.get_unique_constraints("bsp_overlay_einsatz")[0]["column_names"] == [
                "incident_id", "overlay_id"]
            migration.downgrade()
            assert "objekt_bsp_overlay" not in sa.inspect(conn).get_table_names()
            migration.upgrade()


POLYGON = {"art": "polygon", "punkte": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.1, "y": 0.9}]}


@pytest.mark.parametrize("umgekehrt", [False, True])
def test_polygon_box_nur_server_und_orientierung(umgekehrt):
    punkte = POLYGON["punkte"][::(-1 if umgekehrt else 1)]
    crop = bsp.ausschnitt_validieren(POLYGON | {"punkte": punkte, "x0": -10, "y0": 9, "x1": None, "y1": 0})
    assert crop == {"art": "polygon", "punkte": punkte, "x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9}


@pytest.mark.parametrize("punkte,fehler", [
    ([], "3 bis 100"), (POLYGON["punkte"][:2], "3 bis 100"),
    (POLYGON["punkte"] * 34, "3 bis 100"),
    ([{"x": x, "y": y} for x, y in ((0, 0), (1, 1), (0, 1), (1, 0))], "überschneiden"),
    ([{"x": -0.1, "y": 0}] + POLYGON["punkte"][1:], "Polygon Punkt"),
    ([{"x": 0, "y": float("nan")}] + POLYGON["punkte"][1:], "Polygon Punkt"),
    ([{"x": True, "y": 0}] + POLYGON["punkte"][1:], "Polygon Punkt"),
    (POLYGON["punkte"] + [POLYGON["punkte"][-1]], "gleich"),
    (POLYGON["punkte"] + [POLYGON["punkte"][0]], "gleich"),
    ([{"x": 0, "y": 0}, {"x": 0.0001, "y": 0}, {"x": 0, "y": 1}], "zu klein"),
    ([{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 0.5, "y": 0.50000001}], "zu klein"),
    ([{"x": x, "y": y} for x, y in ((0, 0), (1, 0), (0.5, 0), (0, 1))], "überschneiden"),
])
def test_polygon_validierung(punkte, fehler):
    with pytest.raises(ObjektKartenFehler, match=fehler):
        bsp.ausschnitt_validieren({"art": "polygon", "punkte": punkte})


def test_polygon_100_punkte_und_konkav():
    kreis = [{"x": 0.5 + 0.4 * math.cos(i * math.tau / 100),
              "y": 0.5 + 0.4 * math.sin(i * math.tau / 100)} for i in range(100)]
    assert len(bsp.ausschnitt_validieren({"art": "polygon", "punkte": kreis})["punkte"]) == 100
    assert bsp.ausschnitt_validieren({"art": "polygon", "punkte": [
        {"x": x, "y": y} for x, y in ((0, 0), (1, 0), (0.4, 0.4), (0, 1))]})["x1"] == 1


def test_polygon_raster_alpha_box_und_druck_weiss(umgebung):
    from io import BytesIO

    db, overlay, user, plan = umgebung
    Image.new("RGB", (400, 200), "red").save(plan["pfad"] / "plan.png")
    geo.referenzpunkte(db, db.get(Objekt, overlay.objekt_id), overlay.dokument_id, 1,
                      "setzen", punkte(), "aehnlichkeit", user.id)
    bsp.entwurf_aendern(db, overlay, {"ausschnitt": POLYGON}, user.id)
    bsp.raster_erzeugen(db, overlay)
    g = db.get(ObjektDokumentGeoref, overlay.georef_id)
    p = json.loads(g.parameter_json)
    crop = bsp.ausschnitt_validieren(POLYGON)
    rectangle = crop | {"art": "rechteck"}
    assert bsp._raster_rechnung(p, crop, 400, 200) == bsp._raster_rechnung(p, rectangle, 400, 200)
    bounds = json.loads(overlay.raster_bounds_json)
    with Image.open(plan["pfad"] / overlay.raster_pfad) as bild:
        for x, y, innen in ((0.25, 0.25, True), (0.75, 0.75, False)):
            ost, nord = geo._abbilden(p, x, y)
            fgrad = geo.ERDRADIUS_M * math.pi / 180
            lat, lng = p["lat0"] + nord / fgrad, p["lng0"] + ost / (fgrad * math.cos(math.radians(p["lat0"])))
            px = int((lng - bounds[0][1]) / (bounds[1][1] - bounds[0][1]) * bild.width)
            py = int((bounds[1][0] - lat) / (bounds[1][0] - bounds[0][0]) * bild.height)
            assert (bild.getpixel((px, py))[3] > 0) == innen
    with Image.open(BytesIO(bsp.original_ausschnitt_png(db, overlay))) as druck:
        assert druck.size == (320, 160)
        assert druck.getpixel((20, 20)) == (255, 0, 0, 255)
        assert druck.getpixel((300, 140)) == (255, 255, 255, 255)
