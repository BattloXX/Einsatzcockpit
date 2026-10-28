"""Synthetischer 2:1-Plan: Rotation 30 Grad, 100 Meter pro Bildhoehe.

Toleranz 1 mm gegen dieselbe lokale Projektion, keine Netzabfragen.
"""

import json
import math

import pytest
from PIL import Image

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentGeoref, ObjektDokumentSeite
from app.services import objekt_georef_service as geo
from app.services import objekt_karte_vorschlag_service as vorschlag
from app.services.objekt_karte_service import ObjektKartenFehler
from tests.test_mcp_objekt_karte import karte as karte


def position(x, y, massstab=100, spiegel=False):
    u, v = (x - 0.5) * 2, 0.5 - y
    if spiegel:
        u = -u
    a = math.radians(30)
    ost, nord = massstab * (u * math.cos(a) - v * math.sin(a)), massstab * (u * math.sin(a) + v * math.cos(a))
    faktor = geo.ERDRADIUS_M * math.pi / 180
    return 47 + nord / faktor, 9 + ost / (faktor * math.cos(math.radians(47)))


def punkte(massstab=100, spiegel=False):
    return [
        dict(
            id=i,
            x=x,
            y=y,
            lat=position(x, y, massstab, spiegel)[0],
            lng=position(x, y, massstab, spiegel)[1],
            quelle="osm_gebaeude",
            beschreibung=f"Ecke {i}",
        )
        for i, (x, y) in enumerate(((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9), (0.5, 0.2), (0.5, 0.8)))
    ]


@pytest.mark.parametrize("modell", ["aehnlichkeit", "affin", "homographie"])
def test_rueckrechnung_rotation_massstab_nichtquadratisch(modell):
    ergebnis = geo.transformation_berechnen(punkte(), 2000, 1000, modell)
    assert ergebnis["status"] == "gueltig", ergebnis
    assert ergebnis["rmse_m"] < 0.001
    g = ObjektDokumentGeoref(**ergebnis)
    lat, lng, genauigkeit = geo.bild_zu_geo(g, 0.37, 0.61)
    erwartet = position(0.37, 0.61)
    assert vorschlag._abstand(lat, lng, *erwartet) < 0.001
    assert genauigkeit == pytest.approx(1, abs=0.001)
    assert len(json.loads(g.residuen_json)) == 6


@pytest.mark.parametrize("fall", ["zwei", "kollinear", "kleine_flaeche", "spiegel", "drehung", "skalierung", "riesig"])
def test_ablehnung(fall):
    p = punkte(spiegel=fall == "spiegel", massstab=100000 if fall == "riesig" else 100)
    if fall == "zwei":
        p = p[:2]
    if fall == "kollinear":
        for punkt in p:
            punkt["y"] = punkt["x"]
    if fall == "kleine_flaeche":
        for punkt in p:
            punkt["x"] *= 0.01
    if fall == "drehung":
        p[0]["lat"], p[0]["lng"] = position(0.9, 0.9)
    if fall == "skalierung":
        p[0]["lat"], p[0]["lng"] = position(0.1, 0.1, 200)
    for modell in ("aehnlichkeit", "affin", "homographie"):
        ergebnis = geo.transformation_berechnen(p, 2000, 1000, modell)
        assert ergebnis["status"] == "abgelehnt", (fall, modell, ergebnis)
        assert ergebnis["ablehngrund"]
        with pytest.raises(ObjektKartenFehler):
            geo.bild_zu_geo(ObjektDokumentGeoref(**ergebnis), 0.5, 0.5)


@pytest.fixture
def plan(karte, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "plan.pdf").write_bytes(b"original")
    Image.new("RGB", (2000, 1000)).save(tmp_path / "plan.png")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        d = ObjektDokument(
            org_id=karte["org_id"], objekt_id=karte["objekt_id"], pfad="plan.pdf", dateiname_original="plan.pdf"
        )
        db.add(d)
        db.flush()
        db.add(
            ObjektDokumentSeite(
                org_id=karte["org_id"],
                objekt_id=karte["objekt_id"],
                dokument_id=d.id,
                seiten_nr=1,
                bild_pfad="plan.png",
            )
        )
        db.commit()
        return karte | {"dokument_id": d.id, "pfad": tmp_path}


