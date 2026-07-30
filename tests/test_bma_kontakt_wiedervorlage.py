"""Ein fehlender importierter Kontakt macht einen bestätigten Satz wieder offen."""
import json

from app.models.bma_import import BmaImportSatz
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
from app.services.bma_import.bma_sync import ist_offener_vorschlag


def test_geloeschter_bma_kontakt_wird_live_erkannt():
    kontakt = {"extern_id": "pdf:123:bma_alarmperson:max", "name": "Max", "art": "bma_alarmperson"}
    satz = BmaImportSatz(org_id=1, extern_id="pdf:123", quell_hash="gleich",
                         bestaetigt_hash="gleich",
                         rohdaten_json=json.dumps({"anlage": {}, "kontakte": [kontakt]}))
    objekt = Objekt(org_id=1, nummer="1", name="Test", status=OBJEKT_STATUS_FREIGEGEBEN)
    objekt.kontakte = []
    assert ist_offener_vorschlag(satz, objekt)
