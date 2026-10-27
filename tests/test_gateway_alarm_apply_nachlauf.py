"""Gateway-Rohdaten, Fehlerantwort und einmaliger Nachlauf bei Neuanlage."""

import base64
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal, get_db
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident, IncidentAlarmJob
from app.routers.gateway_api import router
from app.services import serial_alarm_service


@pytest.fixture
def alarm_api(setup_db, monkeypatch):
    token = uuid4().hex
    calls = []
    with SessionLocal() as db:
        set_tenant_context(db, None)
        gateway = Gateway(org_id=1, name="Alarm-Test", device_token_hash=hash_api_key(token))
        db.add(gateway)
        db.commit()
        gateway_id = gateway.id

    def record(name):
        return lambda *args, **kwargs: calls.append(name)

    async def broadcast(*args, **kwargs):
        calls.append("broadcast")

    monkeypatch.setattr("app.services.print_dispatcher.autoprint_alarm_background", record("alarm_print"))
    monkeypatch.setattr("app.services.print_dispatcher.autoprint_incident_background", record("incident_print"))
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", record("match"))
    monkeypatch.setattr("app.routers.api_v1._geocode_incident", record("geocode"))
    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)

    def session():
        with SessionLocal() as db:
            set_tenant_context(db, None)
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        def post(payload):
            return client.post("/api/v1/gateway/alarms", json=payload,
                               headers={"Authorization": f"Bearer {token}"})

        yield post, gateway_id, calls

    with SessionLocal() as db:
        set_tenant_context(db, None)
        rows = db.query(AlarmIngest).filter(
            AlarmIngest.org_id == 1, AlarmIngest.gateway_id == gateway_id,
        ).all()
        incident_ids = [row.einsatz_id for row in rows if row.einsatz_id]
        for job in db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.org_id == 1, IncidentAlarmJob.incident_id.in_(incident_ids),
        ):
            db.delete(job)
        db.commit()


def payload(**kwargs):
    values = {
        "raw_text": "Alarm " + uuid4().hex,
        "parsed": {"alarm_type_code": "T1", "street": "Teststrasse " + uuid4().hex,
                   "city": "Testort", "house_no": "1"},
    }
    values.update(kwargs)
    return values


def saved(row_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(AlarmIngest).filter(AlarmIngest.org_id == 1, AlarmIngest.id == row_id).one()
        db.expunge(row)
        return row


def jobs(incident_id):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return [(job.id, job.channel) for job in db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.org_id == 1, IncidentAlarmJob.incident_id == incident_id,
        ).order_by(IncidentAlarmJob.id)]


def test_new_fields_sequence_duplicate_and_merged_run_nachlauf(alarm_api):
    post, gateway_id, calls = alarm_api
    raw = b"Alarm\x00\xff\x0c"
    data = payload(sequence=42, raw_bytes_b64=base64.b64encode(raw).decode(),
                   received_at="2026-10-26T12:00:00+02:00", source_addr="10.0.0.1:8000", incomplete=True)
    response = post(data)
    assert response.status_code == 200
    result = response.json()
    assert result["duplicate"] is False and result["process_status"] == "applied"
    row = saved(result["ingest_id"])
    assert row.gateway_id == gateway_id and row.sequence == 42 and row.raw_bytes == raw
    assert row.received_at_gateway == datetime(2026, 10, 26, 10)  # noqa: DTZ001
    assert row.dedup_action == "created"
    initial_jobs = jobs(result["einsatz_id"])
    assert {channel for _, channel in initial_jobs} == {"sms", "push", "teams", "mail"}
    assert calls == ["broadcast", "alarm_print", "geocode", "match", "incident_print"]
    calls.clear()

    duplicate = post({**data, "raw_text": "Geaenderter Text"})
    assert duplicate.status_code == 200
    assert duplicate.json() == {**result, "duplicate": True}
    assert calls == []

    merged = post({**data, "sequence": 43, "raw_text": "Ergaenzung " + uuid4().hex})
    assert merged.status_code == 200
    merged_result = merged.json()
    assert merged_result["duplicate"] is False and merged_result["process_status"] == "applied"
    assert merged_result["einsatz_id"] == result["einsatz_id"]
    assert saved(merged_result["ingest_id"]).dedup_action == "merged"
    # Ohne Leitstellennummer kein Auto-Merge und kein erneuter Anlagenachlauf.
    assert calls == [] and jobs(result["einsatz_id"]) == initial_jobs
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(Incident).filter(
            Incident.primary_org_id == 1, Incident.address_street == data["parsed"]["street"],
        ).count() == 1


