"""Regression tests for the persistent incident alarm outbox."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.sms import SmsLog, SmsLogRecipient
from app.models.user import AuditLog
from app.services.alarm_outbox import (
    enqueue_incident_alarm,
    process_incident_alarm,
    run_alarm_outbox_once,
)
from app.services.sms_dispatch_service import EinsatzinfoDispatchResult
from app.services.teams_alarm_service import TeamsDispatchResult


def _db_incident(*, code="B2", exercise=False):
    db = SessionLocal()
    set_tenant_context(db, None)
    incident = Incident(
        alarm_type_code=code, address_city="Testort", is_exercise=exercise,
        primary_org_id=1, status="active",
    )
    db.add(incident)
    db.commit()
    return db, incident


def _enqueue(db, incident):
    enqueue_incident_alarm(db, incident, org_id=1, source="test", base_url="https://example.test")
    db.commit()


def _channels(monkeypatch, calls):
    async def sms(*args, **kwargs):
        calls.append("sms")
        return EinsatzinfoDispatchResult(True, recipient_count=1, handed_off=True)

    async def push(*args, **kwargs):
        calls.append("push")

    async def teams(*args, **kwargs):
        calls.append("teams")
        return TeamsDispatchResult(True)

    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.services.sms_dispatch_service.dispatch_einsatzinfo", sms)
    monkeypatch.setattr("app.services.incident_notify._send_incident_push", push)
    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", teams)


def test_enqueue_is_idempotent(setup_db):
    db, incident = _db_incident()
    try:
        _enqueue(db, incident)
        _enqueue(db, incident)
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).all()
        assert {job.channel for job in jobs} == {"sms", "push", "teams"}
        assert {job.status for job in jobs} == {"pending"}
        for job in jobs:
            job.status = "suppressed"
        db.commit()
    finally:
        db.close()


def test_incident_alarm_started_accepts_outbox_and_legacy_audit(setup_db):
    from app.core.audit import write_audit
    from app.services.incident_notify import incident_alarm_started

    db, incident = _db_incident()
    try:
        assert incident_alarm_started(db, incident.id) is False
        _enqueue(db, incident)
        assert incident_alarm_started(db, incident.id) is True
        db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).delete()
        write_audit(db, "incident.alarm_started", incident_id=incident.id)
        db.commit()
        assert incident_alarm_started(db, incident.id) is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_f30_is_persistently_suppressed(setup_db, monkeypatch):
    db, incident = _db_incident(code="F30")
    calls = []
    _channels(monkeypatch, calls)
    try:
        _enqueue(db, incident)
        await asyncio.gather(*await run_alarm_outbox_once())
        db.expire_all()
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).all()
        assert len(jobs) == 3 and {job.status for job in jobs} == {"suppressed"}
        log = db.query(IncidentLog).filter_by(incident_id=incident.id).one()
        assert log.text == "F30 – keine Alarmierung (SMS/Push/Teams)"
        assert calls == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_committed_jobs_are_processed_once_after_crash(setup_db, monkeypatch):
    db, incident = _db_incident()
    calls = []
    _channels(monkeypatch, calls)
    try:
        _enqueue(db, incident)
        await asyncio.gather(*await run_alarm_outbox_once())
        await asyncio.gather(*await run_alarm_outbox_once())
        db.expire_all()
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).all()
        assert sorted(calls) == ["push", "sms", "teams"]
        assert all(job.status in {"sent", "suppressed", "failed"} for job in jobs)
        assert db.query(AuditLog).filter_by(action="incident.alarm_finished", incident_id=incident.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stale_sending_job_is_claimed_but_valid_lease_is_not(setup_db, monkeypatch):
    db, incident = _db_incident()
    calls = []
    _channels(monkeypatch, calls)
    try:
        _enqueue(db, incident)
        jobs = {job.channel: job for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id)}
        now = datetime.now(UTC).replace(tzinfo=None)
        jobs["sms"].status = "sending"
        jobs["sms"].lease_until = now - timedelta(seconds=1)
        jobs["push"].status = "sending"
        jobs["push"].lease_until = now + timedelta(minutes=1)
        jobs["teams"].status = "suppressed"
        jobs["teams"].finished_at = now
        db.commit()
        await asyncio.gather(*await run_alarm_outbox_once())
        assert calls == ["sms"]
        db.expire_all()
        assert db.get(IncidentAlarmJob, jobs["push"].id).status == "sending"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_concurrent_claim_sends_each_channel_once(setup_db, monkeypatch):
    db, incident = _db_incident()
    calls = []
    _channels(monkeypatch, calls)
    try:
        _enqueue(db, incident)
        await asyncio.gather(process_incident_alarm(incident.id), process_incident_alarm(incident.id))
        assert sorted(calls) == ["push", "sms", "teams"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_teams_retry_then_success(setup_db, monkeypatch):
    db, incident = _db_incident()
    calls = []
    _channels(monkeypatch, calls)
    results = [TeamsDispatchResult(False, "Timeout", retryable=True), TeamsDispatchResult(True)]

    async def teams(*args, **kwargs):
        return results.pop(0)

    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", teams)
    try:
        _enqueue(db, incident)
        await process_incident_alarm(incident.id)
        db.expire_all()
        job = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id, channel="teams").one()
        assert job.status == "retry" and job.attempt_count == 1
        delay = job.next_attempt_at - datetime.now(UTC).replace(tzinfo=None)
        assert timedelta(seconds=3) < delay < timedelta(seconds=7)
        job.next_attempt_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
        await process_incident_alarm(incident.id)
        db.expire_all()
        assert db.get(IncidentAlarmJob, job.id).status == "sent"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_teams_non_retryable_4xx_fails(setup_db, monkeypatch):
    db, incident = _db_incident()
    _channels(monkeypatch, [])

    async def teams(*args, **kwargs):
        return TeamsDispatchResult(False, "HTTP 400", retryable=False)

    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", teams)
    try:
        _enqueue(db, incident)
        await process_incident_alarm(incident.id)
        db.expire_all()
        assert db.query(IncidentAlarmJob).filter_by(incident_id=incident.id, channel="teams").one().status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_exercise_suppresses_channels_and_wakes_push(setup_db, monkeypatch):
    db, incident = _db_incident(exercise=True)
    wake_calls = []

    async def wake(*args, **kwargs):
        wake_calls.append(1)

    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.services.incident_notify._send_incident_wake_only", wake)
    try:
        _enqueue(db, incident)
        await process_incident_alarm(incident.id)
        db.expire_all()
        assert {job.status for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id)} == {"suppressed"}
        assert wake_calls == [1]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_sms_idempotency_and_stale_recipient_job_id(setup_db, monkeypatch):
    """The dispatch boundary owns both SmsLog idempotency and stable retry IDs."""
    from app.services import sms_dispatch_service as svc
    from app.services.sms_service import SmsContext

    db, incident = _db_incident()
    now = datetime.now(UTC).replace(tzinfo=None)
    log = SmsLog(
        org_id=1, sent_at=now, source="alarm", alarm_type_code="B2", text="Text",
        recipient_count=1, incident_id=incident.id,
    )
    db.add(log)
    db.flush()
    recipient = SmsLogRecipient(
        sms_log_id=log.id, phone_number="+43660123456", success=False, sent_at=now,
        provider="sendet", lease_until=now - timedelta(seconds=1),
    )
    db.add(recipient)
    db.commit()
    seen = []

    async def send(_org, jobs, ctx=None, on_result=None):
        seen.extend(jobs)
        result = svc.SmsSendResult(jobs[0][0], True, datetime.now(UTC).replace(tzinfo=None), "gateway")
        await on_result(result)
        return [result]

    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *args: True)
    monkeypatch.setattr("app.services.sms_service.resolve_sms_config", lambda *args: SmsContext(1, ["gateway"], None))
    monkeypatch.setattr(svc, "send_bulk_detailed", send)
    try:
        assert await svc.retry_pending_einsatzinfo(1) == 1
        assert seen[0][2] == f"alarm-{recipient.id}"
        assert db.query(SmsLog).filter_by(source="alarm", incident_id=incident.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dispatch_einsatzinfo_is_idempotent_per_incident(setup_db, monkeypatch):
    from types import SimpleNamespace

    from app.models.master import OrgSettings
    from app.services import sms_dispatch_service as svc
    from app.services.sms_service import SmsContext

    db, incident = _db_incident()
    settings = db.query(OrgSettings).filter_by(org_id=1).first()
    if settings is None:
        settings = OrgSettings(org_id=1)
        db.add(settings)
    settings.einsatzinfo_sms_enabled = True
    db.commit()
    sends = []

    async def send(_org, jobs, ctx=None, on_result=None):
        sends.append(jobs)
        result = svc.SmsSendResult(jobs[0][0], True, datetime.now(UTC).replace(tzinfo=None), "gateway")
        await on_result(result)
        return [result]

    member = SimpleNamespace(id=1, full_name="Test", active=True, phone="+43660123456")
    monkeypatch.setattr(svc, "collect_einsatzinfo_recipients", lambda *args: {"+43660123456": member})
    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *args: True)
    monkeypatch.setattr("app.services.sms_service.resolve_sms_config", lambda *args: SmsContext(1, ["gateway"], None))
    monkeypatch.setattr(svc, "send_bulk_detailed", send)
    try:
        first = await svc.dispatch_einsatzinfo(1, "B2", "Test", "Test", None, None, False, incident_id=incident.id)
        second = await svc.dispatch_einsatzinfo(1, "B2", "Test", "Test", None, None, False, incident_id=incident.id)
        assert first.handed_off and second.handed_off
        assert len(sends) == 1
        assert db.query(SmsLog).filter_by(source="alarm", incident_id=incident.id).count() == 1
    finally:
        db.close()


def test_sms_lease_scales_with_bulk_size():
    from app.services.sms_dispatch_service import _einsatzinfo_lease_until

    now = datetime.now(UTC).replace(tzinfo=None)
    assert _einsatzinfo_lease_until(now, 10) >= now + timedelta(minutes=3, seconds=200)
