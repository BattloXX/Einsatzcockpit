"""M5: Ortskandidaten, Serienverortung und sichere Stammdatenpunkte."""

import asyncio
import json

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanElement, KatplanLink
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.models.user import User
from app.models.wasserstelle import Wasserstelle
from app.services import geocoding
from app.services.katplan_verortung_service import kandidaten
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_fundament import _mcp, _oauth_tokens
from tests.test_mcp_katplan import inhalte as inhalte_fixture
from tests.test_mcp_katplan import rufe
from tests.test_mcp_katplan_m2 import zustand
from tests.test_mcp_katplan_schreiben import schreiben
from tests.test_mcp_strassensperren import _rpc, _rufe

inhalte = inhalte_fixture

TOOL = "katplan_elemente_verorten"


@pytest.fixture
def suche(monkeypatch):
    calls = []

    async def suchen(q, limit=5, **kwargs):
        calls.append((q, limit, kwargs))
        return [{"lat": 47.4, "lng": 9.7, "display_name": "Brücke", "typ": "bridge", "kategorie": "man_made"}]

    monkeypatch.setattr(geocoding, "search_addresses", suchen)
    return calls


def neu(info, **kwargs):
    return schreiben(info, "katplan_element_entwurf_anlegen", typ="kontrollstelle", titel="Bachbrücke", **kwargs)


def test_kandidaten_auswahl_protokoll_rollback(inhalte, suche):
    e = neu(inhalte)
    vorher = zustand(inhalte)
    result = schreiben(inhalte, TOOL, element_ids=[e["kennung"]])
    assert result["gespeichert"] is False and zustand(inhalte) == vorher
    eintrag = result["ergebnisse"][0]
    assert eintrag["status"] == "kandidaten"
    auswahl = {e["element_id"]: eintrag["kandidaten"][0]["kandidat_id"]}
    probe = schreiben(inhalte, TOOL, auswahl=auswahl, pruefen=True)
    assert probe["gespeichert"] is False and probe["ergebnisse"][0]["status"] == "gesetzt"
    assert zustand(inhalte) == vorher
    result = schreiben(inhalte, TOOL, auswahl=auswahl)
    assert result["gespeichert"] and result["ergebnisse"][0]["version"] == e["version"] + 1
    with TestingSession() as db:
        set_tenant_context(db, None)
        element = db.get(KatplanElement, e["id"])
        assert element.geo_status == "ungeprueft"
        assert json.loads(element.geometry_json) == {"type": "Point", "coordinates": [9.7, 47.4]}
        assert db.query(KatplanAenderung).filter_by(element_id=e["id"], feld="geometrie").one().quelle == "mcp"
        element.geo_status = "geprueft"
        db.commit()
    vorher = zustand(inhalte)
    result = schreiben(inhalte, TOOL, auswahl=auswahl, nur_ohne_ort=False)
    assert result["ergebnisse"][0]["status"] == "fehler" and zustand(inhalte) == vorher
    result = schreiben(inhalte, TOOL, auswahl=auswahl, nur_ohne_ort=False, ueberschreiben_geprueft=True)
    assert result["ergebnisse"][0]["status"] == "gesetzt"
    result = schreiben(inhalte, TOOL, auswahl={e["kennung"]: "unbekannt"}, nur_ohne_ort=False)
    assert result["ergebnisse"][0]["status"] == "fehler" and not result["gespeichert"]
    assert schreiben(inhalte, TOOL, element_ids=[e["kennung"]])["ergebnisse"] == []


