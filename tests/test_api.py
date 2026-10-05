"""Tests für die REST-API (Einsatz anlegen)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.incident import IncidentAlarmJob
from app.models.master import FireDept
from app.models.user import ApiKey


@pytest.fixture
def api_key(setup_db):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"api-test-{uuid.uuid4().hex[:8]}", name="API-Test-Org",
            color="#123456", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        key = ApiKey(key_hash=hash_api_key(raw), label="Test", org_id=org.id)
        db.add(key)
        db.commit()
    finally:
        db.close()
    return raw


PAYLOAD = {
    "Key": "test-key-001",
    "Nummer": 1,
    "AlarmDatumZeit": "2026-01-01T10:00:00",
    "Stufe": "t1",
    "Art": "T",
    "Meldung": "Testmeldung",
    "Einsatzgrund": "Test",
    "Ort": "Wolfurt",
    "Strasse": "Teststraße",
    "HausNr": "1",
    "Uebung": True,
}


def test_create_incident_no_key(client):
    r = client.post("/api/v1/einsatz", json=PAYLOAD)
    assert r.status_code == 422  # missing header


def test_create_incident_invalid_key(client):
    r = client.post("/api/v1/einsatz", json=PAYLOAD, headers={"X-API-Key": "invalid"})
    assert r.status_code == 401


def test_create_incident_success(client, api_key, monkeypatch):
    notify_calls = []

    async def fake_notify(db, incident, **kwargs):
        notify_calls.append(incident.id)

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )
    payload = dict(
        PAYLOAD,
        Key=f"test-key-{uuid.uuid4().hex}",
        Strasse=f"Teststrasse-{uuid.uuid4().hex}",
    )
    r = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert data["created"] is True
    assert data["id"] > 0
    incident_id = data["id"]
    assert notify_calls == [incident_id]

    # Idempotency: same Key again → created=False
    r2 = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert r2.status_code == 200
    assert r2.json()["created"] is False
    assert r2.json()["id"] == incident_id
    assert notify_calls == [incident_id]


def test_api_create_enqueues_alarm_jobs(client, api_key, monkeypatch):
    async def no_process(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.alarm_outbox.process_incident_alarm", no_process)
    payload = dict(PAYLOAD, Key=f"outbox-{uuid.uuid4().hex}", Strasse=f"Outbox-{uuid.uuid4().hex}")
    response = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=response.json()["id"]).all()
        assert {job.channel for job in jobs} == {"sms", "push", "teams"}
    finally:
        db.close()


def test_create_incident_key_race_returns_existing(client, api_key, monkeypatch):
    import app.routers.api_v1 as api_v1

    winner_id = None
    process_calls = []

    def race_create(db, **kwargs):
        nonlocal winner_id
        winner_db = SessionLocal()
        set_tenant_context(winner_db, None)
        try:
            winner = Incident(
                primary_org_id=kwargs["primary_org_id"], external_key="race-1",
                alarm_type_code="T1", status="active",
            )
            winner_db.add(winner)
            winner_db.commit()
            winner_id = winner.id
        finally:
            winner_db.close()
        raise IntegrityError("x", {}, Exception("dup"))

    async def no_process(*args, **kwargs):
        process_calls.append(args)

    monkeypatch.setattr(api_v1, "create_incident", race_create)
    monkeypatch.setattr("app.services.alarm_outbox.process_incident_alarm", no_process)
    payload = dict(PAYLOAD, Key="race-1", Strasse=f"Race-{uuid.uuid4().hex}")
    response = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert response.status_code == 200
    assert response.json()["created"] is False
    assert response.json()["id"] == winner_id
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Incident).filter_by(external_key="race-1").count() == 1
    finally:
        db.close()
    assert process_calls == []

def test_create_incident_stores_caller_info(client, api_key):
    """Name/Telefon aus dem Alarm-Webhook landen auf caller_name/caller_phone —
    Anzeige mit Wählfunktion im Alarm-Modal (Klick auf Alarmstichwort im Board).

    Eigenes Stichwort (t2 statt t1): sonst faengt die Duplikat-Sperre (gleiches
    Stichwort, gleiche Org, innerhalb weniger Sekunden) diesen Post faelschlich
    als Duplikat von test_create_incident_success() ab (siehe
    app/services/incident_service.py::create_incident()).
    """
    payload = dict(PAYLOAD, Key="test-key-caller", Stufe="t2", Name="Max Mustermann", Telefon="+43 664 1234567")
    r = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    incident_id = r.json()["id"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.incident import Incident
        incident = db.get(Incident, incident_id)
        assert incident.caller_name == "Max Mustermann"
        assert incident.caller_phone == "+43 664 1234567"
    finally:
        db.close()


def test_list_active(client, api_key):
    r = client.get("/api/v1/einsatz/active", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── Duplikat-Sperre: fast zeitgleiche Alarme mit gleichem Stichwort, aber
#    UNTERSCHIEDLICHEM Key (der eigentliche Vorfall - siehe
#    app/services/incident_service.py::create_incident()) ──────────────────
#
# Eigene, frische Org + eigener API-Key statt der geteilten Home-Org/api_key-
# Fixture: setup_db ist session-scoped (DB wird NICHT zwischen Tests
# zurueckgesetzt) - mit der Home-Org wuerden bereits von anderen Tests in
# dieser Datei angelegte T1-Einsaetze faelschlich als Duplikat-Kandidat
# gefunden.

@pytest.fixture
def duplicate_guard_api_key(setup_db):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        import uuid
        org = FireDept(
            slug=f"dup-guard-api-{uuid.uuid4().hex[:8]}", name="Duplikat-Sperre-Test-Org",
            color="#654321", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        key = ApiKey(key_hash=hash_api_key(raw), label="Test", org_id=org.id)
        db.add(key)
        db.commit()
    finally:
        db.close()
    return raw


def test_fast_zeitgleicher_alarm_mit_anderem_key_wird_nicht_doppelt_angelegt(
    client, duplicate_guard_api_key,
):
    payload_1 = dict(PAYLOAD, Key="dup-guard-key-1", Uebung=False)
    payload_2 = dict(PAYLOAD, Key="dup-guard-key-2", Uebung=False)

    r1 = client.post("/api/v1/einsatz", json=payload_1, headers={"X-API-Key": duplicate_guard_api_key})
    assert r1.status_code == 200
    assert r1.json()["created"] is True
    erster_id = r1.json()["id"]

    r2 = client.post("/api/v1/einsatz", json=payload_2, headers={"X-API-Key": duplicate_guard_api_key})
    assert r2.status_code == 200
    assert r2.json()["created"] is False
    assert r2.json()["id"] == erster_id
    # external_key bleibt der des ZUERST angelegten Einsatzes - der zweite Post
    # (anderer Key) wird nicht als eigener Einsatz gefuehrt.
    assert r2.json()["external_key"] == "dup-guard-key-1"

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        anzahl = db.query(Incident).filter(Incident.external_key.in_(
            ["dup-guard-key-1", "dup-guard-key-2"],
        )).count()
        assert anzahl == 1
    finally:
        db.close()


# ── Leitstellennummer (EUS): stabiler Matching-Schlüssel gegen bereits per
#    LIS/DIBOS angelegte Einsätze (Incident.lis_operation_number) ───────────
#
# Eigene, frische Org (siehe Begründung bei duplicate_guard_api_key oben).

@pytest.fixture
def leitstellennummer_api_key(setup_db):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        import uuid
        org = FireDept(
            slug=f"lsz-api-{uuid.uuid4().hex[:8]}", name="Leitstellennummer-Test-Org",
            color="#123456", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        key = ApiKey(key_hash=hash_api_key(raw), label="Test", org_id=org.id)
        db.add(key)
        db.commit()
        org_id = org.id
    finally:
        db.close()
    return raw, org_id


def test_leitstellennummer_stored_on_new_incident(client, leitstellennummer_api_key):
    api_key, _org_id = leitstellennummer_api_key
    payload = dict(PAYLOAD, Key="lsz-key-neu", Leitstellennummer="fu26303655")
    r = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.json()["created"] is True
    incident_id = r.json()["id"]

    from app.models.incident import Incident
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        assert incident.lis_operation_number == "fu26303655"
    finally:
        db.close()


def test_leitstellennummer_links_existing_dibos_incident_ohne_duplikat(client, leitstellennummer_api_key):
    """Ein per DIBOS angelegter Einsatz (lis_operation_number gesetzt, kein
    lis_operation_id, kein external_key) darf nicht doppelt angelegt werden, wenn
    derselbe Alarm zusätzlich über EUS mit derselben Leitstellennummer eintrifft —
    stattdessen wird der bestehende Einsatz verknüpft (external_key nachgetragen).
    Koordinaten (hier simuliert wie von DIBOS geliefert) dürfen dabei NICHT
    überschrieben werden — EUS liefert selbst keine Koordinaten."""
    api_key, org_id = leitstellennummer_api_key

    from app.models.incident import Incident
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        bestehend = Incident(
            alarm_type_code="T1",
            status="active",
            primary_org_id=org_id,
            address_street="Andere Straße",
            address_city="Andereort",
            lis_operation_number="fu26303655",
            lat=47.4925,
            lng=9.7503,
        )
        db.add(bestehend)
        db.commit()
        bestehend_id = bestehend.id
    finally:
        db.close()

    payload = dict(PAYLOAD, Key="lsz-key-verknuepft", Leitstellennummer="fu26303655")
    r = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.json()["created"] is False
    assert r.json()["id"] == bestehend_id

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, bestehend_id)
        assert incident.external_key == "lsz-key-verknuepft"
        assert incident.lat == 47.4925
        assert incident.lng == 9.7503

        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id,
            Incident.lis_operation_number == "fu26303655",
        ).count()
        assert anzahl == 1
    finally:
        db.close()


@pytest.mark.parametrize(
    ("started_at", "alarm_started", "expected_calls"),
    [
        (None, False, 1),
        (None, True, 0),
        (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=16), False, 0),
    ],
)
def test_lis_link_backfills_only_fresh_unalarmed_incident(
    client, leitstellennummer_api_key, monkeypatch, started_at, alarm_started, expected_calls,
):
    """EUS-Link alarmiert nur frische Einsätze ohne Start-Marker nach."""
    api_key, org_id = leitstellennummer_api_key
    calls = []

    async def fake_notify(db, incident, **kwargs):
        calls.append((incident.id, kwargs["source"]))

    monkeypatch.setattr("app.services.incident_notify.notify_incident_created", fake_notify)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = Incident(
            alarm_type_code="T1", status="active", primary_org_id=org_id,
            lis_operation_number=f"backfill-{org_id}", started_at=started_at,
        )
        db.add(incident)
        db.flush()
        incident_id = incident.id
        if alarm_started:
            from app.core.audit import write_audit
            write_audit(db, "incident.alarm_started", incident_id=incident.id)
        db.commit()
    finally:
        db.close()

    payload = dict(PAYLOAD, Key=f"backfill-key-{org_id}", Leitstellennummer=f"backfill-{org_id}")
    response = client.post("/api/v1/einsatz", json=payload, headers={"X-API-Key": api_key})
    assert response.status_code == 200
    assert calls == ([(incident_id, "api_lis_verknuepfung")] if expected_calls else [])
