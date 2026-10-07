"""HTTP-Regressionen fuer die MCP-Wasserstellen-Werkzeuge."""

import json

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.user import AuditLog
from app.models.wasserstelle import Wasserstelle
from app.services.wasserstelle_service import lade_wasserstellen_im_umkreis
from tests.test_mcp_objekte import _mcp, _seed, _token

WASSERSTELLEN_TOOLS = {
    "wasserstellen_suchen",
    "wasserstelle_lesen",
    "wasserstelle_anlegen",
    "wasserstelle_aktualisieren",
    "wasserstelle_deaktivieren",
}


def _rpc(response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")][-1])
    return response.json()


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 90)
    assert response.status_code == 200, response.text
    result = _rpc(response)["result"]
    if result.get("isError"):
        return {"__fehler__": json.dumps(result["content"], ensure_ascii=False)}
    return result.get("structuredContent", {}).get("result", json.loads(result["content"][0]["text"]))


def _wasserstelle(org_id: int, **werte) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        wasserstelle = Wasserstelle(org_id=org_id, **werte)
        db.add(wasserstelle)
        db.commit()
        return wasserstelle.id
    finally:
        db.close()


def _laden(wasserstelle_id: int) -> Wasserstelle:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db_wasserstelle = db.get(Wasserstelle, wasserstelle_id)
        assert db_wasserstelle is not None
        db.expunge(db_wasserstelle)
        return db_wasserstelle
    finally:
        db.close()


def test_suchen_filter_umkreis_validierung_und_paging(client):
    seed = _seed("wss-suche", {"admin": "org_admin"})
    token = _token(client, seed, "admin")
    _wasserstelle(seed["org_id"], bezeichnung="Alpha", typ="ueberflur", lat=48.2000, lng=16.3700, hinweis="Nord")
    _wasserstelle(
        seed["org_id"], bezeichnung="Bravo", typ="unterflur", lat=48.2002, lng=16.3700, hinweis="Sued Hinweis"
    )
    _wasserstelle(seed["org_id"], bezeichnung="Charlie", typ="ueberflur", lat=48.2100, lng=16.3700, status="wartung")
    _wasserstelle(
        seed["org_id"], bezeichnung="Defekt", typ="saugstelle", lat=48.2001, lng=16.3700, status="defekt", aktiv=False
    )

    assert [w["bezeichnung"] for w in _rufe(client, token, "wasserstellen_suchen", q="Hinweis")["wasserstellen"]] == [
        "Bravo"
    ]
    assert [
        w["bezeichnung"] for w in _rufe(client, token, "wasserstellen_suchen", typ="unterflur")["wasserstellen"]
    ] == ["Bravo"]
    assert [
        w["bezeichnung"] for w in _rufe(client, token, "wasserstellen_suchen", status="wartung")["wasserstellen"]
    ] == ["Charlie"]
    aktive = _rufe(client, token, "wasserstellen_suchen", nur_aktive=True)["wasserstellen"]
    assert {w["bezeichnung"] for w in aktive} == {"Alpha", "Bravo", "Charlie"}

    umkreis = _rufe(client, token, "wasserstellen_suchen", lat=48.2000, lng=16.3700, radius_m=50)
    assert [w["bezeichnung"] for w in umkreis["wasserstellen"]] == ["Alpha", "Defekt", "Bravo"]
    assert all("entfernung_m" in w for w in umkreis["wasserstellen"])
    assert [w["entfernung_m"] for w in umkreis["wasserstellen"]] == sorted(
        w["entfernung_m"] for w in umkreis["wasserstellen"]
    )
    assert "__fehler__" in _rufe(client, token, "wasserstellen_suchen", radius_m=100)
    assert "__fehler__" in _rufe(client, token, "wasserstellen_suchen", typ="unbekannt")

    page = _rufe(client, token, "wasserstellen_suchen", limit=2, seite=2)
    assert page["gesamt"] == 4 and page["seite"] == 2 and len(page["wasserstellen"]) == 2


def test_lesen_liefert_zeitstempel_und_ist_org_isoliert(client):
    eigene = _seed("wss-lesen-eigen", {"admin": "org_admin"})
    fremde = _seed("wss-lesen-fremd", {"admin": "org_admin"})
    eigene_id = _wasserstelle(eigene["org_id"], bezeichnung="Eigene", typ="ueberflur", lat=48.2, lng=16.37)
    fremde_id = _wasserstelle(fremde["org_id"], bezeichnung="Fremde", typ="ueberflur", lat=48.2, lng=16.37)
    token = _token(client, eigene, "admin")

    gelesen = _rufe(client, token, "wasserstelle_lesen", wasserstelle_id=eigene_id)
    assert {"id", "bezeichnung", "typ", "status", "aktiv", "erstellt_am"} <= set(gelesen)
    assert gelesen["erstellt_am"].endswith("Z")
    fremd = _rufe(client, token, "wasserstelle_lesen", wasserstelle_id=fremde_id)
    assert "Wasserstelle nicht gefunden" in fremd["__fehler__"]


