"""Katalogvertrag fuer Legendenabgleich und sichtbare Pfeilrichtungen."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import OBJEKT_SYMBOL_TYPEN, ObjektSymbol
from app.services.objekt_karte_service import SYMBOL_ALIASE, symbolkatalog_lesen
from app.services.objekt_karte_vorschlag_service import symbol_basisrichtung
from tests.test_mcp_objekt_karte import karte as karte


def test_aliase_und_richtungen(karte):
    assert set(SYMBOL_ALIASE) == set(OBJEKT_SYMBOL_TYPEN)
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        db.add_all([
            ObjektSymbol(org_id=karte["org_id"], code="bild", name="Eigenes Bild", stil="bild"),
            ObjektSymbol(org_id=karte["org_id"], code="pfeil", name="Eigener Pfeil", stil="pfeil-leer"),
        ])
        db.flush()
        katalog = {s["code"]: s for s in symbolkatalog_lesen(db, karte["org_id"])}
        assert "Feuerwehrzugang" in katalog["hauptzugang"]["aliase"]
        assert "FSD" in katalog["fsd"]["aliase"]
        for code, richtung, basis in (("hauptzugang", True, 90), ("fsd", False, 0),
                                     ("bild", False, 0), ("pfeil", True, 90)):
            assert katalog[code]["traegt_richtung"] is richtung
            assert katalog[code]["basisrichtung_grad"] == basis
            assert symbol_basisrichtung(db, karte["org_id"], code) == basis
        assert katalog["bild"]["aliase"] == katalog["pfeil"]["aliase"] == []
