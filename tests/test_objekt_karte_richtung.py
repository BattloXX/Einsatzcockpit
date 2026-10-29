"""Bildrichtung, lokale Peilung und toleranter Abgleich von Planvorschlaegen."""

import json
import math

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektChange, ObjektDokumentGeoref, ObjektKartenVorschlag, ObjektSymbol
from app.models.user import User
from app.services import objekt_georef_service as geo
from app.services import objekt_karte_vorschlag_service as service
from app.services.objekt_karte_freigabe_service import freigeben, vorschlag_korrigieren
from app.services.objekt_karte_service import ObjektKartenFehler
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte


@pytest.mark.parametrize("modell", ["aehnlichkeit", "affin", "homographie"])
@pytest.mark.parametrize("dx,dy,erwartet", [(1, 0, 60), (0, -1, 330), (0, 1, 150), (1, -1, 15)])
def test_peilung_nichtquadratisch(modell, dx, dy, erwartet):
    g = ObjektDokumentGeoref(**geo.transformation_berechnen(punkte(), 2000, 1000, modell))
    # Fixture: mathematisch positive Drehung um 30 Grad, daher rechts = Peilung 60 Grad.
    assert geo.bild_richtung_zu_peilung(g, 0.37, 0.61, dx, dy) == pytest.approx(erwartet, abs=0.5)
    g.status = "abgelehnt"
    with pytest.raises(ObjektKartenFehler, match="nicht gueltig"):
        geo.bild_richtung_zu_peilung(g, 0.37, 0.61, dx, dy)


def _setzen(db, objekt, plan, referenzen=None):
    return geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen",
                             referenzen if referenzen is not None else punkte(), None,
                             plan["users"]["u"])["georeferenz"]["id"]


def _speichern(db, objekt, plan, gid, **daten):
    return service.vorschlag_speichern(db, objekt, dict(
        symbol_code="tor", georef_id=gid, bild_x=0.37, bild_y=0.61,
        bild_richtung={"dx": 1, "dy": 0},
    ) | daten, user_id=plan["users"]["u"])


def test_symbolbasis_aus_katalog_und_aendern(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        gid = _setzen(db, objekt, plan)
        db.add(ObjektSymbol(org_id=objekt.org_id, code="eigener_pfeil", name="Eigener Pfeil", stil="pfeil-leer"))
        db.flush()
        for code, erwartet in (("hauptzugang", 330), ("tor", 60), ("eigener_pfeil", 330)):
            v = _speichern(db, objekt, plan, gid, symbol_code=code)
            assert v["rotation"] == erwartet
            assert v["bild_richtung"] == {"dx": 1, "dy": 0}
            a = service.vorschlag_speichern(db, objekt, {"label": "Neu"},
                                          user_id=plan["users"]["u"], vorschlag_id=v["id"])
            assert a["rotation"] == erwartet and a["ergebnis"] == "aktualisiert"


@pytest.mark.parametrize("daten", [
    {"rotation": 45}, {"georef_id": None}, {"bild_x": None},
    {"bild_richtung": {"dx": 0, "dy": 0}}, {"bild_richtung": {"dx": 1e-7, "dy": 0}},
    {"bild_richtung": {"dx": 2, "dy": 0}}, {"bild_richtung": {"dx": True, "dy": 0}},
    {"bild_richtung": {"dx": float("nan"), "dy": 0}}, {"bild_richtung": []},
])
def test_ungueltige_richtung(plan, daten):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        gid = _setzen(db, objekt, plan)
        with pytest.raises(ObjektKartenFehler):
            _speichern(db, objekt, plan, gid, **daten)


def test_ungueltige_georef_und_ersatzrevision(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        gid = _setzen(db, objekt, plan, punkte()[:2])
        v = _speichern(db, objekt, plan, gid)
        assert v["rotation"] == 0 and v["position_status"] == "pruefung_erforderlich"
        assert {"code": "richtung_ohne_georef"} in v["warnungen"]
        gid = _setzen(db, objekt, plan)
        repariert = _speichern(db, objekt, plan, gid)
        assert repariert["id"] == v["id"] and repariert["rotation"] == 60
        assert {"code": "richtung_ohne_georef"} not in repariert["warnungen"]
        v = _speichern(db, objekt, plan, gid, symbol_code="hauptzugang")
        assert v["rotation"] == 330
        # Dieselben Quellen, gueltige neue Revision mit zusaetzlicher Drehung um 90 Grad.
        referenzen = punkte()
        faktor = geo.ERDRADIUS_M * math.pi / 180
        for p in referenzen:
            ost = (p["lng"] - 9) * faktor * math.cos(math.radians(47))
            nord = (p["lat"] - 47) * faktor
            p["lat"], p["lng"] = 47 + ost / faktor, 9 - nord / (faktor * math.cos(math.radians(47)))
        neu = _setzen(db, objekt, plan, referenzen)
        gespeichert = db.get(ObjektKartenVorschlag, v["id"])
        assert gespeichert.georef_id == neu and gespeichert.rotation == 240
        alt = db.query(ObjektKartenVorschlag).filter_by(symbol_code="tor").one()
        assert alt.rotation == 330
        assert {"code": "richtung_ohne_georef"} not in json.loads(alt.warnungen_json)


def test_bildtoleranz_freigabe_und_ui_korrektur(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        objekt.lat, objekt.lng = 47, 9
        gid = _setzen(db, objekt, plan)
        v = _speichern(db, objekt, plan, gid, batch_id="lauf.1")
        a = _speichern(db, objekt, plan, gid, bild_x=0.375, batch_id="lauf.2", label="Neu")
        assert a["id"] == v["id"] and a["ergebnis"] == "aktualisiert" and a["batch_id"] == "lauf.2"
        assert a["label"] == "Neu"
        neu = _speichern(db, objekt, plan, gid, bild_x=0.425)
        assert neu["ergebnis"] == "neu" and neu["id"] != v["id"]
        user = db.get(User, plan["users"]["u"])
        freigeben(db, objekt, [v["id"]], user, {str(v["id"]): [w["code"] for w in a["warnungen"]]}, "mcp")
        audit_anzahl = db.query(ObjektChange).count()
        unveraendert = _speichern(db, objekt, plan, gid, bild_x=0.375)
        assert unveraendert["ergebnis"] == "unveraendert" and unveraendert["marker_id"]
        assert db.query(ObjektChange).count() == audit_anzahl
        assert len(service.vorschlaege_auflisten(db, objekt, "offen")) == 1
        # Derselbe Servicepfad wie die UI loest bei manueller Positionierung die Bildbindung.
        korrigiert = vorschlag_korrigieren(db, objekt, neu["id"], user=user,
                                         lat=47, lng=9, rotation=42, label="Tor", quelle="ui")
        assert korrigiert["rotation"] == 42 and korrigiert["bild_richtung"] is None
