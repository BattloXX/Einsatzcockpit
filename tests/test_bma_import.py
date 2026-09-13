"""Regressionstests fuer den BMA-Datenblatt-Kontaktabgleich."""
import json
from types import SimpleNamespace

from app.models.bma_import import BmaImportSatz
from app.models.kontakt import Kontakt
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektKontakt
from app.services.bma_import.bma_sync import (
    _adoptionskandidaten,
    ignoriere_vorschlag,
    ist_offener_vorschlag,
    kontakt_abweichung,
)


def _satz(hash="h", ignoriert=None):
    kontakte = [{"extern_id": "pdf:1:bma_alarmperson:max", "name": "Max", "art": "bma_alarmperson"}]
    return BmaImportSatz(org_id=1, extern_id="pdf:1", quell_hash=hash, bestaetigt_hash=hash,
                         ignoriert_hash=ignoriert, rohdaten_json=json.dumps({"anlage": {}, "kontakte": kontakte}))


def _objekt(kontakte=None):
    o = Objekt(org_id=1, name="Test", nummer="1", status=OBJEKT_STATUS_FREIGEGEBEN)
    o.kontakte = kontakte or []
    return o


def _kontakt(name, **werte):
    return ObjektKontakt(
        org_id=1,
        zentraler_kontakt=Kontakt(org_id=1, anzeigename=name),
        **werte,
    )


def test_fehlender_kontakt_ist_offener_vorschlag():
    assert ist_offener_vorschlag(_satz(), _objekt()) is True


def test_vollstaendiger_kontaktstand_ist_nicht_offen():
    k = _kontakt("Max", extern_quelle="dibos_bma", extern_id="pdf:1:bma_alarmperson:max", art="bma_alarmperson")
    assert ist_offener_vorschlag(_satz(), _objekt([k])) is False


def test_ignorieren_bleibt_bis_quelle_sich_aendert():
    satz = _satz()
    db = SimpleNamespace(add=lambda _entry: None)
    ignoriere_vorschlag(db, satz, SimpleNamespace(id=1))
    assert ist_offener_vorschlag(satz, _objekt()) is False
    satz.quell_hash = "neu"
    assert ist_offener_vorschlag(satz, _objekt()) is True


def test_zwei_anlagen_haben_disjunkte_kontaktmengen():
    k1 = _kontakt("Max", extern_quelle="dibos_bma", extern_id="pdf:1:bma_alarmperson:max", art="bma_alarmperson")
    k2 = _kontakt("Eva", extern_quelle="dibos_bma", extern_id="pdf:12:bma_alarmperson:eva", art="bma_alarmperson")
    fehlend, ueber = kontakt_abweichung(_satz(), _objekt([k1, k2]))
    assert fehlend == []
    assert ueber == []


def test_entwurf_ist_nie_queue_vorschlag():
    objekt = _objekt()
    objekt.status = "entwurf"
    assert ist_offener_vorschlag(_satz(), objekt) is False


def test_adoptionskandidaten_ignorieren_fremdes_datenblatt():
    hand = _kontakt("Max", extern_quelle=None, extern_id=None, art="bma_alarmperson")
    eigen = _kontakt("Eva", extern_quelle="dibos_bma", extern_id="pdf:1:bma_alarmperson:eva", art="bma_alarmperson")
    fremd = _kontakt("Tom", extern_quelle="dibos_bma", extern_id="pdf:2:bma_alarmperson:tom", art="bma_alarmperson")
    kandidaten = _adoptionskandidaten(_objekt([hand, eigen, fremd]), {eigen.extern_id: eigen})

    assert set(kandidaten.values()) == {hand, eigen}
