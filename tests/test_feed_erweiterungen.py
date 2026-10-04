"""Erweiterte, strikt whitelisted Einsatz-Feed-Ansichten."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal, get_db
from app.models.incident import (
    Incident,
    IncidentColumn,
    IncidentCommLog,
    IncidentVehicle,
    IncidentWacheStatus,
    Message,
    RescuedPerson,
    Task,
)
from app.models.master import FireDept, VehicleMaster
from app.models.user import ApiKey
from app.schemas.feed import FORBIDDEN_FIELDS

_INCIDENT_IDS: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup_incidents():
    yield
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for incident_id in _INCIDENT_IDS:
            incident = db.get(Incident, incident_id)
            if incident is not None:
                db.delete(incident)
        db.flush()
        for master in db.query(VehicleMaster).filter(VehicleMaster.code == "FL-TEST").all():
            db.delete(master)
        db.commit()
    finally:
        _INCIDENT_IDS.clear()
        db.close()


def _org_id() -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(FireDept).filter(FireDept.is_home_org.is_(True)).one().id
    finally:
        db.close()


def _key(org_id: int, scopes: str) -> dict[str, str]:
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Feed Erweiterungen", org_id=org_id, scopes=scopes))
        db.commit()
    finally:
        db.close()
    return {"X-API-Key": raw}


def _incident_with_extensions(org_id: int, *, status: str = "active") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        incident = Incident(
            primary_org_id=org_id, alarm_type_code="B2", status=status, started_at=now,
            closed_at=now if status == "closed" else None,
        )
        db.add(incident)
        db.flush()
        column = IncidentColumn(
            incident_id=incident.id, code="auftraege", title="Aufträge", column_kind="tasks", display_order=4,
            section_leader_name="GEHEIMER-ABSCHNITTSLEITER",
        )
        master = VehicleMaster(
            dept_id=org_id, code="FL-TEST", name="Feed Fahrzeug", type="TLF", is_external=True,
            adhoc_org_short="EXT", kennzeichen="GEHEIM-KZ",
        )
        db.add_all([column, master])
        db.flush()
        db.add_all([
            IncidentVehicle(
                incident_id=incident.id, column_id=column.id, vehicle_master_id=master.id,
                commander_name="GEHEIMER-KOMMANDANT", fahrer_name="GEHEIMER-FAHRER", km_gefahren=99,
                unit_status="Am Einsatzort",
            ),
            IncidentVehicle(
                incident_id=incident.id, column_id=column.id, vehicle_master_id=master.id,
                removed_at=now, unit_status="Entfernt",
            ),
            IncidentWacheStatus(
                incident_id=incident.id, wache_unid="wache-feed", wache_name="Wache Feed", status="alarmiert",
                status_text_raw="GEHEIMER-WACHENTEXT", status_at=now,
            ),
            Task(
                incident_id=incident.id, column_id=column.id, title="Auftrag Feed", detail="GEHEIMER-TASK-DETAIL",
                is_cancelled=True, cancelled_at=now,
            ),
            Message(
                incident_id=incident.id, column_id=column.id, title="Meldung Feed",
                detail="GEHEIMES-MELDUNGS-DETAIL", author_name="GEHEIMER-AUTOR",
            ),
            RescuedPerson(
                incident_id=incident.id, column_id=column.id, name="GEHEIME-GERETTETE-PERSON",
            ),
            IncidentCommLog(
                incident_id=incident.id, direction="in", message="GEHEIMES-FUNKJOURNAL",
                author_name="GEHEIMER-FUNK-AUTOR",
            ),
        ])
        db.commit()
        _INCIDENT_IDS.append(incident.id)
        return incident.id
    finally:
        db.close()


@pytest.mark.parametrize(
    ("include", "scope"),
    [("kraefte", "einsatz:read:kraefte"), ("wachen", "einsatz:read:kraefte"), ("board", "einsatz:read:board")],
)
def test_include_requires_its_scope(client, include, scope):
    org_id = _org_id()
    _incident_with_extensions(org_id)
    denied = client.get(
        f"/api/v1/feed/einsaetze?include={include}", headers=_key(org_id, "einsatz:read"),
    )
    assert denied.status_code == 403
    assert client.get(
        f"/api/v1/feed/einsaetze?include={include}", headers=_key(org_id, f"einsatz:read,{scope}"),
    ).status_code == 200


def test_include_content_whitelist_etag_and_304(client):
    org_id = _org_id()
    incident_id = _incident_with_extensions(org_id)
    headers = _key(org_id, "einsatz:read,einsatz:read:kraefte,einsatz:read:board")
    path = f"/api/v1/feed/einsaetze/{incident_id}?include=board,wachen,kraefte,objekt"
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["objekt"] is None
    assert len(payload["kraefte"]) == 1
    assert payload["kraefte"][0]["vehicle_code"] == "FL-TEST"
    assert payload["wachen"] == [{
        "wache_unid": "wache-feed", "wache_name": "Wache Feed", "status": "alarmiert",
        "status_at": payload["wachen"][0]["status_at"],
    }]
    assert payload["wachen"][0]["status_at"].endswith("Z")
    assert payload["board"]["tasks"][0]["is_cancelled"] is True
    assert "section_leader_name" not in payload["board"]["columns"][0]
    text = response.text
    assert not (set(_keys(payload)) & FORBIDDEN_FIELDS)
    secrets = (
        "GEHEIMER-KOMMANDANT", "GEHEIMER-FAHRER", "GEHEIMER-TASK-DETAIL", "GEHEIMER-AUTOR",
        "GEHEIMER-WACHENTEXT",
        "GEHEIME-GERETTETE-PERSON", "GEHEIMES-FUNKJOURNAL", "GEHEIMER-FUNK-AUTOR",
    )
    for secret in secrets:
        assert secret not in text
    assert client.get(path, headers={**headers, "If-None-Match": response.headers["ETag"]}).status_code == 304
    assert response.headers["ETag"] != client.get(
        f"/api/v1/feed/einsaetze/{incident_id}?include=objekt", headers=headers,
    ).headers["ETag"]
    assert client.get("/api/v1/feed/einsaetze?include=unbekannt", headers=headers).status_code == 422


@pytest.mark.parametrize("kind", ["vehicle", "wache", "task", "column"])
def test_etag_changes_for_included_children(client, kind):
    org_id = _org_id()
    incident_id = _incident_with_extensions(org_id)
    headers = _key(org_id, "einsatz:read,einsatz:read:kraefte,einsatz:read:board")
    path = f"/api/v1/feed/einsaetze/{incident_id}?include=kraefte,wachen,board"
    before = client.get(path, headers=headers).headers["ETag"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        assert incident is not None
        if kind == "vehicle":
            incident.vehicles[0].unit_status = "Einsatzbereit"
        elif kind == "wache":
            incident.wache_status_entries[0].status = "ausgerückt"
        elif kind == "task":
            incident.tasks[0].title = "Geänderter Auftrag"
        else:
            incident.columns[0].title = "Geänderte Spalte"
        db.commit()
    finally:
        db.close()
    assert client.get(path, headers=headers).headers["ETag"] != before


def test_all_includes_do_not_add_queries_per_incident(client):
    """selectinload muss bei fünf Einsätzen gleich viele Statements wie bei einem erzeugen."""
    org_id = _org_id()
    _incident_with_extensions(org_id, status="closed")
    headers = _key(org_id, "einsatz:read,einsatz:read:kraefte,einsatz:read:board")
    path = "/api/v1/feed/einsaetze?status=closed&include=kraefte,wachen,board"
    session_generator = client.app.dependency_overrides[get_db]()
    db = next(session_generator)
    engine = db.get_bind()
    session_generator.close()
    # Der erste Key-Zugriff schreibt last_used_at; für den reinen Lesevergleich aufwärmen.
    assert client.get(path, headers=headers).status_code == 200

    def count_request() -> int:
        statements: list[str] = []

        def record(*args):
            statements.append(args[2])

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get(path, headers=headers).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    one_incident = count_request()
    for _ in range(4):
        _incident_with_extensions(org_id, status="closed")
    assert count_request() == one_incident


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()
