"""Regression tests for privacy-safe MCP incident reads."""

import json
from datetime import datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.incident import (
    Incident,
    IncidentColumn,
    IncidentCommLog,
    IncidentLog,
    IncidentOrg,
    IncidentVehicle,
    Message,
    MessageMedia,
    RescuedPerson,
    Task,
)
from app.models.kontakt import Kontakt
from app.models.master import OrgSettings, VehicleMaster
from app.models.objekt import (
    GefahrenKatalog,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektEinsatz,
    ObjektGefahr,
    ObjektKontakt,
)
from app.models.user import AuditLog, User
from app.services.mcp_download_service import erstelle_download_token
from tests.test_mcp_objekte import _mcp, _seed, _token

EINSATZ_TOOLS = {
    "einsaetze_liste",
    "einsatz_lesen",
    "einsatz_einheiten",
    "einsatz_auftraege",
    "einsatz_meldungen",
    "einsatz_verlauf",
    "einsatz_objekte",
    "einsatz_lagebild",
    "einsatz_objekt_dokument_herunterladen",
    "einsatz_objekt_dokument_seite_ansehen",
    "einsatz_objekt_dokument_text",
    "einsatz_medium_herunterladen",
}
EINSATZ_SCHREIB_TOOLS = {"einsatz_auftrag_anlegen", "einsatz_meldung_anlegen"}


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


def _schreibspalten(incident_id):
    db = _db()
    try:
        tasks = IncidentColumn(incident_id=incident_id, code="tasks", title="Aufträge", column_kind="tasks")
        messages = IncidentColumn(incident_id=incident_id, code="messages", title="Meldungen", column_kind="messages")
        db.add_all([tasks, messages])
        db.commit()
        return tasks.id, messages.id
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
    tool_liste = _mcp(client, token, "tools/list", {}, 95).text
    # Ohne Objektmodul bleibt nur der Objektdokument-Download ausgeblendet.
    assert all(name in tool_liste for name in EINSATZ_TOOLS - {
        "einsatz_objekt_dokument_herunterladen", "einsatz_objekt_dokument_seite_ansehen",
        "einsatz_objekt_dokument_text",
    })
    assert '"name":"einsatz_objekt_dokument_herunterladen"' not in tool_liste
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