def test_anlegen_validiert_dubletten_und_auditiert(client):
    seed = _seed("wss-anlegen", {"admin": "org_admin"})
    token = _token(client, seed, "admin")
    basis_id = _wasserstelle(seed["org_id"], bezeichnung="Basis", typ="ueberflur", lat=48.2000, lng=16.3700)

    neu = _rufe(
        client,
        token,
        "wasserstelle_anlegen",
        bezeichnung="Neu",
        typ="saugstelle",
        lat=48.2020,
        lng=16.3700,
        ergiebigkeit_l_min=900,
    )
    assert neu["quelle"] == "manuell" and neu["aktiv"] is True
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(AuditLog).filter_by(action="wasserstelle.mcp_angelegt", entity_id=neu["id"]).count() == 1
    finally:
        db.close()
    for argumente in (
        {"bezeichnung": "Typ", "typ": "falsch", "lat": 48.3, "lng": 16.3},
        {"bezeichnung": "Breite", "typ": "ueberflur", "lat": 95, "lng": 16.3},
        {"bezeichnung": "Menge", "typ": "ueberflur", "lat": 48.3, "lng": 16.3, "ergiebigkeit_l_min": -1},
    ):
        assert "__fehler__" in _rufe(client, token, "wasserstelle_anlegen", **argumente)
    assert "__fehler__" in _rufe(
        client, token, "wasserstelle_anlegen", bezeichnung=" basis ", typ="brunnen", lat=48.3, lng=16.3
    )
    nah = _rufe(client, token, "wasserstelle_anlegen", bezeichnung="Nah", typ="ueberflur", lat=48.20005, lng=16.3700)
    assert "__fehler__" in nah
    bestaetigt = _rufe(
        client,
        token,
        "wasserstelle_anlegen",
        bezeichnung="Nah",
        typ="ueberflur",
        lat=48.20005,
        lng=16.3700,
        duplikat_bestaetigt=True,
    )
    assert bestaetigt["id"] != basis_id
    fern = _rufe(client, token, "wasserstelle_anlegen", bezeichnung="Fern", typ="ueberflur", lat=48.20045, lng=16.3700)
    assert "__fehler__" not in fern


def test_aktualisieren_aendert_nur_felder_und_wartung_bleibt_aktiv(client):
    seed = _seed("wss-update", {"admin": "org_admin"})
    token = _token(client, seed, "admin")
    wasserstelle_id = _wasserstelle(
        seed["org_id"], bezeichnung="Alt", typ="ueberflur", lat=48.2, lng=16.37, ergiebigkeit_l_min=800
    )

    result = _rufe(
        client, token, "wasserstelle_aktualisieren", wasserstelle_id=wasserstelle_id, felder={"hinweis": "Neu"}
    )
    assert result["geaenderte_felder"] == [{"feld": "hinweis", "vorher": None, "nachher": "Neu"}]
    unveraendert = _laden(wasserstelle_id)
    assert (unveraendert.bezeichnung, unveraendert.typ, unveraendert.lat, unveraendert.ergiebigkeit_l_min) == (
        "Alt",
        "ueberflur",
        48.2,
        800,
    )
    assert "__fehler__" in _rufe(
        client, token, "wasserstelle_aktualisieren", wasserstelle_id=wasserstelle_id, felder={"unbekannt": "x"}
    )
    wartung = _rufe(
        client, token, "wasserstelle_aktualisieren", wasserstelle_id=wasserstelle_id, felder={"status": "wartung"}
    )
    assert wartung["wasserstelle"]["status"] == "wartung" and wartung["wasserstelle"]["aktiv"] is True


def test_deaktivieren_und_reaktivieren(client):
    seed = _seed("wss-deaktivieren", {"admin": "org_admin"})
    token = _token(client, seed, "admin")
    wasserstelle_id = _wasserstelle(
        seed["org_id"], bezeichnung="Ausfall", typ="ueberflur", lat=48.2, lng=16.37, hinweis="Alt"
    )

    deaktiviert = _rufe(
        client, token, "wasserstelle_deaktivieren", wasserstelle_id=wasserstelle_id, grund="Ventil kaputt"
    )
    assert deaktiviert["status"] == "defekt" and deaktiviert["aktiv"] is False
    assert "[Deaktiviert " in deaktiviert["hinweis"] and "reaktivieren" in deaktiviert
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert not lade_wasserstellen_im_umkreis(db, seed["org_id"], 48.2, 16.37, 100)
    finally:
        db.close()
    reaktiviert = _rufe(
        client, token, "wasserstelle_aktualisieren", wasserstelle_id=wasserstelle_id, felder={"status": "bereit"}
    )
    assert reaktiviert["wasserstelle"]["aktiv"] is True


def test_schreiben_ist_org_isoliert_und_objekt_verwalter_hat_keinen_zugriff(client):
    eigene = _seed("wss-schreiben-eigen", {"admin": "org_admin", "objekt": "objekt_verwalter"})
    fremde = _seed("wss-schreiben-fremd", {"admin": "org_admin"})
    fremde_id = _wasserstelle(
        fremde["org_id"], bezeichnung="Fremde", typ="ueberflur", lat=48.2, lng=16.37, hinweis="Original"
    )
    admin_token = _token(client, eigene, "admin")
    assert _rufe(client, admin_token, "wasserstellen_suchen", q="Fremde")["wasserstellen"] == []
    assert "__fehler__" in _rufe(
        client, admin_token, "wasserstelle_aktualisieren", wasserstelle_id=fremde_id, felder={"hinweis": "Nein"}
    )
    assert "__fehler__" in _rufe(client, admin_token, "wasserstelle_deaktivieren", wasserstelle_id=fremde_id)
    fremde_wasserstelle = _laden(fremde_id)
    assert fremde_wasserstelle.hinweis == "Original" and fremde_wasserstelle.aktiv is True

    objekt_token = _token(client, eigene, "objekt")
    listed = _mcp(client, objekt_token, "tools/list", {}, 91).text
    assert WASSERSTELLEN_TOOLS <= set(TOOLS) and not any(name in listed for name in WASSERSTELLEN_TOOLS)
    assert "__fehler__" in _rufe(client, objekt_token, "wasserstellen_suchen")
