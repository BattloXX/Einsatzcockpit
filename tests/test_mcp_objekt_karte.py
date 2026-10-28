"""HTTP-Regressionen fuer lesende MCP-Lagekarten und Tenant-Grenzen."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import TOOLS
from app.mcp.tools.objekt_karte import objekt_karte_lesen, objekt_karte_symbolkatalog
from app.models.master import OrgSettings
from app.models.objekt import Objekt, ObjektChange, ObjektKartenObjekt, ObjektSymbol
from app.models.user import User, UserRole
from app.services.objekt_karte_service import ObjektKartenFehler, karten_objekt_neu
from app.services.objekt_service import erstelle_arbeitskopie
from app.services.objekt_symbol_service import seed_objekt_symbole
from tests.test_mcp_objekte import _mcp, _rufe, _seed, _token

KARTEN_TOOLS = {"objekt_karte_lesen", "objekt_karte_symbolkatalog"}


@pytest.fixture
def karte():
    seed = _seed(f"mcp-karte-{uuid4().hex[:12]}", {"u": "objekt_verwalter"})
    with SessionLocal() as db:
        set_tenant_context(db, None)
        objekt = db.get(Objekt, seed["objekt_id"])
        objekt.lat, objekt.lng = 47.1, 9.2
        seed_objekt_symbole(db, seed["org_id"])
        db.add(ObjektSymbol(org_id=seed["org_id"], code="tor", name="Werkstor", stil="box", text="T"))
        db.add_all([
            ObjektKartenObjekt(org_id=seed["org_id"], objekt_id=objekt.id, typ="tor", lat=47.11, lng=9.22,
                               label="Zufahrt", rotation=450, sort=2),
            ObjektKartenObjekt(org_id=seed["org_id"], objekt_id=objekt.id, typ="geometrie", sort=1,
                               geometry_json=json.dumps({"type": "LineString", "coordinates": [[9, 47], [9.1, 47.1]]})),
        ])
        db.commit()
    return seed


def test_lesen_werte_sortierung_und_katalog(client, karte):
    token = _token(client, karte, "u")
    gelesen = _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"])
    assert gelesen["objekt"] == {"id": karte["objekt_id"], "name": "Musterbetrieb", "lat": 47.1, "lng": 9.2}
    assert gelesen["anzahl_marker"] == 2
    assert not gelesen["hat_arbeitskopie"] and gelesen["arbeitskopie_id"] is None
    assert "ungeprüfter Bestand" in gelesen["hinweis"]
    linie, punkt = gelesen["marker"]
    assert linie["geometry"] == {"type": "LineString", "coordinates": [[9, 47], [9.1, 47.1]]}
    assert punkt == {"id": punkt["id"], "typ": "tor", "symbolname": "Werkstor", "lat": 47.11, "lng": 9.22,
                     "geometry": None, "label": "Zufahrt", "rotation": 90, "sort": 2,
                     "position_status": "ungeprueft", "revision": 1, "quelle_dokument_id": None,
                     "quelle_seite": None, "genauigkeit_m": None, "geprueft_von": None, "geprueft_am": None}
    katalog = _rufe(client, token, "objekt_karte_symbolkatalog")["symbole"]
    symbole = {s["code"]: s for s in katalog}
    assert symbole["tor"] == {"code": "tor", "name": "Werkstor", "stil": "box", "text": "T",
                              "system": False, "einsatzkritisch": False}
    for code in ("fsd", "bmz", "fbf", "hauptzugang", "nebenzugang", "dlk_stellplatz",
                 "gefahr_gas", "hydrant_ueberflur", "hydrant_unterflur"):
        assert symbole[code]["einsatzkritisch"] and symbole[code]["system"]
    assert not symbole["sammelplatz"]["einsatzkritisch"]


def test_lesetools_fuehren_nur_selects_aus(karte):
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        context = MCPContext(db, db.get(User, karte["users"]["u"]), karte["org_id"])
        statements = []
        verbindung = db.connection()

        def merken(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(verbindung, "before_cursor_execute", merken)
        try:
            asyncio.run(objekt_karte_lesen(context, karte["objekt_id"], arbeitskopie=True))
            asyncio.run(objekt_karte_symbolkatalog(context))
        finally:
            event.remove(verbindung, "before_cursor_execute", merken)
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        assert not db.new and not db.dirty and not db.deleted
        assert db.query(ObjektChange).count() == 0


def test_arbeitskopie_und_direkte_kopie(client, karte):
    token = _token(client, karte, "u")
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        kopie = erstelle_arbeitskopie(db, db.get(Objekt, karte["objekt_id"]), karte["users"]["u"])
        db.flush()
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=kopie.id, typ="tor").one()
        marker.label = "Arbeitsstand"
        db.commit()
        kopie_id = kopie.id
    basis = _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"])
    kopie = _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"], arbeitskopie=True)
    direkt = _rufe(client, token, "objekt_karte_lesen", objekt_id=kopie_id)
    assert basis["hat_arbeitskopie"] and kopie["hat_arbeitskopie"]
    assert basis["arbeitskopie_id"] == kopie["objekt"]["id"] == kopie_id
    assert basis["marker"][1]["label"] == "Zufahrt"
    assert kopie["marker"][1]["label"] == "Arbeitsstand"
    assert kopie["basis_objekt_id"] == direkt["basis_objekt_id"] == karte["objekt_id"]
    assert direkt["marker"] == kopie["marker"] and not direkt["hat_arbeitskopie"]


def test_tenant_isolation_und_leere_karte(client, karte):
    fremd = _seed("mcp-karte-fremd", {"u": "objekt_verwalter"})
    token = _token(client, fremd, "u")
    assert "__fehler__" in _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"])
    assert "__fehler__" in _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"], arbeitskopie=True)
    assert _rufe(client, token, "objekt_karte_symbolkatalog")["symbole"] == []
    gelesen = _rufe(client, token, "objekt_karte_lesen", objekt_id=fremd["objekt_id"])
    assert gelesen["marker"] == [] and gelesen["anzahl_marker"] == 0


@pytest.mark.parametrize("rolle", ["objekt_verwalter", "readonly", "recorder", "incident_leader", "org_admin"])
def test_leserechte_live_und_modul(client, rolle):
    seed = _seed(f"karte-recht-{rolle}", {"u": rolle})
    token = _token(client, seed, "u")
    assert KARTEN_TOOLS <= TOOLS.keys()
    listed = _mcp(client, token, "tools/list", {}, 1).text
    assert all(name in listed for name in KARTEN_TOOLS)
    assert "__fehler__" not in _rufe(client, token, "objekt_karte_lesen", objekt_id=seed["objekt_id"])
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().objekt_module_enabled = False
        db.commit()
    for tool in KARTEN_TOOLS:
        argumente = {"objekt_id": seed["objekt_id"]} if tool == "objekt_karte_lesen" else {}
        assert "__fehler__" in _rufe(client, token, tool, **argumente)
    assert all(name not in _mcp(client, token, "tools/list", {}, 1).text for name in KARTEN_TOOLS)


def test_entzogene_rolle_wird_live_abgewiesen(client, karte):
    token = _token(client, karte, "u")
    with SessionLocal() as db:
        for rolle in db.query(UserRole).filter_by(user_id=karte["users"]["u"]).all():
            db.delete(rolle)
        db.commit()
    assert all(name not in _mcp(client, token, "tools/list", {}, 1).text for name in KARTEN_TOOLS)
    assert "__fehler__" in _rufe(client, token, "objekt_karte_lesen", objekt_id=karte["objekt_id"])
    assert "__fehler__" in _rufe(client, token, "objekt_karte_symbolkatalog")


def test_service_validiert_org_eigene_symbole(karte):
    fremd = _seed("karte-service-fremd", {"u": "objekt_verwalter"})
    with SessionLocal() as db:
        set_tenant_context(db, None)
        objekt = db.get(Objekt, karte["objekt_id"])
        eintrag = karten_objekt_neu(db, objekt, {"typ": "tor", "lat": 47, "lng": 9}, user_id=karte["users"]["u"])
        assert eintrag["typ"] == "tor"
        assert db.query(ObjektChange).filter_by(objekt_id=objekt.id, feld="symbol_neu").count() == 1
        with pytest.raises(ObjektKartenFehler, match="Unbekannter Symboltyp"):
            karten_objekt_neu(db, db.get(Objekt, fremd["objekt_id"]), {"typ": "tor", "lat": 47, "lng": 9},
                              user_id=fremd["users"]["u"])
