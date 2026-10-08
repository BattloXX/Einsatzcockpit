"""MCP- und UI-Abläufe für automatisch ermittelte Sperrengeometrie und deren Freigabe."""

import json
from uuid import uuid4

from app.services.road_closure_section_resolver import SectionResult

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.road_closure import IncidentRoute, RoadClosure, RoadClosureChange
from app.models.user import AuditLog
from app.services import road_closure_section_service as section_service
from tests.test_mcp_objekte import _token
from tests.test_mcp_strassensperren import ROUTE, _bereit, _db, _rufe
from tests.test_strassensperren_ui import _closure, _login, _setup_user

LINE = {"type": "LineString", "coordinates": [[9.74, 47.47], [9.745, 47.471], [9.75, 47.472]]}


def _result(quality: str) -> SectionResult:
    return SectionResult(
        geometry=LINE,
        quality=quality,
        geometry_status="ok" if quality == "hoch" else "needs_review",
        osm_name="Rebberg",
        length_m=850.0,
        endpoints=[{"rolle": "von", "methode": "kreuzung", "text": "Kreuzung Kellaweg", "lat": 47.47, "lng": 9.74}],
        mehrdeutigkeiten=[],
        hinweise=[] if quality == "hoch" else ["Nur ein Abschnittsende gefunden – ganze Straße übernommen"],
    )


def _patch(monkeypatch, quality: str, calls: list | None = None) -> None:
    async def fake(db, org, street, from_text, to_text, city=None):
        if calls is not None:
            calls.append((street, from_text, to_text, city))
        return _result(quality)

    monkeypatch.setattr(section_service, "resolve_section", fake)


def test_mcp_anlegen_mit_hoher_qualitaet_wird_umfahren(client, monkeypatch):
    seed = _bereit("mcp-geo-hoch", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    calls: list = []
    _patch(monkeypatch, "hoch", calls)
    answer = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title=f"Rebberg {uuid4().hex}",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Rebberg",
        from_text="Kreuzung Kellaweg",
        to_text="Unterhub",
        city="Wolfurt",
    )
    assert calls == [("Rebberg", "Kreuzung Kellaweg", "Unterhub", "Wolfurt")]
    assert answer["geometrie"]["wird_umfahren"] is True
    assert answer["geometrie"]["osm_strassenname"] == "Rebberg"
    db = _db()
    try:
        closure = db.get(RoadClosure, answer["strassensperre"]["id"])
        assert closure.geometry_status == "ok" and closure.geometry_quality == "hoch"
        assert json.loads(closure.geometry_meta_json)["osm_name"] == "Rebberg"
    finally:
        db.close()


def test_mcp_mittlere_qualitaet_braucht_bestaetigung(client, monkeypatch):
    seed = _bereit("mcp-geo-mittel", {"obj": "objekt_verwalter", "leser": "readonly"})
    token = _token(client, seed, "obj")
    _patch(monkeypatch, "mittel")
    answer = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title=f"Unterhub {uuid4().hex}",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Unterhub",
    )
    closure_id = answer["strassensperre"]["id"]
    assert answer["geometrie"]["wird_umfahren"] is False
    assert any("strassensperre_geometrie_bestaetigen" in hint for hint in answer["hinweise"])
    liste = _rufe(client, token, "strassensperren_liste", status="all", geometrie="pruefen", limit=200)
    assert closure_id in {item["id"] for item in liste["items"]}

    db = _db()
    try:
        incident = Incident(primary_org_id=seed["org_id"], alarm_type_code="T1", status="active")
        db.add(incident)
        db.flush()
        route = IncidentRoute(
            org_id=seed["org_id"],
            incident_id=incident.id,
            route_geojson=json.dumps(LINE),
            dest_lat=47.471,
            dest_lng=9.745,
        )
        db.add(route)
        db.commit()
        route_id = route.id
        version = db.get(RoadClosure, closure_id).version
    finally:
        db.close()

    reader_token = _token(client, seed, "leser")
    denied = _rufe(client, reader_token, "strassensperre_geometrie_bestaetigen", road_closure_id=closure_id)
    assert "__fehler__" in denied
    confirmed = _rufe(client, token, "strassensperre_geometrie_bestaetigen", road_closure_id=closure_id)
    assert confirmed["strassensperre"]["geometry_status"] == "ok"
    db = _db()
    try:
        closure = db.get(RoadClosure, closure_id)
        assert closure.version == version + 1
        change = db.query(RoadClosureChange).filter_by(road_closure_id=closure_id, action="geometry_confirmed").one()
        assert change.source == "mcp" and change.mcp_tool == "strassensperre_geometrie_bestaetigen"
        assert db.query(AuditLog).filter_by(action="road_closure.geometry_confirmed", entity_id=closure_id).count() == 1
        assert db.get(IncidentRoute, route_id).stale is True
    finally:
        db.close()
    liste = _rufe(client, token, "strassensperren_liste", status="all", geometrie="pruefen", limit=200)
    assert closure_id not in {item["id"] for item in liste["items"]}


