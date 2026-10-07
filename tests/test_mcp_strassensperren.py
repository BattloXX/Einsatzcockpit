"""HTTP-Regressionen für die MCP-Lesewerkzeuge für Straßensperren."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.invitation import OrgPartner
from app.models.road_closure import IncidentRoadClosure, IncidentRoute, RoadClosure, RoadClosureChange, RoadClosureShare
from app.models.user import AuditLog
from app.services import road_closure_service
from app.services.einsatz_routing import RouteResponse, RouteResult, RoutingError
from tests.test_mcp_objekte import _mcp, _seed, _token

TOOLS_STRASSENSPERREN = {
    "strassensperren_liste",
    "strassensperre_lesen",
    "strassensperren_suchen",
    "strassensperren_im_gebiet",
    "einsatz_strassensperren",
    "einsatz_anfahrtsroute_pruefen",
    "strassensperren_entlang_route",
}
ROUTE = {"type": "LineString", "coordinates": [[9.73, 47.47], [9.76, 47.47]]}
DETOUR = {"type": "LineString", "coordinates": [[9.73, 47.47], [9.745, 47.475], [9.76, 47.47]]}


def _rpc(response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        lines = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(lines[-1])
    return response.json()


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 94)
    assert response.status_code == 200, response.text
    result = _rpc(response)["result"]
    if result.get("isError"):
        return {"__fehler__": json.dumps(result["content"], ensure_ascii=False)}
    return result.get("structuredContent", {}).get("result", json.loads(result["content"][0]["text"]))


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _bereit(slug: str, users: dict[str, str] | None = None) -> dict:
    seed = _seed(slug, users or {"admin": "org_admin"})
    db = _db()
    try:
        org = db.get(FireDept, seed["org_id"])
        org.timezone = "Europe/Vienna"
        org_settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        org_settings.mcp_modul_aktiv = True
        org_settings.strassensperren_modul_aktiv = True
        for key in ("mcp_module_enabled", "strassensperren_module_enabled"):
            row = db.query(SystemSettings).filter_by(key=key).first()
            if row is None:
                db.add(SystemSettings(key=key, value="true"))
            else:
                row.value = "true"
        db.commit()
    finally:
        db.close()
    return seed


def _sperre(org_id: int, title: str, geometry: dict, **werte) -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    data = {
        "title": f"{title} {uuid4().hex}",
        "street": "Teststraße",
        "valid_from": now - timedelta(hours=1),
        "valid_until": now + timedelta(days=1),
        "restriction_type": "closed",
        "priority": "normal",
        "geometry_geojson": geometry,
        "geometry_status": "ok",
    } | werte
    db = _db()
    try:
        closure = road_closure_service.create_closure(db, org_id, None, data)
        db.commit()
        return closure.id
    finally:
        db.close()


class _Provider:
    name = "test"
    supports_avoid_polygons = True

    def __init__(self):
        self.calls = 0

    async def calculate_route(self, start, dest, *, avoid_polygons=None, alternatives=False):
        self.calls += 1
        geometry = DETOUR if avoid_polygons else ROUTE
        return RouteResponse(
            RouteResult(geometry, 3600 if avoid_polygons else 3000, 420 if avoid_polygons else 300, ["Umfahrung"]),
            [],
            "test",
            True,
        )


def test_tools_liste_und_org_modul_aus(client):
    seed = _bereit("mcp-sperre-tools")
    token = _token(client, seed, "admin")
    assert TOOLS_STRASSENSPERREN <= set(TOOLS)
    assert all(name in _mcp(client, token, "tools/list", {}, 94).text for name in TOOLS_STRASSENSPERREN)
    db = _db()
    try:
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().strassensperren_modul_aktiv = False
        db.commit()
    finally:
        db.close()
    assert not any(name in _mcp(client, token, "tools/list", {}, 94).text for name in TOOLS_STRASSENSPERREN)
    assert "__fehler__" in _rufe(client, token, "strassensperren_liste")


def test_liste_status_zeitstempel_und_zusammenfassung(client):
    seed = _bereit("mcp-sperre-liste")
    token = _token(client, seed, "admin")
    now = datetime.now(UTC).replace(tzinfo=None)
    active = _sperre(seed["org_id"], "Aktiv", {"type": "Point", "coordinates": [9.73, 47.47]})
    planned = _sperre(
        seed["org_id"],
        "Geplant",
        {"type": "Point", "coordinates": [9.731, 47.47]},
        valid_from=now + timedelta(days=1),
        valid_until=now + timedelta(days=2),
    )
    expired = _sperre(
        seed["org_id"],
        "Abgelaufen",
        {"type": "Point", "coordinates": [9.732, 47.47]},
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(days=1),
    )
    summer = _sperre(
        seed["org_id"],
        "Sommer",
        {"type": "Point", "coordinates": [9.733, 47.47]},
        valid_from=datetime(2026, 7, 15, 10),
        valid_until=datetime(2026, 7, 16, 10),
    )
    current = _rufe(client, token, "strassensperren_liste", status="current")
    assert {row["id"] for row in current["items"]} == {active, planned}
    assert [row["id"] for row in _rufe(client, token, "strassensperren_liste", status="active")["items"]] == [active]
    all_rows = _rufe(client, token, "strassensperren_liste", status="all")["items"]
    assert {row["id"] for row in all_rows} == {active, planned, expired, summer}
    assert next(row for row in all_rows if row["id"] == summer)["valid_from"].endswith("+02:00")
    assert current["zusammenfassung"]


def test_lesen_und_isolation_mit_freigabe(client):
    seed, fremd = _bereit("mcp-sperre-eigen"), _bereit("mcp-sperre-fremd")
    token = _token(client, seed, "admin")
    own = _sperre(seed["org_id"], "Eigen", {"type": "Point", "coordinates": [9.73, 47.47]}, description="Details")
    foreign = _sperre(fremd["org_id"], "Fremd", {"type": "Point", "coordinates": [9.74, 47.47]})
    read = _rufe(client, token, "strassensperre_lesen", road_closure_id=own)
    assert {"description", "geometry", "version", "ui_link"} <= set(read) and read["description"] == "Details"
    assert "__fehler__" in _rufe(client, token, "strassensperre_lesen", road_closure_id=foreign)
    assert foreign not in {row["id"] for row in _rufe(client, token, "strassensperren_liste", status="all")["items"]}
    db = _db()
    try:
        db.add(RoadClosureShare(road_closure_id=foreign, org_id=seed["org_id"]))
        db.commit()
    finally:
        db.close()
    shared = _rufe(client, token, "strassensperre_lesen", road_closure_id=foreign)
    assert shared["eigene"] is False and shared["besitzer_org"] == "Org mcp-sperre-fremd"
    assert "__fehler__" in _rufe(client, token, "strassensperre_lesen", road_closure_id=999999999)


def test_suchen_und_gebiet(client):
    seed = _bereit("mcp-sperre-suche")
    token = _token(client, seed, "admin")
    bregenz = _sperre(
        seed["org_id"],
        "Bregenzer Straße",
        {"type": "Point", "coordinates": [9.73, 47.47]},
        description="Wasserleitungsarbeiten",
    )
    _sperre(seed["org_id"], "Achstraße", {"type": "Point", "coordinates": [9.733, 47.47]}, description="Wasser")
    near = _sperre(seed["org_id"], "Nah", {"type": "Point", "coordinates": [9.73, 47.4727]})
    far = _sperre(seed["org_id"], "Fern", {"type": "Point", "coordinates": [9.73, 47.4835]})
    assert (
        _rufe(client, token, "strassensperren_suchen", suchtext="Bregenzer Str Wasserleitung")["items"][0]["id"]
        == bregenz
    )
    area = _rufe(client, token, "strassensperren_im_gebiet", lat=47.47, lng=9.73, radius_m=1000)
    assert near in [row["id"] for row in area["items"]] and far not in [row["id"] for row in area["items"]]
    all_area = _rufe(client, token, "strassensperren_im_gebiet", lat=47.47, lng=9.73, radius_m=2000)
    ids = [row["id"] for row in all_area["items"]]
    assert ids.index(near) < ids.index(far)
    assert "__fehler__" in _rufe(client, token, "strassensperren_im_gebiet", lat=47.47, lng=9.73, radius_m=10)


def test_einsatz_strassensperren_und_isolation(client):
    seed, fremd = _bereit("mcp-sperre-einsatz"), _bereit("mcp-sperre-einsatz-fremd")
    token = _token(client, seed, "admin")
    closure = _sperre(seed["org_id"], "Route", {"type": "Point", "coordinates": [9.74, 47.47]})
    db = _db()
    try:
        incident = Incident(primary_org_id=seed["org_id"], status="active")
        foreign = Incident(primary_org_id=fremd["org_id"], status="active")
        db.add_all([incident, foreign])
        db.flush()
        route = IncidentRoute(org_id=seed["org_id"], incident_id=incident.id, status="affected")
        db.add(route)
        db.flush()
        db.add(
            IncidentRoadClosure(
                org_id=seed["org_id"],
                incident_route_id=route.id,
                incident_id=incident.id,
                road_closure_id=closure,
                relevance="route",
                title_snapshot="Route",
                restriction_type_snapshot="closed",
                geometry_status_snapshot="ok",
            )
        )
        db.commit()
        incident_id, foreign_id = incident.id, foreign.id
    finally:
        db.close()
    assert _rufe(client, token, "einsatz_strassensperren", incident_id=incident_id)["closures"]
    assert "__fehler__" in _rufe(client, token, "einsatz_strassensperren", incident_id=foreign_id)


def test_live_route_und_parameterfehler(client, monkeypatch):
    seed = _bereit("mcp-sperre-live")
    token = _token(client, seed, "admin")
    _sperre(seed["org_id"], "Route", {"type": "LineString", "coordinates": [[9.744, 47.47], [9.746, 47.47]]})
    provider = _Provider()
    monkeypatch.setattr("app.services.einsatz_routing.get_provider", lambda: provider)
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", True)
    db = _db()
    try:
        row = db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one()
        row.routing_start_lat, row.routing_start_lng = 47.47, 9.73
        db.commit()
    finally:
        db.close()
    live = _rufe(client, token, "einsatz_anfahrtsroute_pruefen", lat=47.47, lng=9.76, vehicle_id=1)
    assert (
        live["routing_status"] == "affected"
        and "Umfahrung" in live["zusammenfassung"]
        and "Fahrzeugprofile" in live["hinweis"]
    )
    assert _rufe(
        client, token, "strassensperren_entlang_route", start_lat=47.47, start_lng=9.73, ziel_lat=47.47, ziel_lng=9.76
    )["closures"]
    assert "__fehler__" in _rufe(client, token, "einsatz_anfahrtsroute_pruefen")
    assert "__fehler__" in _rufe(client, token, "einsatz_anfahrtsroute_pruefen", incident_id=1, lat=47.47, lng=9.76)
    db = _db()
    try:
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().routing_start_lat = None
        db.commit()
    finally:
        db.close()
    assert (
        "Kein Routing-Startpunkt"
        in _rufe(client, token, "einsatz_anfahrtsroute_pruefen", lat=47.47, lng=9.76)["__fehler__"]
    )
    monkeypatch.setattr(settings, "EINSATZ_ROUTING_ENABLED", False)
    monkeypatch.setattr(
        "app.services.einsatz_routing.get_provider",
        lambda: (_ for _ in ()).throw(RoutingError("disabled")),
    )
    assert "__fehler__" in _rufe(
        client, token, "strassensperren_entlang_route", start_lat=47.47, start_lng=9.73, ziel_lat=47.47, ziel_lng=9.76
    )


def test_incident_route_wird_ohne_provider_gelesen(client, monkeypatch):
    seed = _bereit("mcp-sperre-gespeichert")
    token = _token(client, seed, "admin")
    provider = _Provider()
    monkeypatch.setattr("app.services.einsatz_routing.get_provider", lambda: provider)
    db = _db()
    try:
        incident = Incident(primary_org_id=seed["org_id"], status="active")
        db.add(incident)
        db.flush()
        db.add(IncidentRoute(org_id=seed["org_id"], incident_id=incident.id, status="ok"))
        db.commit()
        incident_id = incident.id
    finally:
        db.close()
    assert _rufe(client, token, "einsatz_anfahrtsroute_pruefen", incident_id=incident_id)["routing_status"] == "ok"
    assert provider.calls == 0


def test_mcp_sperre_anlegen_zeiten_audit_und_validierung(client):
    seed = _bereit("mcp-sperre-schreiben", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    first = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title="MCP Sommer",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Hauptstraße",
        geometry_geojson=ROUTE,
    )
    assert first["status"] == "created" and first["strassensperre"]["geometry_status"] == "ok"
    second = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title="MCP Winter",
        valid_from="2026-12-01T10:00:00+01:00",
        restriction_type="partial",
        street="Nebenstraße",
        geometry_geojson=ROUTE,
    )
    db = _db()
    try:
        summer = db.get(RoadClosure, first["strassensperre"]["id"])
        winter = db.get(RoadClosure, second["strassensperre"]["id"])
        assert summer.valid_from == datetime(2026, 7, 1, 8) and winter.valid_from == datetime(2026, 12, 1, 9)
        change = db.query(RoadClosureChange).filter_by(road_closure_id=summer.id, action="created").one()
        assert change.source == "mcp" and change.mcp_tool == "strassensperre_anlegen"
        assert db.query(AuditLog).filter_by(action="road_closure.created", entity_id=summer.id).count() == 1
        before = db.query(RoadClosure).filter_by(org_id=seed["org_id"]).count()
    finally:
        db.close()
    for arguments in (
        {"valid_until": "2026-06-01T10:00"},
        {"restriction_type": "unbekannt"},
        {"max_height_m": 12},
        {"geometry_geojson": "{"},
        {"street": "", "geometry_geojson": None},
    ):
        data = {"title": "Ungültig", "valid_from": "2026-07-01T10:00", "restriction_type": "closed", "street": "X"}
        data.update(arguments)
        assert "__fehler__" in _rufe(client, token, "strassensperre_anlegen", **data)
    db = _db()
    try:
        assert db.query(RoadClosure).filter_by(org_id=seed["org_id"]).count() == before
    finally:
        db.close()


def test_mcp_sperre_abschnitt_dublette_freigabe_und_update(client, monkeypatch):
    seed = _bereit("mcp-sperre-write-a", {"obj": "objekt_verwalter"})
    partner = _bereit("mcp-sperre-write-b")
    token = _token(client, seed, "obj")
    async def section(*args):
        return {"geometry": ROUTE, "hinweis": "Bitte prüfen."}

    monkeypatch.setattr("app.mcp.tools.strassensperren.road_closure_section_service.section_from_address", section)
    created = _rufe(
        client, token, "strassensperre_anlegen", title="Abschnitt", valid_from="2026-07-01T10:00",
        restriction_type="closed", street="Abschnittstraße", from_text="1", to_text="5"
    )
    closure_id = created["strassensperre"]["id"]
    assert created["strassensperre"]["geometry_status"] == "needs_review" and created["hinweise"]
    duplicate = _rufe(
        client, token, "strassensperre_anlegen", title="Abschnitt", valid_from="2026-07-01T10:00",
        restriction_type="closed", street="Abschnittstraße", from_text="1", to_text="5"
    )
    assert duplicate["status"] == "possible_duplicate"
    confirmed = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title="Abschnitt bestätigt",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Abschnittstraße",
        from_text="1",
        to_text="5",
        duplikat_bestaetigt=True,
    )
    assert confirmed["status"] == "created"
    failed_section_title = "Ohne Geometrie"
    async def no_section(*args):
        raise ValueError("Abschnitt nicht gefunden.")

    monkeypatch.setattr("app.mcp.tools.strassensperren.road_closure_section_service.section_from_address", no_section)
    missing = _rufe(
        client, token, "strassensperre_anlegen", title=failed_section_title, valid_from="2026-08-01T10:00",
        restriction_type="closed", street="Fehlstraße", from_text="1"
    )
    assert missing["strassensperre"]["geometry_status"] == "missing" and missing["hinweise"]
    rejected_share = _rufe(
        client, token, "strassensperre_anlegen", title="Nichtpartner", valid_from="2026-09-01T10:00",
        restriction_type="closed", street="Freigabestraße", visible_for_org_ids=[partner["org_id"]]
    )
    assert "__fehler__" in rejected_share
    db = _db()
    try:
        assert db.query(RoadClosure).filter_by(title="Nichtpartner").count() == 0
        db.add(OrgPartner(org_id=seed["org_id"], partner_org_id=partner["org_id"]))
        db.commit()
    finally:
        db.close()
    updated = _rufe(
        client, token, "strassensperre_aktualisieren", road_closure_id=closure_id,
        felder={"valid_until": "2026-07-02T10:00", "visible_for_org_ids": [partner["org_id"]]}, version=1
    )
    assert updated["geaenderte_felder"] and updated["strassensperre"]["version"] == 3
    db = _db()
    try:
        assert db.get(RoadClosureShare, (closure_id, partner["org_id"])) is not None
    finally:
        db.close()
    assert "__fehler__" in _rufe(
        client, token, "strassensperre_aktualisieren", road_closure_id=closure_id, felder={"nein": 1}
    )
    assert "__fehler__" in _rufe(
        client, token, "strassensperre_aktualisieren", road_closure_id=closure_id, felder={}, version=1
    )
    foreign_id = _sperre(partner["org_id"], "Fremde", ROUTE)
    db = _db()
    try:
        db.add(RoadClosureShare(road_closure_id=foreign_id, org_id=seed["org_id"]))
        db.commit()
    finally:
        db.close()
    assert "nicht änderbar" in _rufe(
        client, token, "strassensperre_aktualisieren", road_closure_id=foreign_id, felder={}
    )["__fehler__"]


def test_mcp_sperre_deaktivieren_und_rechte(client):
    seed = _bereit("mcp-sperre-deactivate", {"obj": "objekt_verwalter", "read": "readonly"})
    token = _token(client, seed, "obj")
    closure_id = _sperre(seed["org_id"], "MCP deaktivieren", ROUTE)
    assert "__fehler__" in _rufe(client, token, "strassensperre_deaktivieren", road_closure_id=closure_id, grund="")
    disabled = _rufe(client, token, "strassensperre_deaktivieren", road_closure_id=closure_id, grund="Freigabe")
    assert disabled["strassensperre"]["status"] == "cancelled"
    assert "__fehler__" in _rufe(client, token, "strassensperre_deaktivieren", road_closure_id=closure_id, grund="Nochmal")
    assert _rufe(client, token, "strassensperre_reaktivieren", road_closure_id=closure_id)["strassensperre"]["status"] != "cancelled"
    assert "strassensperre_loeschen" not in TOOLS
    readonly = _token(client, seed, "read")
    listed = _mcp(client, readonly, "tools/list", {}, 94).text
    assert "strassensperre_anlegen" not in listed and "strassensperren_liste" in listed
    assert "__fehler__" in _rufe(
        client,
        readonly,
        "strassensperre_anlegen",
        title="Unzulässig",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Hauptstraße",
    )
