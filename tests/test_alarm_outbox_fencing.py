import asyncio
from datetime import timedelta

import pytest

from app.models.incident import IncidentAlarmJob
from app.services import alarm_outbox as alarm
from app.services.sms_outbox import database
from tests.test_alarm_outbox import _aufraeumen, _db_incident


@pytest.mark.parametrize("channel", ["teams", "push", "mail"])
async def test_expired_non_idempotent_claim_is_uncertain_and_late_result_ignored(monkeypatch, channel):
    db, incident = _db_incident()
    started, resume = asyncio.Event(), asyncio.Event()
    calls = []
    async def sender(*args):
        calls.append(1)
        started.set()
        await resume.wait()
        return "sent", None
    monkeypatch.setattr(alarm, f"_send_{channel}", sender)
    job = IncidentAlarmJob(org_id=1, incident_id=incident.id, channel=channel,
                           status="pending", next_attempt_at=alarm._now(), context={})
    db.add(job)
    db.commit()
    task = asyncio.create_task(alarm.process_incident_alarm(incident.id))
    try:
        async with asyncio.timeout(2):
            await started.wait()
        with database() as other:
            other.get(IncidentAlarmJob, job.id).lease_until = alarm._now() - timedelta(seconds=1)
            other.commit()
        await alarm.process_incident_alarm(incident.id)
        with database() as other:
            assert other.get(IncidentAlarmJob, job.id).status == "uncertain"
            from app.models.user import AuditLog
            assert other.query(AuditLog).filter(
                AuditLog.action == "incident.alarm_finished", AuditLog.incident_id == incident.id
            ).count() == 1
        resume.set()
        await task
        with database() as other:
            assert other.get(IncidentAlarmJob, job.id).status == "uncertain"
        assert calls == [1]
    finally:
        resume.set()
        await task
        _aufraeumen(db, incident)


async def test_old_sms_coordinator_cannot_overwrite_new_generation(monkeypatch):
    db, incident = _db_incident()
    started, resume = asyncio.Event(), asyncio.Event()
    calls = []
    async def sender(*args):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await resume.wait()
            return "failed", "old reply"
        return "sent", None
    monkeypatch.setattr(alarm, "_send_sms", sender)
    job = IncidentAlarmJob(org_id=1, incident_id=incident.id, channel="sms", status="pending",
                           next_attempt_at=alarm._now(), context={})
    db.add(job)
    db.commit()
    task = asyncio.create_task(alarm.process_incident_alarm(incident.id))
    try:
        async with asyncio.timeout(2):
            await started.wait()
        with database() as other:
            other.get(IncidentAlarmJob, job.id).lease_until = alarm._now() - timedelta(seconds=1)
            other.commit()
        await alarm.process_incident_alarm(incident.id)
        resume.set()
        await task
        with database() as other:
            final = other.get(IncidentAlarmJob, job.id)
            assert final.status == "sent" and final.attempt_count == 2
    finally:
        resume.set()
        await task
        _aufraeumen(db, incident)


async def test_alarm_loop_drains_child_tasks_before_return(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def child():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    async def once():
        task = asyncio.create_task(child())
        alarm._running_incident_tasks.add(task)
        task.add_done_callback(alarm._running_incident_tasks.discard)
        return [task]
    monkeypatch.setattr(alarm, "run_alarm_outbox_once", once)
    task = asyncio.create_task(alarm.alarm_outbox_loop())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and not alarm._running_incident_tasks