def test_adresse_titel_viewbox_deduplizierung(inhalte, suche):
    e = neu(inhalte, adresse={"strasse": "Bachweg", "hausnummer": "2"},
            daten={"zugaenglichkeit": "Beim Steg. Vorsicht!"})
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        user.org.city, user.org.fallback_lat, user.org.fallback_lng = "Wolfurt", 47.4, 9.7
        db.commit()
        result = asyncio.run(kandidaten(db, user.org, db.get(KatplanElement, e["id"])))
    assert len(result) == 1 and result[0]["quelle"] == "adresse"
    assert result[0]["entfernung_m"] == 0 and result[0]["typ"] == "bridge"
    assert suche[0][:2] == ("Bachweg 2 Wolfurt", 3)
    assert suche[1][0] == "Bachbrücke, Beim Steg, Wolfurt"
    assert suche[1][2] == {"viewbox": (9.7 - .08, 47.4 - .08, 9.7 + .08, 47.4 + .08), "bounded": True}


@pytest.mark.parametrize("art", ["objekt", "wasserstelle"])
def test_stammdaten_kandidaten_und_geometrie_von(inhalte, suche, monkeypatch, art):
    e = neu(inhalte)
    with TestingSession() as db:
        set_tenant_context(db, None)
        if art == "objekt":
            db.query(OrgSettings).filter_by(org_id=inhalte["org_id"]).one().objekt_module_enabled = True
            flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one_or_none()
            if flag is None:
                flag = SystemSettings(key="objekt_module_enabled")
                db.add(flag)
            flag.value = "true"
        modell = Objekt if art == "objekt" else Wasserstelle
        daten = {"name": "Bauhof"} if art == "objekt" else {"bezeichnung": "Hydrant"}
        ziel = modell(org_id=inhalte["org_id"], lat=47.4, lng=9.7, **daten)
        db.add(ziel)
        db.flush()
        zid = ziel.id
        db.add(KatplanLink(org_id=inhalte["org_id"], plan_id=inhalte["plan_id"], element_id=e["id"],
                           ziel_typ=art, ziel_id=zid, rolle="info"))
        db.commit()
    result = schreiben(inhalte, TOOL, element_ids=[e["kennung"]])["ergebnisse"][0]["kandidaten"]
    assert len(result) == 1 and result[0]["quelle"] == art
    assert result[0]["ziel_id"] == zid and result[0]["genauigkeit"] == "hoch"
    result = schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["kennung"], version=e["version"],
                        felder={"geometrie_von": {art + "_id": zid}})
    assert result["geaendert"] == ["geometrie_von"]
    assert result["element"]["geometry"]["coordinates"] == [9.7, 47.4]
    assert result["element"]["geo_status"] == "ungeprueft"
    for referenz in ({art + "_id": zid, "x": 1}, {art + "_id": "1"}, {}):
        with pytest.raises(ValueError):
            schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["kennung"], version=result["version"],
                       felder={"geometrie_von": referenz})
    with pytest.raises(ValueError, match="zusammen"):
        schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["kennung"], version=result["version"],
                   felder={"geometrie_von": {art + "_id": zid}, "geometrie": None})
    with TestingSession() as db:
        set_tenant_context(db, None)
        ziel = db.get(modell, zid)
        ziel.lat = None
        db.commit()
    with pytest.raises(ValueError, match="Koordinaten"):
        schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["kennung"], version=result["version"],
                   felder={"geometrie_von": {art + "_id": zid}})
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd = db.query(User).filter_by(username=inhalte["users"]["fremd"]).one()
        db.get(modell, zid).org_id = fremd.org_id
        db.commit()
    with pytest.raises(ValueError, match="nicht gefunden"):
        schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["kennung"], version=result["version"],
                   felder={"geometrie_von": {art + "_id": zid}})
    rest = schreiben(inhalte, TOOL, element_ids=[e["kennung"]], nur_ohne_ort=False)
    assert rest["ergebnisse"][0]["kandidaten"][0]["quelle"] == "titel"


