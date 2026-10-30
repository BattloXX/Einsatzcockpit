"""Arbeitsfolge, Planversionen und lesende MCP-Tenant-Grenzen."""

import pytest
from sqlalchemy import event

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.objekt import (
    DOKUMENT_STATUS_FERTIG,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektKartenObjekt,
    ObjektKartenVorschlag,
)
from app.services import objekt_georef_service as geo
from app.services import objekt_karte_vorschlag_service as vorschlag
from app.services.objekt_bsp_arbeitsliste_service import arbeitsliste_lesen
from app.services.objekt_service import erstelle_arbeitskopie
from tests.test_mcp_objekt_georef import _rolle_setzen
from tests.test_mcp_objekt_karte import _rufe
from tests.test_mcp_objekte import _mcp, _seed, _token
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte
from tests.test_objekt_georef_uebernahme import version as version


@pytest.fixture
def bsp(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.get(ObjektDokument, plan["dokument_id"]).status = DOKUMENT_STATUS_FERTIG
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one()
        seite.dokumentart, seite.titel = "lageplan", "Lageplan"
        db.commit()
    return plan


def _lesen(plan, **kwargs):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        return arbeitsliste_lesen(db, plan["org_id"], **kwargs)


def _georef(db, plan):
    return geo.referenzpunkte(db, db.get(Objekt, plan["objekt_id"]), plan["dokument_id"],
                             1, "setzen", punkte(), "aehnlichkeit", plan["users"]["u"])["georeferenz"]


def test_ohne_georef_und_lesend(bsp):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        mutationen = []
        event.listen(db, "before_flush", lambda *args: mutationen.append(True))
        antwort = arbeitsliste_lesen(db, bsp["org_id"])
        assert not mutationen and not db.dirty and not db.new
    e = antwort["eintraege"][0]
    assert e["naechster_schritt"] == "georef_zuordnen"
    assert e["objekt_name"] == "Musterbetrieb" and e["titel"] == "Lageplan"
    assert e["georef"] is None and e["vorversion"] is None
    assert e["dokument_versionsnummer"] == 1 and e["rotation"] == 0
    assert len(antwort["ablauf"]) == 5


def test_vorversion(version):
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        db.get(ObjektDokument, version["neu_id"]).status = DOKUMENT_STATUS_FERTIG
        db.query(ObjektDokumentSeite).filter_by(dokument_id=version["neu_id"]).one().dokumentart = "brandschutzplan"
        db.commit()
    e = _lesen(version)["eintraege"][0]
    assert e["naechster_schritt"] == "georef_uebernehmen"
    assert e["vorversion"] == {"dokument_id": version["dokument_id"], "seite": 1,
                               "georef_id": version["georef_id"], "status": "gueltig"}


def test_gueltig_vorschlag_marker_und_veraltet(bsp):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        g = _georef(db, bsp)
        db.commit()
    e = _lesen(bsp)["eintraege"][0]
    assert e["naechster_schritt"] == "symbole_erfassen"
    assert e["georef"]["id"] == g["id"] and e["georef"]["status"] == "gueltig"
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        db.add(ObjektKartenVorschlag(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"], operation="hinzufuegen",
                                   symbol_code="fsd", quelle_dokument_id=bsp["dokument_id"], quelle_seite=1,
                                   freigabe_status="offen"))
        db.commit()
    e = _lesen(bsp)["eintraege"][0]
    assert e["naechster_schritt"] == "vorschlaege_pruefen" and e["offene_vorschlaege"] == 1
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        db.query(ObjektKartenVorschlag).filter_by(objekt_id=bsp["objekt_id"]).one().freigabe_status = "angenommen"
        db.add(ObjektKartenObjekt(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"], typ="fsd",
                                quelle_dokument_hash=g["dokument_hash"], position_status="bestaetigt"))
        db.commit()
    assert _lesen(bsp)["eintraege"] == []
    e = _lesen(bsp, nur_offen=False)["eintraege"][0]
    assert e["naechster_schritt"] == "aktuell" and e["marker_aus_planversion"] == 1
    (bsp["pfad"] / "plan.pdf").write_bytes(b"veraendert")
    e = _lesen(bsp)["eintraege"][0]
    assert e["naechster_schritt"] == "georef_zuordnen" and e["georef"]["status"] == "veraltet"


@pytest.mark.parametrize("fall", ["andere_art", "alte_version", "arbeitskopie", "nicht_fertig"])
def test_filter(bsp, fall):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        d = db.get(ObjektDokument, bsp["dokument_id"])
        s = db.query(ObjektDokumentSeite).filter_by(dokument_id=d.id).one()
        if fall == "andere_art":
            s.dokumentart = "bma_datenblatt"
        elif fall == "alte_version":
            d.ist_aktuelle_version = False
        elif fall == "nicht_fertig":
            d.status = "verarbeitung"
        else:
            kopie = erstelle_arbeitskopie(db, db.get(Objekt, bsp["objekt_id"]), bsp["users"]["u"])
            d.objekt_id = s.objekt_id = kopie.id
        db.commit()
    assert _lesen(bsp)["eintraege"] == []


def test_limit_sortierung_und_hash_einmal(bsp, monkeypatch):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        _georef(db, bsp)
        db.add(ObjektDokumentSeite(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"],
                                  dokument_id=bsp["dokument_id"], seiten_nr=2,
                                  dokumentart="brandschutzplan", bild_pfad="plan.png"))
        db.flush()
        geo.referenzpunkte(db, db.get(Objekt, bsp["objekt_id"]), bsp["dokument_id"],
                           2, "setzen", punkte(), "aehnlichkeit", bsp["users"]["u"])
        db.commit()
    aufrufe = []
    original = vorschlag._hash

    def zaehlen(d):
        aufrufe.append(d.id)
        return original(d)

    monkeypatch.setattr(vorschlag, "_hash", zaehlen)
    antwort = _lesen(bsp, limit=1)
    assert antwort["anzahl_gesamt"] == 2 and antwort["abgeschnitten"] is True
    assert [e["seite"] for e in antwort["eintraege"]] == [1]
    assert aufrufe == [bsp["dokument_id"]]
    for limit in (0, 201):
        with pytest.raises(ValueError, match="limit"):
            _lesen(bsp, limit=limit)


def test_mcp_org_rechte_und_limit(client, bsp):
    fremd = _seed("arbeitsliste-fremd", {"u": "readonly"})
    token_fremd = _token(client, fremd, "u")
    assert _rufe(client, token_fremd, "objekt_karte_bsp_arbeitsliste")["eintraege"] == []
    assert _rufe(client, token_fremd, "objekt_karte_bsp_arbeitsliste",
                 objekt_id=bsp["objekt_id"])["eintraege"] == []
    _rolle_setzen(bsp, "readonly")
    token = _token(client, bsp, "u")
    assert "objekt_karte_bsp_arbeitsliste" in _mcp(client, token, "tools/list", {}, 1).text
    assert _rufe(client, token, "objekt_karte_bsp_arbeitsliste")["anzahl_gesamt"] == 1
    for limit in (0, 201):
        assert "__fehler__" in _rufe(client, token, "objekt_karte_bsp_arbeitsliste", limit=limit)
    beschreibung = TOOLS["objekt_karte_bsp_arbeitsliste"].description
    assert beschreibung.isascii() and "ausdruecklicher Bestaetigung" in beschreibung


def test_schrittprioritaet_und_gebuendelte_abfragen(bsp):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        _georef(db, bsp)
        db.add(ObjektDokumentSeite(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"],
                                  dokument_id=bsp["dokument_id"], seiten_nr=2,
                                  dokumentart="lageplan", bild_pfad="plan.png"))
        db.commit()
        verbindung = db.connection()
        abfragen = []

        def zaehlen(conn, cursor, statement, parameters, context, executemany):
            abfragen.append(statement)

        event.listen(verbindung, "before_cursor_execute", zaehlen)
        try:
            antwort = arbeitsliste_lesen(db, bsp["org_id"])
        finally:
            event.remove(verbindung, "before_cursor_execute", zaehlen)
        assert len(abfragen) == 5
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in abfragen)
    assert [(e["seite"], e["naechster_schritt"]) for e in antwort["eintraege"]] == [
        (2, "georef_zuordnen"), (1, "symbole_erfassen"),
    ]