def test_verlauf_objekte_und_lagebild_ohne_personendaten(client):
    seed = _seed("mcp-einsatz-p2", {"read": "readonly"}, objekt=True, kontakte=True)
    token = _token(client, seed, "read")
    incident_id, _ = _einsatz(seed["org_id"])
    db = _db()
    try:
        zeit = datetime(2026, 1, 2, 3, 4, 5)
        db.add_all(
            [
                IncidentLog(
                    incident_id=incident_id,
                    ts=zeit,
                    text="Tagebuch sichtbar",
                    author_name="Autor Geheim",
                ),
                IncidentLog(incident_id=incident_id, ts=zeit, text="Person Geheim", entity_type="person"),
                IncidentCommLog(
                    incident_id=incident_id,
                    ts=zeit.replace(second=6),
                    direction="in",
                    message="Funk sichtbar",
                    author_name="Autor Geheim",
                ),
            ]
        )
        gefahr = GefahrenKatalog(org_id=seed["org_id"], name="Gefahr sichtbar")
        kontakt = Kontakt(org_id=seed["org_id"], typ="person", anzeigename="Kontakt Geheim")
        db.add_all([gefahr, kontakt])
        db.flush()
        db.add_all(
            [
                ObjektEinsatz(
                    org_id=seed["org_id"],
                    incident_id=incident_id,
                    objekt_id=seed["objekt_id"],
                    quelle="manuell",
                    status="bestaetigt",
                ),
                ObjektGefahr(org_id=seed["org_id"], objekt_id=seed["objekt_id"], gefahr_id=gefahr.id),
                ObjektKontakt(org_id=seed["org_id"], objekt_id=seed["objekt_id"], kontakt_id=kontakt.id),
            ]
        )
        arbeitskopie = Objekt(
            org_id=seed["org_id"],
            nummer=2,
            name="Arbeitskopie Geheim",
            strasse="Nebenstrasse",
            hausnummer="2",
            plz="1010",
            ort="Wien",
            status="entwurf",
            entwurf_von_id=seed["objekt_id"],
        )
        db.add(arbeitskopie)
        db.flush()
        db.add(
            ObjektEinsatz(
                org_id=seed["org_id"],
                incident_id=incident_id,
                objekt_id=arbeitskopie.id,
                quelle="manuell",
                status="bestaetigt",
            )
        )
        dokument = ObjektDokument(
            org_id=seed["org_id"],
            objekt_id=seed["objekt_id"],
            dateiname_original="plan.pdf",
            pfad="plan.pdf",
            seitenzahl=1,
        )
        db.add(dokument)
        db.flush()
        db.add(
            ObjektDokumentSeite(
                org_id=seed["org_id"],
                objekt_id=seed["objekt_id"],
                dokument_id=dokument.id,
                seiten_nr=1,
                bei_einsatz_drucken=True,
            )
        )
        db.commit()
    finally:
        db.close()
    verlauf = _rufe(client, token, "einsatz_verlauf", incident_id=incident_id)
    assert [row["quelle"] for row in verlauf["eintraege"]] == ["tagebuch", "funkjournal"]
    assert "Autor Geheim" not in json.dumps(verlauf) and "Person Geheim" not in json.dumps(verlauf)
    funkjournal = _rufe(
        client,
        token,
        "einsatz_verlauf",
        incident_id=incident_id,
        quellen=["funkjournal"],
    )
    assert len(funkjournal["eintraege"]) == 1
    objekte = _rufe(client, token, "einsatz_objekte", incident_id=incident_id)["objekte"]
    assert objekte[0]["kontakte_anzahl"] == 1 and objekte[0]["dokumente"][0]["seiten"][0]["nr"] == 1
    assert "Kontakt Geheim" not in json.dumps(objekte)
    assert all(objekt["name"] != "Arbeitskopie Geheim" for objekt in objekte)
    lagebild = _rufe(client, token, "einsatz_lagebild", incident_id=incident_id)
    assert {
        "einsatz",
        "einheiten",
        "offene_auftraege",
        "offene_meldungen",
        "objekte",
        "letzte_eintraege",
    } <= set(lagebild)
    for secret in (
        "Anrufer Geheim",
        "Kommandant Geheim",
        "Fahrer Geheim",
        "Gerettete Geheim",
        "Person Geheim",
        "Autor Geheim",
        "Kontakt Geheim",
    ):
        assert secret not in json.dumps(lagebild, ensure_ascii=False)
    db = _db()
    try:
        db.query(OrgSettings).filter(OrgSettings.org_id == seed["org_id"]).one().objekt_module_enabled = False
        db.commit()
    finally:
        db.close()
    disabled = _rufe(client, token, "einsatz_objekte", incident_id=incident_id)
    assert disabled["objekte"] == [] and "hinweis" in disabled


def test_einsatz_objektdokument_download_und_unverknuepft(client, tmp_path):
    seed = _seed("mcp-einsatz-download-objekt", {"read": "readonly", "admin": "objekt_verwalter"})
    token = _token(client, seed, "read")
    incident_id, _ = _einsatz(seed["org_id"])
    original = b"Einsatz-Objektdokument"
    datei = tmp_path / "plan.pdf"
    datei.write_bytes(original)
    db = _db()
    try:
        dokument = ObjektDokument(
            org_id=seed["org_id"],
            objekt_id=seed["objekt_id"],
            dateiname_original="plan.pdf",
            pfad=str(datei),
            seitenzahl=1,
            groesse_bytes=len(original),
            ist_aktuelle_version=True,
            freigabe_status="freigegeben",
        )
        db.add(dokument)
        db.flush()
        db.add(
            ObjektEinsatz(
                org_id=seed["org_id"],
                incident_id=incident_id,
                objekt_id=dokument.objekt_id,
                quelle="manuell",
                status="bestaetigt",
            )
        )
        db.commit()
        dokument_id = dokument.id
    finally:
        db.close()
    result = _rufe(
        client,
        token,
        "einsatz_objekt_dokument_herunterladen",
        incident_id=incident_id,
        dokument_id=dokument_id,
    )
    assert client.get(result["download_url"]).content == original

    db = _db()
    try:
        foreign_objekt = Objekt(
            org_id=seed["org_id"],
            nummer=99,
            name="Nicht verknuepft",
            strasse="Nebenstrasse",
            hausnummer="9",
            plz="1010",
            ort="Wien",
            status="freigegeben",
        )
        db.add(foreign_objekt)
        db.flush()
        foreign = ObjektDokument(
            org_id=seed["org_id"],
            objekt_id=foreign_objekt.id,
            dateiname_original="fremd.pdf",
            pfad=str(datei),
            seitenzahl=1,
            groesse_bytes=len(original),
            ist_aktuelle_version=True,
            freigabe_status="freigegeben",
        )
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    finally:
        db.close()
    error = _rufe(
        client,
        token,
        "einsatz_objekt_dokument_herunterladen",
        incident_id=incident_id,
        dokument_id=foreign_id,
    )
    assert "Dokument nicht gefunden" in error["__fehler__"]
    manual_token, _ = erstelle_download_token(
        seed["org_id"],
        seed["users"]["read"],
        foreign_id,
        None,
        art="einsatz_objekt",
        incident_id=incident_id,
    )
    assert client.get(f"/api/mcp/downloads/{manual_token}").status_code == 404