@pytest.mark.parametrize("hausnummern,verortet", [(["2", "2"], False), (["2", "3"], True), ([None], True)])
def test_mehrdeutig_und_eindeutig(inhalte, monkeypatch, hausnummern, verortet):
    async def suchen(q, limit=5, **kwargs):
        assert limit == 3
        return [{"lat": 47.4 + i * .01, "lng": 9.7, "display_name": "Bachweg", "hausnummer": n}
                for i, n in enumerate(hausnummern)]

    monkeypatch.setattr(geocoding, "search_addresses", suchen)
    e = neu(inhalte, adresse={"strasse": "Bachweg", "hausnummer": "2"}, verorten=True)
    assert e["verortung"]["verortet"] is verortet
    assert bool(e["element"]["geometry"]) is verortet
    if not verortet:
        assert len(e["verortung"]["kandidaten"]) == 2
        ausgewaehlt = e["verortung"]["kandidaten"][1]["kandidat_id"]
        result = schreiben(inhalte, TOOL, auswahl={e["element_id"]: ausgewaehlt})
        assert result["ergebnisse"][0]["status"] == "gesetzt"
    geaendert = schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=e["element_id"],
                          version=e["version"] + (not verortet), felder={"adresse": {"hausnummer": "2"}}, verorten=True)
    assert geaendert["verortung"]["verortet"] is verortet


def test_limit_typen_und_rechte(inhalte, suche):
    with TestingSession() as db:
        set_tenant_context(db, None)
        for i in range(35):
            db.add(KatplanElement(org_id=inhalte["org_id"], plan_id=inhalte["plan_id"],
                                 typ="kontrollstelle", titel=f"Steg {i}"))
        db.commit()
        erwartet = db.query(KatplanElement).filter_by(plan_id=inhalte["plan_id"], geometry_json=None).count()
    result = schreiben(inhalte, TOOL)
    assert len(result["ergebnisse"]) == 30 and result["weitere"] == erwartet - 30
    with pytest.raises(ValueError, match="30"):
        schreiben(inhalte, TOOL, element_ids=["KS-01"] * 31)
    with pytest.raises(PermissionError):
        rufe(inhalte, TOOL, plan_id=inhalte["plan_id"])
    with pytest.raises(ValueError, match="Nicht gefunden"):
        rufe(inhalte, TOOL, "fremd", plan_id=inhalte["plan_id"])
    flaeche = schreiben(inhalte, "katplan_element_entwurf_anlegen", typ="gefahrenbereich", titel="Bach")
    suche.clear()
    result = schreiben(inhalte, TOOL, element_ids=[flaeche["element_id"]])["ergebnisse"][0]
    assert result["status"] == "keine_kandidaten" and result["hinweis"] and suche == []


def test_nominatim_parameter(monkeypatch):
    params = []

    async def transport(request):
        params.append(request.url.params)
        return httpx.Response(200, json=[{"lat": "47.4", "lon": "9.7", "category": "waterway",
                                         "type": "stream", "name": "Bach"}])

    client = httpx.AsyncClient
    monkeypatch.setattr(geocoding.httpx, "AsyncClient",
                        lambda **kw: client(transport=httpx.MockTransport(transport), **kw))

    async def throttle():
        return None

    monkeypatch.setattr(geocoding, "_throttle", throttle)
    result = asyncio.run(geocoding.search_addresses("Bach", viewbox=(9, 47, 10, 48), bounded=True))
    assert params[0]["viewbox"] == "9,48,10,47" and params[0]["bounded"] == "1"
    assert result[0]["kategorie"] == "waterway" and result[0]["typ"] == "stream" and result[0]["name"] == "Bach"
    asyncio.run(geocoding.search_addresses("Bach"))
    assert "viewbox" not in params[1] and "bounded" not in params[1]


def test_http_wrapper(client, inhalte, suche):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    tools = {t["name"]: t for t in _rpc(_mcp(client, token, "tools/list", {}, 13))["result"]["tools"]}
    assert {"auswahl", "ueberschreiben_geprueft", "pruefen"} <= set(tools[TOOL]["inputSchema"]["properties"])
    e = neu(inhalte)
    result = _rufe(client, token, TOOL, plan_id=inhalte["plan_id"], element_ids=[e["element_id"]])
    assert result["ergebnisse"][0]["status"] == "kandidaten" and not result["gespeichert"]