def test_processing_exception_returns_200_and_preserves_raw(alarm_api, monkeypatch, caplog):
    post, _, calls = alarm_api

    def fail(*args, **kwargs):
        raise RuntimeError("Einsatzanlage fehlgeschlagen")

    monkeypatch.setattr(serial_alarm_service, "_create_or_link_incident", fail)
    data = payload(sequence=9, raw_bytes_b64=base64.b64encode(b"Original").decode())
    response = post(data)
    assert response.status_code == 200
    result = response.json()
    assert result["process_status"] == "error" and result["einsatz_id"] is None
    row = saved(result["ingest_id"])
    assert row.raw_text == data["raw_text"] and row.raw_bytes == b"Original"
    assert row.process_error == "Einsatzanlage fehlgeschlagen"
    assert "Einsatzanlage fehlgeschlagen" in caplog.text and calls == []


@pytest.mark.parametrize("database_error", [False, True])
def test_unexpected_processing_exception_still_returns_raw_id(alarm_api, monkeypatch, caplog, database_error):
    post, _, calls = alarm_api

    def fail(db, *args, **kwargs):
        if database_error:
            db.add(AlarmIngest(org_id=1, gateway_id=1, raw_text=None, raw_hash="bad"))
            db.flush()
        raise RuntimeError("Verarbeitung abgebrochen")

    monkeypatch.setattr(serial_alarm_service, "process_ingest", fail)
    response = post(payload())
    assert response.status_code == 200
    row = saved(response.json()["ingest_id"])
    assert row.process_status == "stored" and row.einsatz_id is None
    assert "Alarm-Verarbeitung fehlgeschlagen" in caplog.text and calls == []


@pytest.mark.parametrize("charset,raw,expected", [
    ("cp850", b"Alarm \x84", "Alarm ä"),
    (None, b"Alarm \xff", "Alarm \ufffd"),
    ("unbekannt", b"Alarm \xff", "Alarm \ufffd"),
])
def test_bytes_without_text_are_decoded(alarm_api, charset, raw, expected):
    post, _, _ = alarm_api
    response = post(payload(raw_text=None, charset=charset,
                            raw_bytes_b64=base64.b64encode(raw).decode()))
    assert response.status_code == 200
    row = saved(response.json()["ingest_id"])
    assert row.raw_text == expected and row.raw_bytes == raw


def test_invalid_base64_is_ignored_with_warning_and_old_payload_works(alarm_api, caplog):
    post, _, _ = alarm_api
    data = payload(raw_bytes_b64="%%%")
    response = post(data)
    assert response.status_code == 200
    row = saved(response.json()["ingest_id"])
    assert row.raw_bytes is None and row.process_status == "applied"
    assert "Ungueltige Alarm-Bytes" in caplog.text
    old = post(payload())
    assert old.status_code == 200 and old.json()["process_status"] == "applied"
    assert {"ingest_id", "duplicate", "einsatz_id", "parse_status"} <= old.json().keys()


def test_raw_bytes_are_limited_to_64_kib(alarm_api, caplog):
    post, _, _ = alarm_api
    response = post(payload(raw_bytes_b64=base64.b64encode(b"x" * 70000).decode()))
    assert response.status_code == 200
    assert saved(response.json()["ingest_id"]).raw_bytes == b"x" * 65536
    assert "64 KiB" in caplog.text


def test_request_does_not_repeat_recovery_nachlauf(alarm_api, monkeypatch):
    post, _, calls = alarm_api
    original = serial_alarm_service.process_ingest

    def recovered(db, ingest_id, **kwargs):
        # Recovery hat vor dem Request-Claim bereits verarbeitet und alarmiert.
        # Der Request verliert den Claim und darf keinen Nachlauf mehr starten.
        with SessionLocal() as worker:
            set_tenant_context(worker, None)
            original(worker, ingest_id)
        return original(db, ingest_id, **kwargs)

    monkeypatch.setattr(serial_alarm_service, "process_ingest", recovered)
    response = post(payload())
    assert response.status_code == 200
    assert response.json()["process_status"] == "applied"
    assert calls == []
