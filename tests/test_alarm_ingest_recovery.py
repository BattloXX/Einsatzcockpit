"""Recovery-Claims, Nachlauf und ausschliessliche Rohbytes-Retention."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import and_, func

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident
from app.services import alarm_ingest_recovery as recovery
from app.services import alarm_ingest_retention as retention
from app.services import serial_alarm_service as service
from tests.background_loop_helpers import registered_background_loop_names


def now():
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def context(setup_db, monkeypatch):
    calls = []
    finished = []

    async def background(ingest_id):
        finished.append(ingest_id)

    def nachlauf(db, ingest, *, org_id, background_tasks, base_url):
        assert db.info["current_org_id"] is None
        assert ingest.org_id == org_id
        calls.append((ingest.id, org_id, base_url))
        background_tasks.add_task(background, ingest.id)

    monkeypatch.setattr(recovery.alarm_apply_service, "nachlauf", nachlauf)
    monkeypatch.setattr(recovery.settings, "PUBLIC_BASE_URL", "https://alarm.example")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        # Andere Tests lassen absichtlich gespeicherte/fehlerhafte Rohdaten zurueck.
        # Nur die in diesem Test angelegten Ingests duerfen dessen Nachlauf zaehlen.
        letzter = db.query(func.max(AlarmIngest.id)).filter(AlarmIngest.org_id.in_((1, 2))).scalar() or 0
        eligible = recovery._eligible
        monkeypatch.setattr(recovery, "_eligible", lambda now: and_(eligible(now), AlarmIngest.id > letzter))
        yield db, calls, finished
        db.rollback()


def stored(db, *, org_id=1, age=60, **values):
    gateway = Gateway(org_id=org_id, name="Recovery " + uuid4().hex)
    db.add(gateway)
    db.commit()
    row, _ = service.store_raw(
        db, org_id=org_id, gateway_id=gateway.id, raw_text="Alarm " + uuid4().hex,
        raw_bytes=b"original", parsed={"reason": "Recovery"}, parse_status="parsed",
    )
    row.received_at = now() - timedelta(seconds=age)
    for name, value in values.items():
        setattr(row, name, value)
    db.commit()
    return row


def test_crash_after_store_runs_nachlauf_and_tasks_once(context):
    db, calls, finished = context
    row = stored(db)
    row_id, text = row.id, row.raw_text
    db.rollback()
    recovery.run_alarm_ingest_recovery_once()
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert row.process_status == "applied" and row.lease_until is None
    assert row.process_attempts == 1
    assert calls == [(row_id, 1, "https://alarm.example")]
    assert finished == [row_id]
    assert db.query(Incident).filter(
        Incident.primary_org_id == 1, Incident.report_text == text,
    ).count() == 1


@pytest.mark.parametrize("attempts,elapsed,expected", [
    (0, 31, True), (1, 31, True), (2, 31, False), (2, 61, True),
    (3, 121, True), (4, 121, False), (4, 241, True), (5, 999, False),
])
def test_error_retry_backoff_and_limit(context, attempts, elapsed, expected):
    db, calls, _ = context
    row = stored(db, age=1000, process_status="error", process_attempts=attempts,
                 parsed_at=now() - timedelta(seconds=elapsed))
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert (row.process_status == "applied") is expected
    assert row.process_attempts == attempts + int(expected)
    assert len(calls) == int(expected)


@pytest.mark.parametrize("status,age,lease,expected", [
    ("processing", 60, -1, True), ("processing", 300, 60, False),
    ("processing", 121, None, True), ("processing", 60, None, False),
    ("stored", 5, None, False), ("applied", 300, None, False),
])
def test_processing_leases_and_applied_are_respected(context, status, age, lease, expected):
    db, calls, _ = context
    row = stored(db, age=age, process_status=status,
                 lease_until=None if lease is None else now() + timedelta(seconds=lease))
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert len(calls) == int(expected)
    assert row.process_attempts == int(expected)


def test_two_parallel_recoveries_alarm_once(context, monkeypatch):
    db, calls, finished = context
    row = stored(db)
    row_id, text = row.id, row.raw_text
    db.rollback()
    barrier = Barrier(2)
    original = recovery.process_ingest

    def process(*args, **kwargs):
        # Beide Sessions haben denselben Kandidaten vor dem Claim gelesen.
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery, "process_ingest", process)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recovery.run_alarm_ingest_recovery_once) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert calls == [(row_id, 1, "https://alarm.example")]
    assert finished == [row_id]
    assert db.query(Incident).filter(
        Incident.primary_org_id == 1, Incident.report_text == text,
    ).count() == 1


def test_cross_org_uses_ingest_org(context):
    db, calls, _ = context
    own = stored(db, age=1)
    foreign = stored(db, org_id=2)
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(own)
    db.refresh(foreign)
    assert own.process_status == "stored" and own.einsatz_id is None
    assert calls == [(foreign.id, 2, "https://alarm.example")]
    assert db.query(Incident).filter(
        Incident.id == foreign.einsatz_id, Incident.primary_org_id == 2,
    ).one().report_text == foreign.raw_text
    assert db.query(Incident).filter(
        Incident.primary_org_id == 1, Incident.report_text == foreign.raw_text,
    ).count() == 0


def test_retention_only_removes_old_bytes_in_batches(context):
    db, _, _ = context
    old = [stored(db, org_id=org, age=366 * 86400) for org in (1, 1, 2)]
    recent = stored(db, age=364 * 86400)
    saved = [(row.id, row.raw_text, row.parsed) for row in old]
    assert retention.purge_old_alarm_raw_bytes(chunk_size=1) == 3
    for row, (row_id, text, parsed) in zip(old, saved, strict=True):
        db.refresh(row)
        assert row.id == row_id and row.raw_bytes is None
        assert row.raw_text == text and row.parsed == parsed
    db.refresh(recent)
    assert recent.raw_bytes == b"original"
    assert retention.purge_old_alarm_raw_bytes() == 0


def test_retention_zero_disables_cleanup(context, monkeypatch):
    db, _, _ = context
    row = stored(db, age=400 * 86400)
    monkeypatch.setattr(retention.settings, "ALARM_RAW_BYTES_RETENTION_DAYS", 0)
    assert retention.purge_old_alarm_raw_bytes() == 0
    db.refresh(row)
    assert row.raw_bytes == b"original"


def test_loops_registered(monkeypatch):
    names = registered_background_loop_names(monkeypatch)
    assert names.count("alarm_ingest_recovery_loop") == 1
    assert names.count("alarm_ingest_retention_loop") == 1


async def test_recovery_loop_runs_in_thread_and_logs_failure(monkeypatch, caplog):
    started = asyncio.Event()
    logged = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_exception = recovery.logger.exception

    def log(*args, **kwargs):
        original_exception(*args, **kwargs)
        logged.set()

    def fail():
        loop.call_soon_threadsafe(started.set)
        raise RuntimeError("Recovery kaputt")

    monkeypatch.setattr(recovery, "run_alarm_ingest_recovery_once", fail)
    monkeypatch.setattr(recovery.logger, "exception", log)
    task = asyncio.create_task(recovery.alarm_ingest_recovery_loop())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.wait_for(logged.wait(), timeout=5)
        assert "Alarm-Recovery-Iteration fehlgeschlagen" in caplog.text
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_claim_sets_lease_and_failure_releases_it(context, monkeypatch):
    db, calls, _ = context
    row = stored(db)

    def fail(session, org_id, *args):
        current = session.query(service.AlarmIngest).filter(
            service.AlarmIngest.id == row.id, service.AlarmIngest.org_id == org_id,
        ).one()
        assert current.process_status == "processing"
        assert now() + timedelta(seconds=115) < current.lease_until < now() + timedelta(seconds=125)
        raise RuntimeError("Parse fehlgeschlagen")

    monkeypatch.setattr(service, "_create_or_link_incident", fail)
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert row.process_status == "error" and row.lease_until is None
    assert row.process_attempts == 1 and row.parsed_at is not None
    assert row.process_error == "Parse fehlgeschlagen" and calls == []
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert row.process_attempts == 1


def test_parallel_failed_recoveries_respect_retry_delay(context, monkeypatch):
    db, calls, _ = context
    row = stored(db, process_status="error", process_attempts=4,
                 parsed_at=now() - timedelta(minutes=10))
    row_id = row.id
    db.rollback()
    barrier = Barrier(2)
    original = recovery.process_ingest

    def process(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    def fail(*args):
        raise RuntimeError("Immer noch kaputt")

    monkeypatch.setattr(recovery, "process_ingest", process)
    monkeypatch.setattr(service, "_create_or_link_incident", fail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recovery.run_alarm_ingest_recovery_once) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    db.refresh(row)
    assert row.id == row_id and row.process_attempts == 5
    assert row.process_status == "error" and calls == []


def test_merged_ingest_runs_nachlauf_like_router(context):
    db, calls, _ = context
    parsed = {"alarm_type_code": "T1", "street": "Strasse " + uuid4().hex, "city": "Testort"}
    first = stored(db, parsed=parsed)
    original = service.process_ingest(db, first.id)
    second = stored(db, parsed=parsed)
    second.raw_text = original.raw_text
    db.commit()
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(second)
    assert second.process_status == "applied" and second.dedup_action == "merged"
    assert second.einsatz_id == original.einsatz_id and len(calls) == 1
