"""Wiederholte Vorschlaege veraendern weder Audit noch produktive Marker."""

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektChange, ObjektKartenVorschlag
from app.services import objekt_karte_vorschlag_service as service
from app.services.objekt_karte_service import karten_objekte_lesen
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_karte_richtung import _setzen


@pytest.mark.parametrize("explizit", [False, True])
@pytest.mark.parametrize("aenderung", ["identisch", "batch", "label", "float"])
def test_wiederholung(plan, explizit, aenderung):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        gid = _setzen(db, objekt, plan)
        daten = dict(symbol_code="tor", georef_id=gid, bild_x=0.37, bild_y=0.61, label="Tor", batch_id="lauf-1")
        neu = service.vorschlag_speichern(db, objekt, daten, user_id=plan["users"]["u"])
        v = db.get(ObjektKartenVorschlag, neu["id"])
        vorher = {k: getattr(v, k) for k in service._FELDER} | {"warnungen_json": v.warnungen_json}
        audits = db.query(ObjektChange).count()
        marker = [(m.id, m.typ, m.lat, m.lng) for m in karten_objekte_lesen(db, objekt)]
        if aenderung == "batch":
            daten["batch_id"] = "lauf-2"
        elif aenderung == "label":
            daten["label"] = "Neues Tor"
        elif aenderung == "float":
            daten["bild_x"] += 1e-12
        antwort = service.vorschlag_speichern(
            db, objekt, daten, user_id=plan["users"]["u"], vorschlag_id=v.id if explizit else None,
        )
        assert antwort["ergebnis"] == ("aktualisiert" if aenderung == "label" else "unveraendert_offen")
        assert db.query(ObjektChange).count() == audits + (aenderung == "label")
        assert v.freigabe_status == "offen"
        assert [(m.id, m.typ, m.lat, m.lng) for m in karten_objekte_lesen(db, objekt)] == marker
        nachher = {k: getattr(v, k) for k in service._FELDER} | {"warnungen_json": v.warnungen_json}
        if aenderung == "label":
            vorher["label"] = "Neues Tor"
        elif aenderung == "batch":
            vorher["batch_id"] = "lauf-2"
        assert nachher == vorher
