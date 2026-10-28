"""Regression tests for the persistent incident alarm outbox."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.master import FireDept, OrgSettings
from app.models.sms import SmsLog, SmsLogRecipient
from app.models.teams_bot import TeamsAlarmConfig
from app.models.user import AuditLog
from app.services.alarm_outbox import (
    _finish_if_done,
    _send_mail,
    enqueue_incident_alarm,
    process_incident_alarm,
    run_alarm_outbox_once,
)
from app.services.einsatzinfo_mail_service import EinsatzinfoMailResult
from app.services.sms_dispatch_service import EinsatzinfoDispatchResult
from app.services.teams_alarm_service import TeamsDispatchResult

pytestmark = pytest.mark.usefixtures("sms_outbox_state")


@pytest.fixture(autouse=True)
def _offene_jobs_anderer_tests_entfernen(setup_db):
    # Die Test-DB lebt die ganze Session; offene Jobs aus anderen Testdateien
    # (z. B. Alarm-Pipeline) wuerde run_alarm_outbox_once sonst mitverarbeiten.
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for job in db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.status.in_(("pending", "sending", "retry"))
        ).all():
            db.delete(job)
        db.commit()
    yield


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


def _set_ausschlussbegriffe(db, org_id, begriffe):
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if settings is None:
        settings = OrgSettings(org_id=org_id)
        db.add(settings)
    settings.alarm_ausschluss_begriffe = begriffe
    db.commit()


def _aufraeumen(db, incident):
    # Die Test-DB lebt die ganze Session: Begriffe zuruecksetzen und offene Jobs
    # entfernen, sonst verarbeitet run_alarm_outbox_once sie in spaeteren Tests mit.
    db.rollback()
    for settings in db.query(OrgSettings).filter(OrgSettings.alarm_ausschluss_begriffe.isnot(None)):
        settings.alarm_ausschluss_begriffe = None
    for job in db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident.id):
        db.delete(job)
    db.commit()
    db.close()


def _channels(monkeypatch, calls):
    async def sms(*args, **kwargs):
        calls.append("sms")
        return EinsatzinfoDispatchResult(True, recipient_count=1, handed_off=True)

    async def push(*args, **kwargs):
        calls.append("push")

    async def teams(*args, **kwargs):
        calls.append("teams")
        return TeamsDispatchResult(True)

    async def mail(*args, **kwargs):
        calls.append("mail")
        return EinsatzinfoMailResult(True, recipient_count=1)

    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.services.sms_dispatch_service.dispatch_einsatzinfo", sms)
    monkeypatch.setattr("app.services.incident_notify._send_incident_push", push)
    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", teams)
    monkeypatch.setattr("app.services.einsatzinfo_mail_service.dispatch_einsatzinfo_mail", mail)


def test_enqueue_is_idempotent(setup_db):
    db, incident = _db_incident()
    try:
        _enqueue(db, incident)
        _enqueue(db, incident)
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).all()
        assert {job.channel for job in jobs} == {"sms", "push", "teams", "mail"}
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
        assert len(jobs) == 4 and {job.status for job in jobs} == {"suppressed"}
        log = db.query(IncidentLog).filter_by(incident_id=incident.id).one()
        assert log.text == "Keine Alarmierung (SMS/Push/Teams/Mail): Stichwort F30"
        assert {job.context["suppressed_reason"] for job in jobs} == {"Stichwort F30"}
        assert calls == []
    finally:
        db.close()


def test_ausschlussbegriff_in_meldung_unterdrueckt_alle_kanaele(setup_db):
    db, incident = _db_incident()
    incident.report_text = "Strassenreinigung - Bereits   erledigt"
    db.commit()
    try:
        _set_ausschlussbegriffe(db, 1, "bereits erledigt")
        _enqueue(db, incident)
        jobs = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id).all()
        assert len(jobs) == 4
        assert {job.status for job in jobs} == {"suppressed"}
        assert {job.context["suppressed_reason"] for job in jobs} == {
            'Ausschlussbegriff "bereits erledigt"'
        }
        log = db.query(IncidentLog).filter_by(incident_id=incident.id).one()
        assert "Ausschlussbegriff" in log.text
    finally:
        _aufraeumen(db, incident)


def test_ausschlussbegriff_im_grund_unterdrueckt_alarm(setup_db):
    db, incident = _db_incident()
    incident.reason = "Einsatz bereits erledigt"
    db.commit()
    try:
        _set_ausschlussbegriffe(db, 1, "BEREITS ERLEDIGT")
        _enqueue(db, incident)
        assert {job.status for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id)} == {"suppressed"}
    finally:
        _aufraeumen(db, incident)


def test_ausschlussbegriff_ohne_treffer_bleibt_pending(setup_db):
    db, incident = _db_incident()
    incident.report_text = "Rauchentwicklung im Keller"
    db.commit()
    try:
        _set_ausschlussbegriffe(db, 1, "bereits erledigt")
        _enqueue(db, incident)
        assert {job.status for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id)} == {"pending"}
    finally:
        _aufraeumen(db, incident)


def test_ausschlussbegriff_anderer_org_wird_nicht_beruecksichtigt(setup_db):
    db, incident = _db_incident()
    incident.report_text = "Bereits erledigt"
    try:
        _set_ausschlussbegriffe(db, 2, "bereits erledigt")
        _enqueue(db, incident)
        assert {job.status for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id)} == {"pending"}
    finally:
        _aufraeumen(db, incident)


def test_alarm_backfill_bei_ausschlussbegriff_nicht_noetig(setup_db):
    from app.services.incident_notify import incident_needs_alarm_backfill

    db, incident = _db_incident()
    incident.report_text = "Bereits erledigt"
    db.commit()
    try:
        _set_ausschlussbegriffe(db, 1, "bereits erledigt")
        assert incident_needs_alarm_backfill(db, incident) is False
    finally:
        _aufraeumen(db, incident)


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
        assert sorted(calls) == ["mail", "push", "sms", "teams"]
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
        jobs["mail"].status = "suppressed"
        jobs["mail"].finished_at = now
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
        assert sorted(calls) == ["mail", "push", "sms", "teams"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_teams_retry_then_success(setup_db, monkeypatch):
    db, incident = _db_incident()
    calls = []
    _channels(monkeypatch, calls)
    results = [TeamsDispatchResult(False, "Connect-Fehler", retryable=True), TeamsDispatchResult(True)]

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
@pytest.mark.parametrize("legacy_provider,expected", [("sendet", "uncertain"), ("ausstehend", "sent")])
@pytest.mark.parametrize("age_minutes", [2, 62])
async def test_sms_idempotency_and_stale_recipient_job_id(
    setup_db, monkeypatch, legacy_provider, expected, age_minutes
):
    """Nur nachweislich wartende Alt-Empfaenger automatisch versenden."""
    from app.services import sms_dispatch_service as svc
    from app.services.sms_service import SmsContext

    db, incident = _db_incident()
    now = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=age_minutes)
    log = SmsLog(
        org_id=1, sent_at=now, source="alarm", alarm_type_code="B2", text="Text",
        recipient_count=1, incident_id=incident.id,
    )
    db.add(log)
    db.flush()
    recipient = SmsLogRecipient(
        sms_log_id=log.id, phone_number="+43660123456", success=False, sent_at=now,
        provider=legacy_provider, lease_until=now - timedelta(seconds=1),
    )
    db.add(recipient)
    db.commit()
    from app.services import sms_outbox
    from tests.gateway_helpers import CaptureSocket, bind, cleanup
    c, socket = bind(socket=CaptureSocket(reply=True))
    monkeypatch.setattr("app.services.sms_service.resolve_sms_config", lambda *args: SmsContext(1, ["gateway"], None))
    try:
        assert await svc.retry_pending_einsatzinfo(1) == 0  # Queue-Handoff ist kein Versandzaehler.
        db.expire_all()
        if legacy_provider == "ausstehend" and age_minutes > 60:
            assert recipient.provider == "verworfen" and not recipient.dispatch_job_id and not socket.sent
            return
        assert recipient.dispatch_job_id
        state = sms_outbox.snapshot(1, recipient.dispatch_job_id)
        assert state["status"] == expected
        if expected == "sent":
            assert socket.sent[0]["job_id"] == recipient.dispatch_job_id
        else:
            assert not socket.sent
            assert recipient.dispatch_status == recipient.provider == "uncertain"
            assert log.completed_at is not None
            await svc.retry_pending_einsatzinfo(1)
            assert not socket.sent
        assert db.query(SmsLog).filter_by(source="alarm", incident_id=incident.id).count() == 1
    finally:
        cleanup(c)
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


def test_enqueue_zweimal_ohne_flush_legt_keine_duplikate_an(setup_db):
    """Vorfall 2026-10-06: lis_sync ruft enqueue_incident_alarm und danach
    notify_incident_created (das erneut enqueued) in derselben Transaktion auf.
    Wegen autoflush=False sah der zweite Aufruf die Jobs des ersten nicht, der
    Commit scheiterte an uq_incident_alarm_job_incident_channel und die
    LIS-Neuanlage wurde bei jedem Poll zurueckgerollt."""
    db, incident = _db_incident()
    try:
        enqueue_incident_alarm(db, incident, org_id=1, source="lis", base_url="https://example.test")
        enqueue_incident_alarm(db, incident, org_id=1, source="lis", base_url="https://example.test")
        db.commit()
        channels = sorted(
            channel for (channel,) in db.query(IncidentAlarmJob.channel).filter(
                IncidentAlarmJob.incident_id == incident.id
            ).all()
        )
        assert channels == ["mail", "push", "sms", "teams"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_send_mail_maps_dispatch_results(setup_db, monkeypatch):
    db, incident = _db_incident()
    try:
        _enqueue(db, incident)
        job = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id, channel="mail").one()

        async def sent(*args, **kwargs):
            return EinsatzinfoMailResult(True, recipient_count=12)

        monkeypatch.setattr("app.services.einsatzinfo_mail_service.dispatch_einsatzinfo_mail", sent)
        assert await _send_mail(db, incident, job) == ("sent", "gesendet an 12")

        async def suppressed(*args, **kwargs):
            return EinsatzinfoMailResult(False, "deaktiviert")

        monkeypatch.setattr("app.services.einsatzinfo_mail_service.dispatch_einsatzinfo_mail", suppressed)
        assert await _send_mail(db, incident, job) == ("suppressed", "deaktiviert")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_mail_dispatch_error_retries_without_skip_log(setup_db, monkeypatch):
    db, incident = _db_incident()
    try:
        _enqueue(db, incident)

        async def failing(*args, **kwargs):
            raise RuntimeError("mail kaputt")

        monkeypatch.setattr("app.services.einsatzinfo_mail_service.dispatch_einsatzinfo_mail", failing)
        await process_incident_alarm(incident.id)
        db.expire_all()
        job = db.query(IncidentAlarmJob).filter_by(incident_id=incident.id, channel="mail").one()
        assert job.status == "retry" and job.next_attempt_at is not None
        assert not db.query(IncidentLog).filter(
            IncidentLog.incident_id == incident.id,
            IncidentLog.text.like("Alarmierung Mail uebersprungen:%"),
        ).count()
    finally:
        db.close()


def test_finish_if_done_accepts_legacy_three_terminal_jobs(setup_db):
    db, incident = _db_incident()
    try:
        _enqueue(db, incident)
        db.query(IncidentAlarmJob).filter_by(incident_id=incident.id, channel="mail").delete()
        for job in db.query(IncidentAlarmJob).filter_by(incident_id=incident.id):
            job.status = "suppressed"
        db.commit()
        _finish_if_done(db, incident, 1, None)
        assert db.query(AuditLog).filter_by(
            action="incident.alarm_finished", incident_id=incident.id,
        ).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("echter_http_pfad", [False, True])
async def test_teams_unklare_zustellung_wird_nicht_wiederholt(setup_db, monkeypatch, caplog, echter_http_pfad):
    from app.services import alarm_outbox, teams_alarm_service

    db, einsatz = _db_incident()
    versand = teams_alarm_service.post_incident_card
    _channels(monkeypatch, [])
    aufrufe = []
    webhook = "https://example.test/geheimer-webhook"
    grund = "Lesefehler"
    konfiguration = None
    if echter_http_pfad:
        konfiguration = TeamsAlarmConfig(org_id=1, enabled=True, webhook_url_alarm=webhook)
        db.add(konfiguration)
        db.commit()

        class HttpClient:
            async def post(self, url, json=None):
                aufrufe.append(url)
                raise httpx.ReadTimeout(f"Lesefehler {url}")

        grund = "Lesefehler [Webhook]"
        monkeypatch.setattr(teams_alarm_service, "_teams_http_client", lambda: HttpClient())
        monkeypatch.setattr(teams_alarm_service, "post_incident_card", versand)
    else:
        async def unklarer_versand(*args, **kwargs):
            aufrufe.append(einsatz.id)
            return TeamsDispatchResult(False, grund, uncertain=True)

        monkeypatch.setattr(teams_alarm_service, "post_incident_card", unklarer_versand)
    try:
        _enqueue(db, einsatz)
        await process_incident_alarm(einsatz.id)
        db.expire_all()
        job = db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id, channel="teams").one()
        assert job.status == "uncertain"
        assert job.attempt_count == 1
        assert job.finished_at is not None and job.lease_until is None
        assert len(aufrufe) == 1
        protokoll = db.query(IncidentLog).filter_by(incident_id=einsatz.id, level="warning").one()
        assert protokoll.text == (
            "Alarmierung Teams: Zustellung unklar, nicht automatisch wiederholt: " + grund
        )
        assert db.query(AuditLog).filter_by(action="incident.alarm_finished", incident_id=einsatz.id).count() == 1
        assert db.query(IncidentLog).filter_by(incident_id=einsatz.id, text="Alarmierung abgeschlossen").count() == 1
        if echter_http_pfad:
            assert f"Teams-Zustellung unklar, nicht automatisch wiederholt (Einsatz {einsatz.id}," in caplog.text
            assert webhook not in caplog.text

        # Die Zeit liegt auch hinter allen regulaeren Teams-Retry-Fristen.
        spaeter = job.next_attempt_at + timedelta(hours=1)
        monkeypatch.setattr(alarm_outbox, "_now", lambda: spaeter)
        for _ in range(3):
            await process_incident_alarm(einsatz.id)
            await asyncio.gather(*await run_alarm_outbox_once())
        db.expire_all()
        assert job.status == "uncertain" and job.attempt_count == 1
        assert len(aufrufe) == 1
    finally:
        db.rollback()
        if konfiguration is not None:
            db.delete(konfiguration)
            db.commit()
        _aufraeumen(db, einsatz)


def test_teams_protokollfehler_nach_versand_bleibt_gesendet(setup_db, monkeypatch, caplog):
    from app.services import teams_alarm_service
    from app.services.major_incident_service import create_lage, create_site

    db, einsatz = _db_incident()
    aufrufe = []

    async def webhook_versand(*args, **kwargs):
        aufrufe.append(einsatz.id)
        return True, False, None, False

    def protokollfehler(*args, **kwargs):
        raise RuntimeError("Kartenprotokoll fehlgeschlagen")

    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", webhook_versand)
    monkeypatch.setattr(teams_alarm_service, "_record_card_post", protokollfehler)
    try:
        db.add(TeamsAlarmConfig(
            org_id=1, enabled=True, suppress_card_in_major_incident=True,
            webhook_url_alarm="https://example.test/webhook",
        ))
        lage = create_lage(db, 1, "Protokollfehler-Test")
        create_site(db, lage, "Stelle", org_id=1, incident_id=einsatz.id)
        db.flush()
        ergebnis = asyncio.run(teams_alarm_service.post_incident_card(db, einsatz, base_url="https://example.test"))
        assert ergebnis == TeamsDispatchResult(True)
        assert aufrufe == [einsatz.id]
        assert f"Kartenprotokoll fehlgeschlagen (Einsatz {einsatz.id})" in caplog.text
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
async def test_teams_gleiche_leitstellennummer_je_organisation(setup_db, monkeypatch):
    from app.services import teams_alarm_service

    db, einsatz = _db_incident()
    versand = teams_alarm_service.post_incident_card
    _channels(monkeypatch, [])
    monkeypatch.setattr(teams_alarm_service, "post_incident_card", versand)
    aufrufe = []

    class HttpClient:
        async def post(self, url, json=None):
            aufrufe.append(url)
            return httpx.Response(200)

    monkeypatch.setattr(teams_alarm_service, "_teams_http_client", lambda: HttpClient())
    zweite_org = FireDept(slug="teams-zweite-org", name="Zweite Organisation")
    db.add(zweite_org)
    db.flush()
    zweiter_einsatz = Incident(
        primary_org_id=zweite_org.id, alarm_type_code="B2", status="active",
        lis_operation_number="TEAMS-ORG-TEST",
    )
    einsatz.lis_operation_number = zweiter_einsatz.lis_operation_number
    db.add(zweiter_einsatz)
    konfigurationen = [TeamsAlarmConfig(
        org_id=org_id, enabled=True, webhook_url_alarm=f"https://example.test/org/{org_id}",
    ) for org_id in (1, zweite_org.id)]
    db.add_all(konfigurationen)
    db.commit()
    try:
        for vorfall in (einsatz, zweiter_einsatz):
            for _ in range(2):
                enqueue_incident_alarm(
                    db, vorfall, org_id=vorfall.primary_org_id, source="test", base_url="https://example.test",
                )
            db.commit()
            await process_incident_alarm(vorfall.id)
            await process_incident_alarm(vorfall.id)
        db.expire_all()
        einsaetze = db.query(Incident).filter_by(lis_operation_number="TEAMS-ORG-TEST").all()
        assert {vorfall.primary_org_id for vorfall in einsaetze} == {1, zweite_org.id}
        jobs = db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.incident_id.in_([einsatz.id, zweiter_einsatz.id]),
            IncidentAlarmJob.channel == "teams",
        ).all()
        assert len(jobs) == 2
        assert {job.org_id for job in jobs} == {1, zweite_org.id}
        assert all(job.status == "sent" and job.attempt_count == 1 for job in jobs)
        assert sorted(aufrufe) == sorted(konfiguration.webhook_url_alarm for konfiguration in konfigurationen)
    finally:
        db.rollback()
        for konfiguration in konfigurationen:
            db.delete(konfiguration)
        db.query(IncidentAlarmJob).filter_by(incident_id=zweiter_einsatz.id).delete()
        db.commit()
        _aufraeumen(db, einsatz)
