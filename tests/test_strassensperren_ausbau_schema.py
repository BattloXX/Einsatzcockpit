"""Zusatzabdeckung für die Ausbau-Enums und Modelle."""

import pytest

from app.core.tenant import _TENANT_TABLE_NAMES
from app.mcp.server import DirectionLiteral, PriorityLiteral, RestrictionTypeLiteral
from app.models.mcp import MCPUpload
from app.models.road_closure import DIRECTIONS, PRIORITIES, RESTRICTION_TYPES
from app.services.road_closure_service import normalize_direction, normalize_priority, normalize_restriction_type


def test_normalizer_akzeptieren_aliase_und_zeigen_katalog_im_fehler() -> None:
    assert normalize_restriction_type("Vollsperre") == "closed"
    assert normalize_restriction_type("höhe") == "height_limit"
    assert normalize_priority("kritisch") == "critical"
    assert normalize_direction("rückwärts") == "backward"
    assert normalize_direction("") is None
    with pytest.raises(ValueError, match="closed \\(Vollsperre\\)"):
        normalize_restriction_type("unbekannt")


def test_adapter_literals_entsprechen_den_katalogen() -> None:
    assert set(RestrictionTypeLiteral.__args__) == set(RESTRICTION_TYPES) | {""}
    assert set(PriorityLiteral.__args__) == set(PRIORITIES)
    assert set(DirectionLiteral.__args__) == set(DIRECTIONS) | {""}


def test_road_closure_document_ist_tenant_tabelle_und_upload_ist_optional() -> None:
    assert "road_closure_document" in _TENANT_TABLE_NAMES
    assert MCPUpload.__table__.c.objekt_id.nullable
    assert MCPUpload.__table__.c.zweck.default is not None


def test_mcp_schema_enum_kataloge_und_neue_felder(client) -> None:
    import json as _json
    from uuid import uuid4

    from app.models.road_closure import RoadClosure
    from tests.test_mcp_objekte import _mcp, _token
    from tests.test_mcp_strassensperren import ROUTE, _bereit, _db, _rpc, _rufe

    seed = _bereit("mcp-sperre-ausbau", {"obj": "objekt_verwalter", "leser": "readonly"})
    token = _token(client, seed, "obj")
    tools = {t["name"]: t for t in _rpc(_mcp(client, token, "tools/list", {}, 95))["result"]["tools"]}
    schema = tools["strassensperre_anlegen"]["inputSchema"]
    restriction = _json.dumps(schema["properties"]["restriction_type"])
    assert '"enum"' in restriction and '"closed"' in restriction and '"residents_only"' in restriction
    assert '"enum"' in _json.dumps(schema["properties"]["priority"])

    kataloge = _rufe(client, _token(client, seed, "leser"), "strassensperren_kataloge")
    closed = next(item for item in kataloge["restriction_types"] if item["wert"] == "closed")
    assert closed["label"] == "Vollsperre" and "vollsperre" in closed["aliase"]
    assert {item["wert"] for item in kataloge["geometry_quality"]} == {"hoch", "mittel", "niedrig", "manuell"}

    title = f"Ausbau {uuid4().hex}"
    created = _rufe(
        client,
        token,
        "strassensperre_anlegen",
        title=title,
        valid_from="2026-07-01T10:00",
        restriction_type="closed",
        street="Rebberg",
        city="Wolfurt",
        reference_number="BHB-123/2026",
        authority="Bezirkshauptmannschaft Bregenz",
        exceptions="Anrainer frei",
        geometry_geojson=ROUTE,
    )
    sperre = created["strassensperre"]
    assert sperre["city"] == "Wolfurt" and sperre["reference_number"] == "BHB-123/2026"
    assert sperre["authority"].startswith("Bezirks") and sperre["exceptions"] == "Anrainer frei"
    updated = _rufe(
        client,
        token,
        "strassensperre_aktualisieren",
        road_closure_id=sperre["id"],
        felder={"exceptions": "Linienbus frei"},
    )
    assert updated["strassensperre"]["exceptions"] == "Linienbus frei"
    blocked = _rufe(
        client, token, "strassensperre_aktualisieren", road_closure_id=sperre["id"], felder={"geometry_quality": "hoch"}
    )
    assert "__fehler__" in blocked
    db = _db()
    try:
        assert db.get(RoadClosure, sperre["id"]).geometry_quality is None
    finally:
        db.close()


def test_ui_speichert_neue_felder_und_aliase_im_service(client) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.road_closure import RoadClosure
    from tests.test_strassensperren_ui import _login, _payload, _setup_user

    user = _setup_user("objekt_verwalter")
    _login(client, user)
    response = client.post(
        "/strassensperren/neu",
        data=_payload(
            client,
            city="Wolfurt",
            reference_number="AZ 7",
            authority="Gemeinde Wolfurt",
            exceptions="Einsatzfahrzeuge frei",
        ),
        follow_redirects=False,
    )
    assert response.status_code == 303
    closure_id = int(response.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        closure = db.get(RoadClosure, closure_id)
        assert (closure.city, closure.reference_number, closure.authority) == ("Wolfurt", "AZ 7", "Gemeinde Wolfurt")
        assert closure.exceptions == "Einsatzfahrzeuge frei"
    finally:
        db.close()
    detail = client.get(f"/strassensperren/{closure_id}").text
    assert "AZ 7" in detail and "Gemeinde Wolfurt" in detail and "Einsatzfahrzeuge frei" in detail