def test_geschossplaene_nicht_georeferenzieren(bsp):
    # Lageplan vorhanden: weitere BSP-Seiten ohne Georef erscheinen nicht.
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        for nr, titel in ((2, "Erdgeschoss"), (3, "Kanalplan West"), (4, "Legende")):
            db.add(ObjektDokumentSeite(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"],
                                      dokument_id=bsp["dokument_id"], seiten_nr=nr, titel=titel,
                                      dokumentart="brandschutzplan", bild_pfad="plan.png"))
        db.commit()
    eintraege = _lesen(bsp)["eintraege"]
    assert [(e["seite"], e["naechster_schritt"]) for e in eintraege] == [(1, "georef_zuordnen")]


def test_ohne_lageplan_nur_klassifizieren(bsp):
    with SessionLocal() as db:
        set_tenant_context(db, bsp["org_id"])
        db.query(ObjektDokumentSeite).filter_by(dokument_id=bsp["dokument_id"]).one().dokumentart = "brandschutzplan"
        db.add(ObjektDokumentSeite(org_id=bsp["org_id"], objekt_id=bsp["objekt_id"],
                                  dokument_id=bsp["dokument_id"], seiten_nr=2, titel="Erdgeschoss",
                                  dokumentart="brandschutzplan", bild_pfad="plan.png"))
        db.commit()
    eintraege = _lesen(bsp)["eintraege"]
    assert len(eintraege) == 1
    e = eintraege[0]
    assert e["naechster_schritt"] == "lageplan_klassifizieren" and e["seite"] is None
    assert e["dokument_id"] == bsp["dokument_id"] and "lageplan" in e["hinweis"]