def test_mcp_ermitteln_ohne_und_mit_uebernahme(client, monkeypatch):
    seed = _bereit("mcp-geo-ermitteln", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    created = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title=f"Kellaweg {uuid4().hex}",
        valid_from="2026-07-01T10:00",
        restriction_type="partial",
        street="Kellaweg",
        geometry_geojson=ROUTE,
    )
    closure_id = created["strassensperre"]["id"]
    calls: list = []
    _patch(monkeypatch, "hoch", calls)
    preview = _rufe(client, token, "strassensperre_geometrie_ermitteln", road_closure_id=closure_id, von="Nr. 3")
    assert preview["qualitaet"] == "hoch" and preview["geometry_geojson"] == LINE
    assert calls[-1][0] == "Kellaweg" and calls[-1][1] == "Nr. 3"
    db = _db()
    try:
        closure = db.get(RoadClosure, closure_id)
        assert json.loads(closure.geometry_geojson) == ROUTE and closure.geometry_quality == "manuell"
    finally:
        db.close()
    _rufe(client, token, "strassensperre_geometrie_ermitteln", road_closure_id=closure_id, uebernehmen=True)
    db = _db()
    try:
        closure = db.get(RoadClosure, closure_id)
        assert json.loads(closure.geometry_geojson)["coordinates"] == LINE["coordinates"]
        assert closure.geometry_quality == "hoch" and closure.geometry_status == "ok"
    finally:
        db.close()
    ohne_id = _rufe(client, token, "strassensperre_geometrie_ermitteln", street="Kellaweg", uebernehmen=True)
    assert "__fehler__" in ohne_id


def test_mcp_bestaetigen_ohne_geometrie_scheitert(client, monkeypatch):
    seed = _bereit("mcp-geo-leer", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")

    async def none(db, org, street, from_text, to_text, city=None):
        return SectionResult(hinweise=["Straße nicht in OSM gefunden"])

    monkeypatch.setattr(section_service, "resolve_section", none)
    created = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title=f"Leer {uuid4().hex}",
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Gibtsnichtweg",
    )
    assert created["strassensperre"]["geometry_status"] == "missing"
    closure_id = created["strassensperre"]["id"]
    answer = _rufe(client, token, "strassensperre_geometrie_bestaetigen", road_closure_id=closure_id)
    assert "__fehler__" in answer and "Keine Geometrie" in answer["__fehler__"]


def test_ui_bestaetigen_rechte_und_isolation(client):
    own = _closure(
        org_id=1,
        title=f"UI Geo {uuid4().hex}",
        geometry_geojson=json.dumps(LINE),
        geometry_status="needs_review",
        geometry_quality="mittel",
    )
    reader = _setup_user("readonly")
    _login(client, reader)
    assert "Geometrie bestätigen" not in client.get(f"/strassensperren/{own.id}").text
    denied = client.post(
        f"/strassensperren/{own.id}/geometrie-bestaetigen", data={"_csrf": client.cookies.get("ec_csrf")}
    )
    assert denied.status_code == 403

    manager = _setup_user("objekt_verwalter")
    _login(client, manager)
    page = client.get(f"/strassensperren/{own.id}").text
    assert "Geometrie bestätigen" in page and "Mittel" in page
    listing = client.get("/strassensperren/liste?status=all&geometrie=pruefen").text
    assert own.title in listing
    response = client.post(
        f"/strassensperren/{own.id}/geometrie-bestaetigen",
        data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(RoadClosure, own.id).geometry_status == "ok"
    finally:
        db.close()
    assert "Geometrie bestätigt" in client.get(f"/strassensperren/{own.id}").text

    foreign = _closure(
        org_id=2, title=f"Fremd {uuid4().hex}", geometry_geojson=json.dumps(LINE), geometry_status="needs_review"
    )
    response = client.post(
        f"/strassensperren/{foreign.id}/geometrie-bestaetigen",
        data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 404