def test_vorschlag_hash_rotation_versionierung_und_ablehnung(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])

        def setzen(p):
            return geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen", p, None, plan["users"]["u"])

        def speichern(gid, **daten):
            return vorschlag.vorschlag_speichern(
                db,
                objekt,
                dict(symbol_code="tor", georef_id=gid, bild_x=0.37, bild_y=0.61) | daten,
                user_id=plan["users"]["u"],
            )

        g = setzen(punkte())["georeferenz"]
        assert g["status"] == "gueltig"
        v = speichern(g["id"])
        assert v["position_status"] == "vorgeschlagen" and v["lat"] is not None
        assert v["position_methode"] == "plan_georeferenzierung"
        with pytest.raises(ObjektKartenFehler, match="gleichzeitig"):
            speichern(g["id"], lat=47, lng=9)
        with pytest.raises(ObjektKartenFehler, match="gleichzeitig"):
            speichern(g["id"], geometry={"type": "Point", "coordinates": [9, 47]})
        with pytest.raises(ObjektKartenFehler):
            speichern(g["id"], bild_x=2)
        # Aendern rechnet erneut, obwohl der gespeicherte Vorschlag bereits lat/lng besitzt.
        vorschlag.vorschlag_speichern(db, objekt, {"bild_x": 0.4}, user_id=plan["users"]["u"], vorschlag_id=v["id"])
        (plan["pfad"] / "plan.pdf").write_bytes(b"geaendert")
        assert geo.georef_pruefen(db, objekt, db.get(ObjektDokumentGeoref, g["id"])) == "veraltet"
        alt = speichern(g["id"])
        assert alt["lat"] is None and alt["lng"] is None and alt["position_status"] == "pruefung_erforderlich"
        assert {"code": "georef_veraltet"} in alt["warnungen"]
        g2 = setzen(punkte())["georeferenz"]
        assert g2["revision"] == 2 and g2["id"] != g["id"]
        assert db.get(ObjektDokumentGeoref, g["id"]).status == "veraltet"
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one()
        seite.rotation = 180
        assert geo.georef_pruefen(db, objekt, db.get(ObjektDokumentGeoref, g2["id"])) == "veraltet"
        seite.rotation = 0
        g3 = setzen(punkte()[:2])["georeferenz"]
        abgelehnt = speichern(g3["id"])
        assert abgelehnt["lat"] is None and abgelehnt["position_status"] == "pruefung_erforderlich"
        assert {"code": "georef_abgelehnt"} in abgelehnt["warnungen"]


def test_lesen_entwertet_vorschlagskoordinaten_ohne_mutation(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        g = geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen", punkte(), None, plan["users"]["u"])
        v = vorschlag.vorschlag_speichern(
            db,
            objekt,
            dict(symbol_code="tor", georef_id=g["georeferenz"]["id"], bild_x=0.5, bild_y=0.5),
            user_id=plan["users"]["u"],
        )
        db.commit()
        (plan["pfad"] / "plan.pdf").write_bytes(b"neue Version")
        gelesen = vorschlag.pruefe_karte(db, objekt)["vorschlaege"][0]
        assert gelesen["lat"] is None and gelesen["lng"] is None
        assert gelesen["position_status"] == "pruefung_erforderlich"
        assert not db.dirty
        assert v["lat"] is not None


@pytest.mark.parametrize("modell,anzahl", [("aehnlichkeit", 2), ("affin", 3), ("homographie", 5)])
def test_mindestanzahl_pro_modell(modell, anzahl):
    assert geo.transformation_berechnen(punkte()[:anzahl], 2000, 1000, modell)["status"] == "abgelehnt"


@pytest.mark.parametrize("modell", ["affin", "homographie"])
def test_echte_affine_und_projektive_transformation(modell):
    referenz = dict(
        lat0=47,
        lng0=9,
        seitenverhaeltnis=2,
        matrix=[90, 15, 3, -10, 110, 5, 0.1 if modell == "homographie" else 0, 0.03],
    )
    if modell == "affin":
        referenz["matrix"][7] = 0

    def ziel(x, y):
        ost, nord = geo._abbilden(referenz, x, y)
        faktor = geo.ERDRADIUS_M * math.pi / 180
        return 47 + nord / faktor, 9 + ost / (faktor * math.cos(math.radians(47)))

    p = punkte()
    for punkt in p:
        punkt["lat"], punkt["lng"] = ziel(punkt["x"], punkt["y"])
    ergebnis = geo.transformation_berechnen(p, 2000, 1000, modell)
    assert ergebnis["status"] == "gueltig", ergebnis
    lat, lng, _ = geo.bild_zu_geo(ObjektDokumentGeoref(**ergebnis), 0.73, 0.28)
    assert vorschlag._abstand(lat, lng, *ziel(0.73, 0.28)) < 0.001


def test_gueltige_aber_ungenaue_georef_braucht_pruefung(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        g = geo.referenzpunkte(
            db, objekt, plan["dokument_id"], 1, "setzen", punkte(massstab=500), None, plan["users"]["u"]
        )["georeferenz"]
        assert g["status"] == "gueltig"
        v = vorschlag.vorschlag_speichern(
            db, objekt, dict(symbol_code="tor", georef_id=g["id"], bild_x=0.5, bild_y=0.5), user_id=plan["users"]["u"]
        )
        assert v["position_status"] == "pruefung_erforderlich" and v["genauigkeit_m"] > 4
        assert {"code": "georef_ungenau"} in v["warnungen"]


def test_anzeigedrehung_90_grad_und_entfernte_georef(plan):
    from app.models.objekt import ObjektKartenVorschlag

    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one()
        seite.rotation = 90
        assert geo._bildgroesse(seite) == (1000, 2000)
        p = punkte()
        for punkt in p:
            punkt["x"], punkt["y"] = 1 - punkt["y"], punkt["x"]
        g = geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen", p, None, plan["users"]["u"])["georeferenz"]
        assert g["status"] == "gueltig"
        v = vorschlag.vorschlag_speichern(
            db,
            objekt,
            dict(symbol_code="tor", georef_id=g["id"], bild_x=1 - 0.61, bild_y=0.37),
            user_id=plan["users"]["u"],
        )
        assert vorschlag._abstand(v["lat"], v["lng"], *position(0.37, 0.61)) < 0.001
        # Entspricht ON DELETE SET NULL; die gespeicherte Position verliert ihre Grundlage.
        zeile = db.get(ObjektKartenVorschlag, v["id"])
        zeile.georef_id = None
        gelesen = vorschlag.vorschlag_dict(zeile, db, objekt)
        assert gelesen["lat"] is None and gelesen["lng"] is None
        assert gelesen["position_status"] == "pruefung_erforderlich"
