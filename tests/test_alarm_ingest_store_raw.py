"""Rohdatenspeicherung, Wiederaufnahme und Org-Isolation des seriellen Ingest."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, NoResultFound

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident
from app.services import serial_alarm_service as service


@pytest.fixture
def db(setup_db):
    with SessionLocal() as session:
        set_tenant_context(session, None)
        yield session
        session.rollback()


@pytest.fixture
def gateway(db):
    row = Gateway(org_id=1, name="Rohdaten " + uuid4().hex)
    db.add(row)
    db.commit()
    return row


def store(db, gateway, **kwargs):
    values = dict(org_id=gateway.org_id, gateway_id=gateway.id,
                  raw_text="Alarm " + uuid4().hex, parse_status="parse_failed")
    values.update(kwargs)
    return service.store_raw(db, **values)


def test_processing_error_keeps_committed_raw_data(db, gateway, monkeypatch, caplog):
    text = "Fehleralarm " + uuid4().hex

    def fail(session, *args):
        session.add(Incident(primary_org_id=1, reason="Teilweise angelegt"))
        session.flush()
        raise RuntimeError("Verarbeitung kaputt")

    monkeypatch.setattr(service, "_create_or_link_incident", fail)
    row, created = service.ingest_alarm(
        db, org_id=1, gateway_id=gateway.id, raw_text=text,
        charset="cp850", parsed=None, parse_status="parse_failed",
    )
    row_id = row.id
    db.rollback()
    with SessionLocal() as reader:
        set_tenant_context(reader, None)
        saved = reader.query(AlarmIngest).filter(AlarmIngest.org_id == 1, AlarmIngest.id == row_id).one()
        assert saved.raw_text == text
        assert saved.process_status == "error"
        assert saved.process_error == "Verarbeitung kaputt"
        assert saved.process_attempts == 1
        assert saved.einsatz_id is None
        assert reader.query(Incident).filter(
            Incident.primary_org_id == 1, Incident.reason == "Teilweise angelegt"
        ).count() == 0
    assert created
    assert "Verarbeitung kaputt" in caplog.text


def test_crash_after_store_can_resume_and_process_twice(db, gateway):
    row, created = store(db, gateway, raw_bytes=b"Original\x00\xff")
    row_id, text = row.id, row.raw_text
    assert created and row.process_status == "stored"
    assert row.einsatz_id is None
    db.rollback()  # Request-Ende/Absturz: nur bereits committed Daten bleiben.
    with SessionLocal() as recovery:
        set_tenant_context(recovery, None)
        first = service.process_ingest(recovery, row_id)
        incident_id = first.einsatz_id
        second = service.process_ingest(recovery, row_id)
        assert second.einsatz_id == incident_id
        assert second.process_status == "applied"
        assert second.process_attempts == 1
        assert second.raw_bytes == b"Original\x00\xff"
        assert second.applied_at is not None
        assert recovery.query(Incident).filter(
            Incident.primary_org_id == 1, Incident.report_text == text
        ).count() == 1


def test_old_payload_without_new_fields(db, gateway):
    row, created = service.ingest_alarm(
        db, org_id=1, gateway_id=gateway.id, raw_text="Alt " + uuid4().hex,
        charset="cp850", parsed=None, parse_status="parse_failed",
    )
    assert created and row.einsatz_id is not None
    assert row.process_status == "applied"
    assert row.sequence is None and row.raw_bytes is None
    assert row.source == "wut"


def test_sequence_dedup_ignores_changed_text_and_age(db, gateway):
    row, _ = store(db, gateway, sequence=7)
    row.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    db.commit()
    duplicate, created = store(db, gateway, sequence=7, raw_text="Andere Daten")
    assert not created and duplicate.id == row.id
    another, created = store(db, gateway, sequence=8, raw_text=row.raw_text)
    assert created and another.id != row.id


def test_hash_dedup_only_within_ten_minutes(db, gateway):
    row, _ = store(db, gateway)
    same, created = store(db, gateway, raw_text=row.raw_text)
    assert not created and same.id == row.id
    row.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=11)
    db.commit()
    later, created = store(db, gateway, raw_text=row.raw_text)
    assert created and later.id != row.id


def test_cross_org_dedup_and_processing_isolation(db, gateway):
    other = Gateway(org_id=2, name="Andere Org " + uuid4().hex)
    db.add(other)
    db.commit()
    row, _ = store(db, gateway, sequence=1)
    foreign, created = store(db, other, raw_text=row.raw_text, sequence=1)
    assert created and foreign.org_id == 2 and foreign.id != row.id
    # Auch der Fallback ohne Sequenz darf nicht org-uebergreifend deduplizieren.
    own_text = "Org-Hash " + uuid4().hex
    own, _ = store(db, gateway, raw_text=own_text)
    foreign_hash, created = store(db, other, raw_text=own_text)
    assert created and foreign_hash.id != own.id
    set_tenant_context(db, 1)
    with pytest.raises(NoResultFound):
        service.process_ingest(db, foreign.id)
    set_tenant_context(db, None)
    applied = service.process_ingest(db, foreign.id)
    incident = db.query(Incident).filter(
        Incident.primary_org_id == 2, Incident.id == applied.einsatz_id,
    ).one()
    assert incident.report_text == row.raw_text
    db.refresh(row)
    assert row.process_status == "stored"


def test_bytes_limit_and_gateway_time_is_utc(db, gateway):
    received = datetime(2026, 10, 26, 12, tzinfo=timezone(timedelta(hours=2)))
    row, _ = store(db, gateway, raw_bytes=b"x" * 70000, received_at_gateway=received)
    assert len(row.raw_bytes) == 65536
    assert row.received_at_gateway == datetime(2026, 10, 26, 10)  # noqa: DTZ001


def test_parallel_insert_integrity_error_loads_winner(db, gateway, monkeypatch):
    winner, _ = store(db, gateway, sequence=42)
    real_query = db.query
    calls = 0

    def query(*args, **kwargs):
        nonlocal calls
        result = real_query(*args, **kwargs)
        if args == (AlarmIngest,):
            calls += 1
            if calls == 1:
                # Vorabpruefung sieht den parallelen, inzwischen committed Insert noch nicht.
                from unittest.mock import MagicMock
                empty = MagicMock()
                empty.filter.return_value = empty
                empty.execution_options.return_value = empty
                empty.first.return_value = None
                return empty
        return result

    monkeypatch.setattr(db, "query", query)
    duplicate, created = store(db, gateway, sequence=42)
    assert not created and duplicate.id == winner.id
    assert db.is_active


def test_unrelated_integrity_error_is_not_swallowed(db, gateway, monkeypatch):
    def fail():
        raise IntegrityError("insert", {}, RuntimeError("DB-Fehler"))

    monkeypatch.setattr(db, "flush", fail)
    with pytest.raises(IntegrityError):
        store(db, gateway, sequence=99)


def test_error_can_be_retried(db, gateway, monkeypatch):
    row, _ = store(db, gateway)
    original = service._create_or_link_incident

    def fail(*args):
        raise RuntimeError("Retry")

    monkeypatch.setattr(service, "_create_or_link_incident", fail)
    assert service.process_ingest(db, row.id).process_status == "error"
    monkeypatch.setattr(service, "_create_or_link_incident", original)
    applied = service.process_ingest(db, row.id)
    assert applied.process_status == "applied" and applied.einsatz_id is not None
    assert applied.process_error is None and applied.process_attempts == 2


def test_two_workers_process_exactly_one_incident(db, gateway):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    row, _ = store(db, gateway)
    row_id, text = row.id, row.raw_text
    db.rollback()
    barrier = Barrier(2)

    def process():
        with SessionLocal() as worker:
            set_tenant_context(worker, None)
            barrier.wait(timeout=10)
            result = service.process_ingest(worker, row_id)
            return result.einsatz_id

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(process) for _ in range(2)]
        incident_ids = [future.result(timeout=20) for future in futures]
    assert incident_ids[0] is not None and incident_ids[0] == incident_ids[1]
    assert db.query(Incident).filter(
        Incident.primary_org_id == 1, Incident.report_text == text,
    ).count() == 1


def test_database_processing_error_keeps_session_usable(db, gateway, monkeypatch):
    row, _ = store(db, gateway)

    def fail(session, *args):
        session.add(Incident(primary_org_id=1, status=None))
        # Explizit ein echtes NOT-NULL-Problem beim Flush ausloesen.
        bad = AlarmIngest(org_id=1, gateway_id=gateway.id, raw_text=None, raw_hash="bad")
        session.add(bad)
        session.flush()

    monkeypatch.setattr(service, "_create_or_link_incident", fail)
    result = service.process_ingest(db, row.id)
    assert result.process_status == "error" and result.process_attempts == 1
    assert "NOT NULL" in result.process_error
    assert db.is_active


def test_two_parallel_sequence_inserts_return_same_row(db, gateway):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    gateway_id = gateway.id
    barrier = Barrier(2)
    text = "Paralleler Empfang " + uuid4().hex
    db.rollback()

    def insert():
        with SessionLocal() as worker:
            set_tenant_context(worker, None)
            original_flush = worker.flush

            def flush(*args, **kwargs):
                if any(isinstance(row, AlarmIngest) for row in worker.new):
                    # Beide Vorabpruefungen waren leer; jetzt entscheidet UNIQUE.
                    barrier.wait(timeout=10)
                return original_flush(*args, **kwargs)

            worker.flush = flush
            result, created = service.store_raw(
                worker, org_id=1, gateway_id=gateway_id, sequence=101,
                raw_text=text, parse_status="parse_failed",
            )
            return result.id, created

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(insert) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    assert db.query(AlarmIngest).filter(
        AlarmIngest.org_id == 1, AlarmIngest.gateway_id == gateway_id,
        AlarmIngest.sequence == 101,
    ).count() == 1
