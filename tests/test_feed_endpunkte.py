"""Basis-Endpunkte und Revisions-Hook des Einsatz-Feeds."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentOrg, IncidentWacheStatus, Message, Task
from app.models.master import FireDept
from app.models.objekt import ObjektEinsatz
from app.models.user import ApiKey
from app.schemas.feed import FORBIDDEN_FIELDS, FeedEinsatzBasis

_ANGELEGTE_EINSAETZE: list[int] = []


@pytest.fixture(autouse=True)
def _einsaetze_aufraeumen():
    """Entfernt alle hier angelegten Einsaetze, damit andere Tests (z. B. "neuester
    Einsatz der Org") nicht von Testdaten dieser Datei beeinflusst werden."""
    yield
    ids = list(_ANGELEGTE_EINSAETZE)
    _ANGELEGTE_EINSAETZE.clear()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for incident_id in ids:
            incident = db.get(Incident, incident_id)
            if incident is not None:
                for kind in (ObjektEinsatz, IncidentOrg):
                    for row in db.query(kind).filter(kind.incident_id == incident_id).all():
                        db.delete(row)
                db.delete(incident)
        db.commit()
    finally:
        db.close()


def _key(org_id: int, scopes: str = "einsatz:read") -> str:
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(ApiKey(key_hash=hash_api_key(raw), label="Feed endpoint", org_id=org_id, scopes=scopes))
        db.commit()
    finally:
        db.close()
    return raw


def _incident(org_id: int, *, status="active", exercise=False, started_at=None) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(
            primary_org_id=org_id, alarm_type_code="B2", status=status,
            is_exercise=exercise, started_at=started_at or datetime.now(UTC),
            address_city="Musterort",
        )
        if status == "closed":
            incident.closed_at = incident.started_at + timedelta(hours=1)
        db.add(incident)
        db.commit()
        _ANGELEGTE_EINSAETZE.append(incident.id)
        return incident.id
    finally:
        db.close()


def _home_org_id() -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(FireDept).filter(FireDept.is_home_org.is_(True)).one().id
    finally:
        db.close()


def test_feed_list_detail_head_and_etag(client):
    org_id = _home_org_id()
    started_at = datetime.now(UTC) - timedelta(minutes=5)
    incident_id = _incident(org_id, started_at=started_at)
    headers = {"X-API-Key": _key(org_id)}

    since = (started_at - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    listing = client.get(f"/api/v1/feed/einsaetze?since={since}", headers=headers)
    assert listing.status_code == 200
    assert listing.headers["Cache-Control"] == "private, no-cache"
    assert "X-API-Key" in listing.headers["Vary"]
    body = listing.json()
    assert set(body) == set(FeedEinsatzBasis.model_fields) ^ set(FeedEinsatzBasis.model_fields) | {
        "schema_version", "server_time", "einsaetze"
    }
    assert incident_id in [einsatz["id"] for einsatz in body["einsaetze"]]
    assert set(body["einsaetze"][0]) == set(FeedEinsatzBasis.model_fields)
    assert not (set(body["einsaetze"][0]) & FORBIDDEN_FIELDS)
    assert body["server_time"].endswith("Z")

    etag = listing.headers["ETag"]
    path = f"/api/v1/feed/einsaetze?since={since}"
    conditional = client.get(path, headers={**headers, "If-None-Match": f'W/{etag}, "other"'})
    assert conditional.headers["ETag"] == etag
    assert conditional.status_code == 304
    assert client.get(path, headers={**headers, "If-None-Match": "*"}).status_code == 304
    assert client.get(f"/api/v1/feed/einsaetze/{incident_id}", headers=headers).status_code == 200
    head = client.get("/api/v1/feed/head", headers=headers)
    assert head.status_code == 200
    assert set(head.json()) == {"rev", "server_time", "active_count", "schema_version"}
    assert head.json()["rev"] == client.get("/api/v1/feed/einsaetze", headers=headers).headers["ETag"].strip('"')


@pytest.mark.parametrize("query", ["status=invalid", "since=nein", "limit=0", "limit=201"])
def test_feed_rejects_invalid_filters(client, query):
    response = client.get(f"/api/v1/feed/einsaetze?{query}", headers={"X-API-Key": _key(_home_org_id())})
    assert response.status_code == 422
    assert "muss" in response.json()["detail"]


def test_feed_scope_exercises_and_cross_org_visibility(client):
    org_a = _home_org_id()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug="feed-org-b", name="Feed Org B", is_home_org=False)
        db.add(org_b)
        db.commit()
        org_b_id = org_b.id
    finally:
        db.close()
    incident_a = _incident(org_a)
    exercise = _incident(org_a, exercise=True)
    key_b = {"X-API-Key": _key(org_b_id, "einsatz:read,einsatz:read:kraefte")}
    key_a = {"X-API-Key": _key(org_a)}
    assert client.get("/api/v1/feed/einsaetze", headers={"X-API-Key": _key(org_a, "")}).status_code == 403
    assert client.get(f"/api/v1/feed/einsaetze/{incident_a}?include=kraefte", headers=key_b).status_code == 404
    visible_b = client.get("/api/v1/feed/einsaetze?include=kraefte", headers=key_b).json()["einsaetze"]
    default_a = client.get("/api/v1/feed/einsaetze", headers=key_a).json()["einsaetze"]
    with_exercises = client.get(
        "/api/v1/feed/einsaetze?include_exercises=true", headers=key_a,
    ).json()["einsaetze"]
    assert incident_a not in [item["id"] for item in visible_b]
    assert exercise not in [item["id"] for item in default_a]
    assert exercise in [item["id"] for item in with_exercises]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(IncidentOrg(incident_id=incident_a, org_id=org_b_id))
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/v1/feed/einsaetze/{incident_a}", headers=key_b).status_code == 200


@pytest.mark.parametrize("kind", ["incident", "column", "wache", "task", "message"])
def test_feed_revision_hook_bumps_once_per_flush(kind):
    org_id = _home_org_id()
    incident_id = _incident(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        assert incident is not None
        initial = incident.feed_rev
        if kind == "incident":
            incident.address_city = "Anderer Ort"
        elif kind == "column":
            db.add(IncidentColumn(incident_id=incident_id, code="feed", title="Feed"))
        elif kind == "wache":
            db.add(IncidentWacheStatus(incident_id=incident_id, wache_unid=f"w-{incident_id}", status="alarmiert"))
        elif kind == "task":
            db.add_all([Task(incident_id=incident_id, title="A"), Task(incident_id=incident_id, title="B")])
        else:
            db.add(Message(incident_id=incident_id, title="Meldung"))
        db.commit()
        db.refresh(incident)
        assert incident.feed_rev == initial + 1
    finally:
        db.close()
