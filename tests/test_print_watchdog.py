from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
import uuid

import pytest

from app.core.tenant import set_tenant_context
from app.models.gateway import JOB_CANCELED, JOB_DONE, JOB_FAILED, JOB_SENT, PrintJob
from tests.conftest import TestingSession


def _job(status: str, age_minutes: int) -> int:
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        job = PrintJob(
            org_id=1,
            gateway_id=1,
            document_type="einsatzinfo",
            status=status,
            idempotency_key="watchdog-" + uuid.uuid4().hex,
            aktualisiert_am=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=age_minutes),
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _status(job_id: int) -> tuple[str, str | None]:
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        job = db.get(PrintJob, job_id)
        return job.status, job.error
    finally:
        db.close()


def test_watchdog_warns_young_stale_and_escalates_old(monkeypatch, caplog):
    import app.routers.ws as ws
    import app.services.print_watchdog as watchdog

    monkeypatch.setattr(ws, "gateway_online", lambda _org_id: False)
    young = _job(JOB_SENT, 15)
    old = _job(JOB_SENT, 40)
    done = _job(JOB_DONE, 40)
    canceled = _job(JOB_CANCELED, 40)

    with caplog.at_level("WARNING"):
        won = watchdog._pruefe_haengende_jobs()

    assert won == [old]
    assert _status(young) == (JOB_SENT, None)
    assert _status(old)[0] == JOB_FAILED
    assert "40 Minuten" in (_status(old)[1] or "")
    assert _status(done) == (JOB_DONE, None)
    assert _status(canceled) == (JOB_CANCELED, None)
    assert str(young) in caplog.text and str(old) in caplog.text
    assert watchdog._pruefe_haengende_jobs() == []


def test_escalate_stale_job_parallel_exactly_one_winner():
    import app.services.print_dispatcher as dispatcher

    job_id = _job(JOB_SENT, 40)
    barrier = Barrier(2)

    def attempt() -> bool:
        db = TestingSession()
        set_tenant_context(db, None)
        try:
            barrier.wait()
            return dispatcher.escalate_stale_job(db, job_id, "Watchdog")
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: attempt(), range(2)))

    assert sorted(results) == [False, True]
    assert _status(job_id) == (JOB_FAILED, "Watchdog")


@pytest.mark.asyncio
async def test_watchdog_broadcasts_before_fallback(monkeypatch):
    import app.services.broadcast as broadcast
    import app.services.print_dispatcher as dispatcher
    import app.services.print_watchdog as watchdog

    calls = []
    monkeypatch.setattr(watchdog, "_pruefe_haengende_jobs", lambda: [123])
    monkeypatch.setattr(watchdog, "_eskalations_context", lambda _ids: [(123, 7, 9)])

    async def fake_broadcast(org_id, event):
        calls.append(("broadcast", org_id, event))

    async def fake_fallback(job_id):
        calls.append(("fallback", job_id))

    monkeypatch.setattr(broadcast, "broadcast_org", fake_broadcast)
    monkeypatch.setattr(dispatcher, "dispatch_fallback_for_failed_job", fake_fallback)

    await watchdog._watchdog_iteration()

    assert calls == [
        ("broadcast", 7, {
            "type": "print_job_status",
            "job_id": 123,
            "status": "failed",
            "gateway_id": 9,
        }),
        ("fallback", 123),
    ]