def test_einsatz_medium_download_und_live_benutzerpruefung(client, tmp_path):
    seed = _seed("mcp-einsatz-download-medium", {"read": "readonly"}, objekt=False, kontakte=False)
    token = _token(client, seed, "read")
    incident_id, _ = _einsatz(seed["org_id"])
    data = b"Meldungsmedium"
    storage_path = tmp_path / "meldung.txt"
    storage_path.write_bytes(data)
    db = _db()
    try:
        message = db.query(Message).filter(Message.incident_id == incident_id).one()
        medium = MessageMedia(
            message_id=message.id,
            incident_id=incident_id,
            kind="datei",
            original_filename="meldung.txt",
            storage_path=str(storage_path),
            mime_type="text/plain",
            bytes=len(data),
        )
        db.add(medium)
        db.commit()
        medium_id = medium.id
    finally:
        db.close()
    result = _rufe(
        client,
        token,
        "einsatz_medium_herunterladen",
        incident_id=incident_id,
        art="meldung",
        medium_id=medium_id,
    )
    response = client.get(result["download_url"])
    assert response.status_code == 200 and response.content == data
    assert response.headers["content-type"].startswith("application/octet-stream")
    other_incident_id, _ = _einsatz(seed["org_id"])
    assert "Medium nicht gefunden" in _rufe(
        client,
        token,
        "einsatz_medium_herunterladen",
        incident_id=other_incident_id,
        art="meldung",
        medium_id=medium_id,
    )["__fehler__"]
    db = _db()
    try:
        db.get(User, seed["users"]["read"]).active = False
        db.commit()
    finally:
        db.close()
    assert client.get(result["download_url"]).status_code == 403


def test_mcp_auftrag_anlegen_mit_rechten_zuordnung_und_broadcast(client, monkeypatch):
    seed = _seed("mcp-einsatz-schreib-auftrag", {"rec": "recorder", "read": "readonly"}, objekt=False, kontakte=False)
    token = _token(client, seed, "rec")
    readonly_token = _token(client, seed, "read")
    incident_id, vehicle_id = _einsatz(seed["org_id"])
    tasks_id, _ = _schreibspalten(incident_id)
    broadcast = []

    async def fake_broadcast(incident_id, event):
        broadcast.append((incident_id, event))

    monkeypatch.setattr("app.services.broadcast.manager.broadcast", fake_broadcast)
    result = _rufe(
        client,
        token,
        "einsatz_auftrag_anlegen",
        incident_id=incident_id,
        titel="  Wasserversorgung prüfen  ",
        detail="Hydrant Nord",
        einheit_id=vehicle_id,
        abschnitt_id=tasks_id,
    )
    assert result["angelegt"] and result["auftrag"]["titel"] == "Wasserversorgung prüfen"
    assert result["auftrag"]["einheit"]["id"] == vehicle_id
    assert broadcast[0][0] == incident_id and broadcast[0][1]["type"] == "task_created"
    db = _db()
    try:
        task = db.get(Task, result["auftrag"]["id"])
        assert task.created_by_user_id == seed["users"]["rec"] and task.column_id == tasks_id
        assert db.query(AuditLog).filter_by(action="einsatz.mcp_auftrag_angelegt", entity_id=task.id).one()
        from app.models.incident import IncidentChange

        assert db.query(IncidentChange).filter_by(action="task.created", entity_id=task.id).one()
    finally:
        db.close()
    assert result["auftrag"]["id"] in {
        row["id"] for row in _rufe(client, token, "einsatz_auftraege", incident_id=incident_id)["items"]
    }
    listed = _mcp(client, readonly_token, "tools/list", {}, 95).text
    assert not any(name in listed for name in EINSATZ_SCHREIB_TOOLS)
    assert "__fehler__" in _rufe(
        client, readonly_token, "einsatz_auftrag_anlegen", incident_id=incident_id, titel="Nein"
    )


