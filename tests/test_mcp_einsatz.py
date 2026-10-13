"""Regression tests for privacy-safe MCP incident reads."""

import json

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.incident import (
    Incident,
    IncidentColumn,
    IncidentOrg,
    IncidentVehicle,
    Message,
    MessageMedia,
    RescuedPerson,
    Task,
)
from app.models.master import VehicleMaster
from tests.test_mcp_objekte import _mcp, _seed, _token

EINSATZ_TOOLS = {"einsaetze_liste", "einsatz_lesen", "einsatz_einheiten", "einsatz_auftraege", "einsatz_meldungen"}


def _rpc(response):
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")][-1])
    return response.json()


def _rufe(client, token, tool, **arguments):
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 95)
    assert response.status_code == 200, response.text
    result = _rpc(response)["result"]
    if result.get("isError"):
        return {"__fehler__": json.dumps(result["content"], ensure_ascii=False)}
    return result.get("structuredContent", {}).get("result", json.loads(result["content"][0]["text"]))


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _einsatz(org_id, *, status="active", secret=False):
    db = _db()
    try:
        incident = Incident(
            primary_org_id=org_id,
            status=status,
            reason="Brand Test",
            caller_name="Anrufer Geheim",
            caller_phone="0664 Geheim",
        )
        db.add(incident)
        db.flush()
        active = IncidentColumn(incident_id=incident.id, code="active", title="Einheiten", column_kind="vehicles")
        rescued = IncidentColumn(
            incident_id=incident.id, code="rescued", title="Geheim Personen", column_kind="rescued"
        )
        db.add_all([active, rescued])
        db.flush()
        master = VehicleMaster(dept_id=org_id, code="FL", name="FL Test", type="LF")
        db.add(master)
        db.flush()
        vehicle = IncidentVehicle(
            incident_id=incident.id,
            column_id=active.id,
            vehicle_master_id=master.id,
            commander_name="Kommandant Geheim",
            fahrer_name="Fahrer Geheim",
        )
        db.add(vehicle)
        db.flush()
        task = Task(incident_id=incident.id, column_id=active.id, vehicle_id=vehicle.id, title="Offener Auftrag")
        hidden = Task(incident_id=incident.id, column_id=rescued.id, title="Person Geheim")
        message = Message(incident_id=incident.id, column_id=active.id, vehicle_id=vehicle.id, title="Meldung")
        db.add_all(
            [
                task,
                hidden,
                message,
                RescuedPerson(incident_id=incident.id, column_id=rescued.id, name="Gerettete Geheim"),
            ]
        )
        db.flush()
        db.add(
            MessageMedia(
                message_id=message.id,
                incident_id=incident.id,
                kind="image",
                original_filename="bild.jpg",
                storage_path="x",
                mime_type="image/jpeg",
                bytes=12,
            )
        )
        db.commit()
        return incident.id, vehicle.id
    finally:
        db.close()


def test_tools_liste_und_sichtbarkeit_status(client):
    own = _seed("mcp-einsatz-own", {"read": "readonly"}, objekt=False, kontakte=False)
    other = _seed("mcp-einsatz-other", {"read": "readonly"}, objekt=False, kontakte=False)
    token = _token(client, own, "read")
    own_active, _ = _einsatz(own["org_id"])
    own_closed, _ = _einsatz(own["org_id"], status="closed")
    collab, _ = _einsatz(other["org_id"])
    foreign, _ = _einsatz(other["org_id"])
    db = _db()
    try:
        db.add(IncidentOrg(incident_id=collab, org_id=own["org_id"], role="collaborator"))
        db.commit()
    finally:
        db.close()
    assert EINSATZ_TOOLS <= set(TOOLS)
    assert all(name in _mcp(client, token, "tools/list", {}, 95).text for name in EINSATZ_TOOLS)
    assert {row["id"] for row in _rufe(client, token, "einsaetze_liste", status="aktiv")["items"]} == {
        own_active,
        collab,
    }
    assert {row["id"] for row in _rufe(client, token, "einsaetze_liste", status="abgeschlossen")["items"]} == {
        own_closed
    }
    assert "Einsatz nicht gefunden." in _rufe(client, token, "einsatz_lesen", incident_id=foreign)["__fehler__"]


def test_privacy_filter_and_media(client):
    seed = _seed("mcp-einsatz-privacy", {"read": "readonly"}, objekt=False, kontakte=False)
    token = _token(client, seed, "read")
    incident_id, vehicle_id = _einsatz(seed["org_id"], secret=True)
    result = [
        _rufe(client, token, "einsatz_lesen", incident_id=incident_id),
        _rufe(client, token, "einsatz_einheiten", incident_id=incident_id),
        _rufe(client, token, "einsatz_auftraege", incident_id=incident_id),
        _rufe(client, token, "einsatz_meldungen", incident_id=incident_id),
    ]
    text = json.dumps(result, ensure_ascii=False)
    for secret in (
        "Anrufer Geheim",
        "0664 Geheim",
        "Kommandant Geheim",
        "Fahrer Geheim",
        "Gerettete Geheim",
        "Person Geheim",
    ):
        assert secret not in text
    tasks = _rufe(client, token, "einsatz_auftraege", incident_id=incident_id, status="offen", einheit_id=vehicle_id)
    assert [item["titel"] for item in tasks["items"]] == ["Offener Auftrag"]
    media = _rufe(client, token, "einsatz_meldungen", incident_id=incident_id)["items"][0]["medien"]
    assert media == [media[0]] and media[0]["dateiname"] == "bild.jpg" and "storage_path" not in media[0]
