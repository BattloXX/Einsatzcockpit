"""MCP-Erweiterungen Ausbau 3: beenden, Teams-Warteschlange, Freigabelink, Kennzahlen, Listenfilter."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.crypto import encrypt_secret
from app.models.invitation import OrgPartner
from app.models.master import FireDept, OrgSettings
from app.models.road_closure import (
    RoadClosure,
    RoadClosureChange,
    RoadClosureNotification,
    RoadClosureShare,
    RoadClosureTeamsConfig,
)
from app.services import road_closure_notify_service, road_closure_stats_service
from tests.test_mcp_objekte import _mcp, _token
from tests.test_mcp_strassensperren import _bereit, _db, _rpc, _rufe, _sperre

NEUE_TOOLS = {"strassensperre_beenden", "strassensperre_teams_senden", "strassensperre_freigabelink",
              "strassensperren_kennzahlen"}
POINT = {"type": "Point", "coordinates": [9.745, 47.47]}
HOOK = "https://example.invalid/hook/SECRET-MCP-HOOK"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _setup(slug: str):
    seed = _bereit(f"{slug}-{uuid4().hex[:6]}", {"obj": "objekt_verwalter", "leser": "readonly"})
    return seed


def _partner_sperre(org_id: int) -> int:
    db = _db()
    try:
        partner = FireDept(slug=f"mcp-partner-{uuid4().hex}", name="MCP-Partner", timezone="Europe/Vienna")
        db.add(partner)
        db.flush()
        db.add(OrgSettings(org_id=partner.id, strassensperren_modul_aktiv=True))
        db.add(OrgPartner(org_id=partner.id, partner_org_id=org_id))
        closure = RoadClosure(org_id=partner.id, title=f"Partner {uuid4().hex}", valid_from=_now() - timedelta(hours=1),
                              restriction_type="closed")
        db.add(closure)
        db.flush()
        db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_id))
        db.commit()
        return closure.id
    finally:
        db.close()


def test_neue_tools_rollen_schema_und_aliase(client) -> None:
    seed = _setup("mcp-a3-tools")
    obj = _token(client, seed, "obj")
    tools = {t["name"]: t for t in _rpc(_mcp(client, obj, "tools/list", {}, 95))["result"]["tools"]}
    assert NEUE_TOOLS <= set(tools)
    assert tools["strassensperre_freigabelink"]["inputSchema"]["properties"]["aktion"]["enum"] == [
        "abrufen", "erzeugen", "widerrufen"]
    for name, alias in (("strassensperren_liste", "road_closures_list"), ("strassensperre_lesen", "road_closures_get"),
                        ("strassensperre_anlegen", "road_closures_create"),
                        ("strassensperre_aktualisieren", "road_closures_update"),
                        ("strassensperre_beenden", "road_closures_close"),
                        ("strassensperre_teams_senden", "road_closures_publish"),
                        ("strassensperre_freigabelink", "road_closures_share_link"),
                        ("strassensperren_kennzahlen", "road_closures_stats"),
                        ("strassensperren_entlang_route", "road_closures_route_check")):
        assert alias in tools[name]["description"], name

    leser = _token(client, seed, "leser")
    closure_id = _sperre(seed["org_id"], "Rolle", POINT)
    for tool, args in (("strassensperre_beenden", {"road_closure_id": closure_id, "grund": "x"}),
                       ("strassensperre_teams_senden", {"road_closure_id": closure_id}),
                       ("strassensperre_freigabelink", {"road_closure_id": closure_id})):
        assert "__fehler__" in _rufe(client, leser, tool, **args)
    assert "aktiv" in _rufe(client, leser, "strassensperren_kennzahlen")


def test_beenden(client) -> None:
    seed = _setup("mcp-a3-beenden")
    obj = _token(client, seed, "obj")
    closure_id = _sperre(seed["org_id"], "Beenden", POINT, valid_until=_now() + timedelta(days=5))
    result = _rufe(client, obj, "strassensperre_beenden", road_closure_id=closure_id, grund="Arbeiten fertig")
    assert "__fehler__" not in result, result
    assert "deaktivieren" in result["hinweis"]
    db = _db()
    try:
        closure = db.get(RoadClosure, closure_id)
        assert closure.valid_until is not None and closure.valid_until <= _now() + timedelta(minutes=1)
        assert db.query(RoadClosureChange).filter_by(road_closure_id=closure_id,
                                                     mcp_tool="strassensperre_beenden").count() >= 1
    finally:
        db.close()

    spaeter = _sperre(seed["org_id"], "Später", POINT, valid_until=_now() + timedelta(days=2))
    zu_spaet = _rufe(client, obj, "strassensperre_beenden", road_closure_id=spaeter, grund="x",
                     ende=(_now() + timedelta(days=10)).date().isoformat())
    assert "aktualisieren" in zu_spaet["__fehler__"]
    zu_frueh = _rufe(client, obj, "strassensperre_beenden", road_closure_id=spaeter, grund="x",
                     ende=(_now() - timedelta(days=10)).date().isoformat())
    assert "__fehler__" in zu_frueh
    assert "__fehler__" in _rufe(client, obj, "strassensperre_beenden", road_closure_id=spaeter, grund=" ")
    partner = _partner_sperre(seed["org_id"])
    assert "__fehler__" in _rufe(client, obj, "strassensperre_beenden", road_closure_id=partner, grund="x")


def test_teams_senden_nur_warteschlange(client, monkeypatch) -> None:
    async def verboten(*args, **kwargs):
        raise AssertionError("MCP darf nicht direkt senden")

    monkeypatch.setattr(road_closure_notify_service, "send_payload", verboten)
    seed = _setup("mcp-a3-teams")
    obj = _token(client, seed, "obj")
    closure_id = _sperre(seed["org_id"], "Teams", POINT, teams_melden=False)
    assert "nicht eingerichtet" in _rufe(client, obj, "strassensperre_teams_senden",
                                         road_closure_id=closure_id)["__fehler__"]
    db = _db()
    try:
        db.add(RoadClosureTeamsConfig(org_id=seed["org_id"], enabled=True, webhook_url_enc=encrypt_secret(HOOK)))
        db.commit()
    finally:
        db.close()
    result = _rufe(client, obj, "strassensperre_teams_senden", road_closure_id=closure_id)
    assert result["status"] == "pending" and result["ereignis"] == "manuell"
    assert "SECRET-MCP-HOOK" not in json.dumps(result)
    db = _db()
    try:
        rows = db.query(RoadClosureNotification).filter_by(road_closure_id=closure_id).all()
        assert [(row.ereignis, row.source) for row in rows] == [("manuell", "mcp")]
    finally:
        db.close()
    lesen = _rufe(client, obj, "strassensperre_lesen", road_closure_id=closure_id)
    assert lesen["teams_benachrichtigungen"][0]["ereignis"] == "manuell"
    assert lesen["teams_melden"] is False
    assert "SECRET-MCP-HOOK" not in json.dumps(lesen)


def test_freigabelink(client) -> None:
    seed = _setup("mcp-a3-link")
    obj = _token(client, seed, "obj")
    closure_id = _sperre(seed["org_id"], "Link", POINT)
    assert _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id)["link"] is None
    erzeugt = _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id, aktion="erzeugen")
    assert "/oeffentlich/strassensperre/rcd_" in erzeugt["link"]
    assert _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id)["link"] == erzeugt["link"]
    lesen = _rufe(client, obj, "strassensperre_lesen", road_closure_id=closure_id)
    assert lesen["freigabelink"] == {"aktiv": True, "gueltig_bis": None}
    assert "rcd_" not in json.dumps(lesen)
    path = erzeugt["link"].split("://", 1)[-1].split("/", 1)[1]
    assert client.get("/" + path).status_code == 200
    assert _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id,
                 aktion="widerrufen")["link"] is None
    assert client.get("/" + path).status_code == 404
    assert "__fehler__" in _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id,
                                 aktion="widerrufen")
    mit_ablauf = _rufe(client, obj, "strassensperre_freigabelink", road_closure_id=closure_id, aktion="erzeugen",
                       gueltig_bis=(_now() + timedelta(days=3)).date().isoformat())
    assert mit_ablauf["gueltig_bis"] is not None


def test_kennzahlen_entsprechen_service(client) -> None:
    seed = _setup("mcp-a3-kz")
    obj = _token(client, seed, "obj")
    _sperre(seed["org_id"], "KZ aktiv", POINT)
    _sperre(seed["org_id"], "KZ geplant", POINT, valid_from=_now() + timedelta(days=2),
            valid_until=_now() + timedelta(days=4))
    _partner_sperre(seed["org_id"])
    alle = _rufe(client, obj, "strassensperren_kennzahlen")
    oeffentlich = _rufe(client, obj, "strassensperren_kennzahlen", bereich="oeffentlich")
    db = _db()
    try:
        org = db.get(FireDept, seed["org_id"])
        erwartet = road_closure_stats_service.kennzahlen(db, org, scope="all")
    finally:
        db.close()
    assert alle["aktiv"] == erwartet["aktiv"] == 2 and alle["geplant"] == 1
    assert oeffentlich["aktiv"] == 1
    assert "aktiv" in alle["zusammenfassung"]


def test_liste_zeitraum_bereich_radius(client) -> None:
    seed = _setup("mcp-a3-liste")
    obj = _token(client, seed, "obj")
    nah = _sperre(seed["org_id"], "Nah", POINT)
    fern = _sperre(seed["org_id"], "Fern", {"type": "Point", "coordinates": [9.9, 47.6]})
    spaet = _sperre(seed["org_id"], "Spät", POINT, valid_from=_now() + timedelta(days=60),
                    valid_until=_now() + timedelta(days=61))
    partner = _partner_sperre(seed["org_id"])

    def ids(**args):
        return {item["id"] for item in _rufe(client, obj, "strassensperren_liste", **args)["items"]}

    assert spaet not in ids(zeitraum="30tage") and nah in ids(zeitraum="30tage")
    assert ids(bereich="nachbarn") == {partner}
    assert partner not in ids(bereich="eigene")
    radius = ids(lat=47.47, lng=9.745, radius_m=500)
    assert nah in radius and fern not in radius
    assert "__fehler__" in _rufe(client, obj, "strassensperren_liste", lat=47.47, lng=9.745)
    assert "__fehler__" in _rufe(client, obj, "strassensperren_liste", lat=47.47, lng=9.745, radius_m=10)


def test_anlegen_und_aktualisieren_mit_grund_und_teams(client) -> None:
    seed = _setup("mcp-a3-anlegen")
    obj = _token(client, seed, "obj")
    created = _rufe(client, obj, "strassensperre_anlegen", title=f"Grund {uuid4().hex}",
                    valid_from=_now().isoformat(timespec="minutes"), restriction_type="closed",
                    street=f"Grundweg {uuid4().hex[:6]}", reason="Fernwärme", teams_melden=False,
                    geometry_geojson=POINT)
    assert "__fehler__" not in created, created
    sperre = created.get("strassensperre", created)
    closure_id = sperre["id"]
    assert sperre["reason"] == "Fernwärme"
    db = _db()
    try:
        closure = db.get(RoadClosure, closure_id)
        assert closure.reason == "Fernwärme" and closure.teams_melden is False
        version = closure.version
    finally:
        db.close()
    updated = _rufe(client, obj, "strassensperre_aktualisieren", road_closure_id=closure_id,
                    felder={"reason": "Leitungsbau"}, version=version)
    assert "__fehler__" not in updated, updated
    db = _db()
    try:
        assert db.get(RoadClosure, closure_id).reason == "Leitungsbau"
    finally:
        db.close()
