"""MCP-Rechte, Transaktionen und Mandantengrenzen der Kartenvorschlaege."""
import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektChange, ObjektKartenObjekt, ObjektKartenVorschlag
from app.services.objekt_service import erstelle_arbeitskopie
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _rufe, _seed, _token


def test_dry_run_batch_atomar_idempotenz_und_verwerfen(client, karte):
    token = _token(client, karte, "u")
    argumente = {"objekt_id": karte["objekt_id"], "aktion": "anlegen", "vorschlaege": [
        {"symbol_code": "tor", "lat": 47.11, "lng": 9.22}, {"symbol_code": "bmz"},
    ]}
    trocken = _rufe(client, token, "objekt_karte_vorschlaege", **argumente, pruefen=True)
    assert not trocken["gespeichert"] and trocken["vorschlaege"][0]["warnungen"]
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert db.query(ObjektKartenVorschlag).count() == db.query(ObjektChange).count() == 0
        vorher = [(m.id, m.revision) for m in db.query(ObjektKartenObjekt).all()]
    echt = _rufe(client, token, "objekt_karte_vorschlaege", **argumente)
    nochmal = _rufe(client, token, "objekt_karte_vorschlaege", **argumente)
    assert echt["zusammenfassung"]["neu"] == 2
    assert nochmal["zusammenfassung"]["unveraendert_offen"] == 2
    assert all(v["ergebnis"] == "neu" for v in echt["vorschlaege"])
    assert all(v["ergebnis"] == "unveraendert_offen" for v in nochmal["vorschlaege"])
    assert [{k: w for k, w in v.items() if k != "ergebnis"} for v in echt["vorschlaege"]] == [
        {k: w for k, w in v.items() if k != "ergebnis"} for v in nochmal["vorschlaege"]
    ]
    gelistet = _rufe(client, token, "objekt_karte_vorschlaege", 
                                  objekt_id=karte["objekt_id"], aktion="auflisten")
    assert len(gelistet["vorschlaege"]) == 2
    vid = echt["vorschlaege"][0]["id"]
    for trockenlauf in (True, False):
        resultat = _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=karte["objekt_id"], aktion="aendern",
                         vorschlag_id=vid, vorschlaege=[{"label": "Neu"}], pruefen=trockenlauf)
        assert resultat["gespeichert"] != trockenlauf
    assert _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=karte["objekt_id"], aktion="verwerfen",
                 vorschlag_id=vid)["vorschlaege"][0]["freigabe_status"] == "abgelehnt"
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert vorher == [(m.id, m.revision) for m in db.query(ObjektKartenObjekt).all()]
    # Ein Fehler rollt auch vorherige Elemente desselben Batches zurueck.
    argumente["vorschlaege"] = [{"symbol_code": "tor", "label": "Rollback"}, {"symbol_code": "???"}]
    assert "__fehler__" in _rufe(client, token, "objekt_karte_vorschlaege", **argumente)
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        assert db.query(ObjektKartenVorschlag).filter_by(label="Rollback").count() == 0


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "objekt_verwalter", "org_admin"])
def test_rechte(client, rolle):
    seed = _seed(f"vorschlag-rechte-{rolle}", {"u": rolle})
    token = _token(client, seed, "u")
    assert "__fehler__" not in _rufe(client, token, "objekt_karte_pruefen", objekt_id=seed["objekt_id"])
    assert "__fehler__" not in _rufe(client, token, "objekt_karte_vorschlaege", 
                                      objekt_id=seed["objekt_id"], aktion="auflisten")
    antwort = _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=seed["objekt_id"], aktion="anlegen",
                    vorschlaege=[{"symbol_code": "geometrie"}])
    assert ("__fehler__" not in antwort) == (rolle in {"objekt_verwalter", "org_admin"})


def test_fremder_tenant_arbeitskopie_und_batchlimit(client, karte):
    fremd = _seed("mcp-vorschlag-fremd", {"u": "objekt_verwalter"})
    fremd_token = _token(client, fremd, "u")
    assert "__fehler__" in _rufe(client, fremd_token, "objekt_karte_vorschlaege", 
                                  objekt_id=karte["objekt_id"], aktion="auflisten")
    assert "__fehler__" in _rufe(client, fremd_token, "objekt_karte_pruefen", objekt_id=karte["objekt_id"])
    token = _token(client, karte, "u")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        kopie = erstelle_arbeitskopie(db, db.get(Objekt, karte["objekt_id"]), karte["users"]["u"])
        db.commit()
        kopie_id = kopie.id
    assert "__fehler__" in _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=kopie_id, aktion="anlegen",
                                vorschlaege=[{"symbol_code": "tor"}])
    assert "__fehler__" in _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=karte["objekt_id"],
                                aktion="anlegen", vorschlaege=[{"symbol_code": "tor"}] * 31)