def test_mcp_auftrag_sichtbarkeit_abschnitt_einheit_und_abgeschlossen(client):
    seed = _seed("mcp-einsatz-schreib-pruefung", {"rec": "recorder"}, objekt=False, kontakte=False)
    other = _seed("mcp-einsatz-schreib-fremd", {"rec": "recorder"}, objekt=False, kontakte=False)
    token = _token(client, seed, "rec")
    incident_id, _ = _einsatz(seed["org_id"])
    tasks_id, _ = _schreibspalten(incident_id)
    foreign_id, foreign_vehicle = _einsatz(other["org_id"])
    _, foreign_messages = _schreibspalten(foreign_id)
    db = _db()
    try:
        rescued_id = db.query(IncidentColumn.id).filter_by(incident_id=incident_id, column_kind="rescued").scalar()
    finally:
        db.close()
    assert "Abschnitt nicht gefunden" in _rufe(
        client, token, "einsatz_auftrag_anlegen", incident_id=incident_id, titel="x", abschnitt_id=rescued_id
    )["__fehler__"]
    assert "Abschnitt nicht gefunden" in _rufe(
        client, token, "einsatz_auftrag_anlegen", incident_id=incident_id, titel="x", abschnitt_id=foreign_messages
    )["__fehler__"]
    assert "Einheit nicht gefunden" in _rufe(
        client, token, "einsatz_auftrag_anlegen", incident_id=incident_id, titel="x", einheit_id=foreign_vehicle
    )["__fehler__"]
    assert "Einsatz nicht gefunden." in _rufe(
        client, token, "einsatz_auftrag_anlegen", incident_id=foreign_id, titel="x"
    )["__fehler__"]
    db = _db()
    try:
        db.add(IncidentOrg(incident_id=foreign_id, org_id=seed["org_id"], role="collaborator"))
        db.commit()
    finally:
        db.close()
    assert _rufe(client, token, "einsatz_auftrag_anlegen", incident_id=foreign_id, titel="Kollaboration")["angelegt"]
    db = _db()
    try:
        db.get(Incident, incident_id).status = "closed"
        db.commit()
    finally:
        db.close()
    error = _rufe(client, token, "einsatz_auftrag_anlegen", incident_id=incident_id, titel="Gesperrt")
    assert "Einsatz ist abgeschlossen" in error["__fehler__"]
    db = _db()
    try:
        assert db.query(Task).filter(Task.incident_id == incident_id, Task.title == "Gesperrt").count() == 0
    finally:
        db.close()
    assert tasks_id


def test_mcp_meldung_anlegen_status_faelligkeit_audit_und_broadcast(client, monkeypatch):
    seed = _seed("mcp-einsatz-schreib-meldung", {"lead": "incident_leader"}, objekt=False, kontakte=False)
    token = _token(client, seed, "lead")
    incident_id, _ = _einsatz(seed["org_id"])
    _, messages_id = _schreibspalten(incident_id)
    broadcast = []

    async def fake_broadcast(incident_id, event):
        broadcast.append((incident_id, event))

    monkeypatch.setattr("app.services.broadcast.manager.broadcast", fake_broadcast)
    result = _rufe(client, token, "einsatz_meldung_anlegen", incident_id=incident_id, titel="Achtung",
                   status="achtung", faellig_in_min=15)
    assert result["angelegt"] and result["meldung"]["status"] == "achtung"
    assert result["meldung"]["abschnitt"]["id"] == messages_id
    assert broadcast[0][0] == incident_id and broadcast[0][1]["type"] == "message_created"
    db = _db()
    try:
        message = db.get(Message, result["meldung"]["id"])
        incident = db.get(Incident, incident_id)
        assert message.author_name.endswith("(MCP)") and message.due_at == incident.started_at + timedelta(minutes=15)
        assert db.query(AuditLog).filter_by(action="einsatz.mcp_meldung_angelegt", entity_id=message.id).one()
    finally:
        db.close()
    assert "__fehler__" in _rufe(
        client, token, "einsatz_meldung_anlegen", incident_id=incident_id, titel="Nein", status="erledigt"
    )
