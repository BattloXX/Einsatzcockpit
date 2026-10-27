"""P4c: transaktionale Nachalarmierung mit eigenen Versandidentitaeten."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.master import OrgSettings
from app.models.sms import SmsLog, SmsLogRecipient
from app.services import alarm_outbox as outbox
from app.services import sms_dispatch_service as sms
from app.services.sms_service import SmsContext
from tests import test_alarm_apply_service as apply_tests
from tests import test_alarm_merge as merge_tests
from tests.test_alarm_outbox import _db_incident

# Dieselbe Isolation offener Jobs wie in test_alarm_outbox; auch Merge-Tests
# duerfen keine wartenden Jobs fuer spaetere Worker-Tests hinterlassen.
db = apply_tests.db
merge_katalog = merge_tests.merge_katalog
pytestmark = pytest.mark.usefixtures("sms_outbox_state")


@pytest.fixture(autouse=True)
def offene_jobs(setup_db):
    def cleanup():
        with SessionLocal() as session:
            set_tenant_context(session, None)
            for job in session.query(IncidentAlarmJob).filter(
                IncidentAlarmJob.status.in_(("pending", "sending", "retry")),
            ).all():
                session.delete(job)
            session.commit()
    cleanup()
    yield
    cleanup()


def enqueue(db, inc, codes, namespace="code"):
    return outbox.enqueue_nachalarmierung(
        db, inc, org_id=inc.primary_org_id, key=f"{namespace}:{'+'.join(sorted(codes))}",
        codes=codes, source="test", base_url="https://example.test", push_url=None,
    )


def jobs(db, inc):
    return db.query(IncidentAlarmJob).filter_by(incident_id=inc.id, org_id=inc.primary_org_id).all()


def test_neue_codes_ein_satz_idempotent_und_erstalarm_unberuehrt():
    db, inc = _db_incident()
    try:
        outbox.enqueue_incident_alarm(db, inc, org_id=1, source="test")
        assert enqueue(db, inc, ["r2", "_drehleiter"])
        assert not enqueue(db, inc, ["r2"])
        assert not enqueue(db, inc, ["_drehleiter", "r2"])
        assert enqueue(db, inc, ["r2", "r20"])
        outbox.enqueue_incident_alarm(db, inc, org_id=1, source="test")
        db.commit()
        rows = jobs(db, inc)
        assert len(rows) == 12
        assert sum(row.variant == "" for row in rows) == 4
        combined = [row for row in rows if row.variant == "nach:code:_drehleiter+r2"]
        assert {row.channel for row in combined} == set(outbox.CHANNELS)
        assert all(row.context["codes"] == ["_drehleiter", "r2"] for row in combined)
        assert all(row.context["nachalarmierung"] for row in combined)
        assert sum(row.variant == "nach:code:r20" for row in rows) == 4
    finally:
        db.close()


def test_erstalarm_nach_ungeflushtem_nachalarm():
    db, inc = _db_incident()
    try:
        # pending-Objekte muessen ebenso wie persistierte Varianten beachtet werden.
        for channel in outbox.CHANNELS:
            db.add(IncidentAlarmJob(org_id=1, incident_id=inc.id, channel=channel,
                                   variant="nach:code:r2", context={"codes": ["r2"]}))
        assert not enqueue(db, inc, ["r2"])
        outbox.enqueue_incident_alarm(db, inc, org_id=1, source="test")
        db.commit()
        assert len(jobs(db, inc)) == 8
    finally:
        db.close()


def test_merge_codes_stichwort_text_und_closed(db, merge_katalog):
    first = apply_tests.apply(db, apply_tests.nummer() + " f2,r1 > brand")
    inc = apply_tests.incident(db, first)
    number = first.leitstellennummer
    apply_tests.apply(db, number + " f2,r1 > brand > nur Text")
    assert jobs(db, inc) == []
    apply_tests.apply(db, number + " f2,r1,r2 _drehleiter > brand")
    assert len(jobs(db, inc)) == 4
    assert all(row.variant == "nach:code:_drehleiter+r2" for row in jobs(db, inc))
    apply_tests.apply(db, number + " f2,r2 _drehleiter > brand > nochmals")
    assert len(jobs(db, inc)) == 4
    apply_tests.apply(db, number + " f3,r2 _drehleiter > brand")
    assert len(jobs(db, inc)) == 8
    assert sum(row.variant == "nach:stichwort:F3" for row in jobs(db, inc)) == 4
    assert db.query(IncidentLog).filter_by(incident_id=inc.id,
                                         text="Nachalarmierung: Codes r2, _drehleiter").count() == 1
    inc.status = "closed"
    db.commit()
    apply_tests.apply(db, number + " f3,r3 > brand")
    assert len(jobs(db, inc)) == 8
    assert db.query(IncidentLog).filter_by(incident_id=inc.id,
                                         text="Nachalarmierung unterdrueckt: Einsatz abgeschlossen").count() == 1


def test_merge_und_jobs_werden_zusammen_zurueckgerollt(db, merge_katalog):
    first = apply_tests.apply(db, apply_tests.nummer() + " f2 > brand")
    inc = apply_tests.incident(db, first)
    row = apply_tests.store(db, first.leitstellennummer + " f2,r2 > brand")
    from app.services.alarm_apply_service import wende_an
    wende_an(db, row, first.org_id)
    assert len(jobs(db, inc)) == 4
    db.rollback()
    assert jobs(db, inc) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exercise", [False, True])
async def test_ausschluss_und_uebung_suppressed(exercise, monkeypatch):
    db, inc = _db_incident(exercise=exercise)
    cfg = db.query(OrgSettings).filter_by(org_id=1).first()
    if cfg is None:
        cfg = OrgSettings(org_id=1)
        db.add(cfg)
    old = cfg.alarm_ausschluss_begriffe
    try:
        if not exercise:
            inc.reason = "Probealarm"
            cfg.alarm_ausschluss_begriffe = "Probealarm"
            db.commit()
        assert enqueue(db, inc, ["r2"])
        db.commit()
        if exercise:
            monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: False)
            async def wake(*a, **kw):
                pass
            monkeypatch.setattr("app.services.incident_notify._send_incident_wake_only", wake)
            await outbox.process_incident_alarm(inc.id)
        db.expire_all()
        assert all(row.status == "suppressed" for row in jobs(db, inc))
        assert not db.query(IncidentLog).filter(IncidentLog.incident_id == inc.id,
                                               IncidentLog.text.like("Keine Alarmierung%")).count()
    finally:
        cfg.alarm_ausschluss_begriffe = old
        db.commit()
        db.close()


@pytest.mark.asyncio
async def test_sms_nachalarm_echter_dispatch_eigene_jobs_und_recovery(monkeypatch):
    db, inc = _db_incident()
    cfg = db.query(OrgSettings).filter_by(org_id=1).first()
    if cfg is None:
        cfg = OrgSettings(org_id=1)
        db.add(cfg)
    old = bool(cfg.einsatzinfo_sms_enabled)
    sends = []
    cfg.einsatzinfo_sms_enabled = True
    db.commit()
    member = SimpleNamespace(id=1, full_name="Test", active=True, phone="+43660123456")
    monkeypatch.setattr(sms, "collect_einsatzinfo_recipients", lambda *a: {member.phone: member})
    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *a: True)
    monkeypatch.setattr("app.services.sms_service.resolve_sms_config", lambda *a: SmsContext(1, ["gateway"], None))
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: True)
    from tests.gateway_helpers import CaptureSocket, bind, cleanup
    connection, socket = bind(socket=CaptureSocket(reply=True))
    real_send = sms.send_bulk_detailed
    async def send(_org, rows, ctx=None, on_result=None):
        sends.append(rows)
        return await real_send(_org, rows, ctx=ctx, on_result=on_result)
    monkeypatch.setattr(sms, "send_bulk_detailed", send)
    try:
        outbox.enqueue_incident_alarm(db, inc, org_id=1, source="test")
        enqueue(db, inc, ["r2"])
        db.commit()
        first = next(row for row in jobs(db, inc) if row.channel == "sms" and not row.variant)
        nach = next(row for row in jobs(db, inc) if row.channel == "sms" and row.variant)
        assert (await outbox._send_sms(db, inc, first))[0] == "sent"
        assert (await outbox._send_sms(db, inc, nach))[0] == "sent"
        nach.status = "sending"
        nach.lease_until = outbox._now() - timedelta(minutes=1)
        db.commit()
        assert outbox._claim(db, nach, outbox._now())
        assert (await outbox._send_sms(db, inc, nach))[0] == "sent"
        assert len(socket.sent) == 2
        assert len(sends) == 2
        assert not sends[0][0][1].startswith("NACHALARMIERUNG")
        assert sends[1][0][1].startswith("NACHALARMIERUNG r2 ")
        assert "nach:code:r2" in sends[1][0][2]
        logs = db.query(SmsLog).filter_by(org_id=1, incident_id=inc.id).all()
        assert {log.variant for log in logs} == {"", "nach:code:r2"}
        ids = {row.dispatch_job_id for row in db.query(SmsLogRecipient).filter(
            SmsLogRecipient.sms_log_id.in_([log.id for log in logs]))}
        assert len(ids) == 2
    finally:
        cleanup(connection)
        cfg.einsatzinfo_sms_enabled = old
        db.commit()
        db.close()


def test_paralleler_doppelaufruf():
    db, inc = _db_incident()
    incident_id = inc.id
    db.close()
    barrier = Barrier(2)
    def worker():
        with SessionLocal() as session:
            set_tenant_context(session, None)
            incident = session.get(Incident, incident_id)
            barrier.wait(timeout=10)
            enqueue(session, incident, ["r2"])
            session.add(IncidentLog(incident_id=incident_id, author_name="Test", text="Commit bleibt moeglich"))
            session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))
    with SessionLocal() as session:
        set_tenant_context(session, None)
        rows = session.query(IncidentAlarmJob).filter_by(incident_id=incident_id, org_id=1).all()
        assert len(rows) == 4
        assert {row.channel for row in rows} == set(outbox.CHANNELS)
        assert session.query(IncidentLog).filter_by(incident_id=incident_id,
                                                  text="Commit bleibt moeglich").count() == 2


def test_integrity_error_bleibt_im_savepoint(monkeypatch):
    db, inc = _db_incident()
    original = db.flush
    calls = 0
    def flush(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            # Echte UNIQUE-Verletzung innerhalb des Savepoints erzwingen.
            db.execute(IncidentAlarmJob.__table__.insert().values(
                org_id=1, incident_id=inc.id, channel="sms", variant="nach:code:r2", context={},
            ))
        return original(*args, **kwargs)
    monkeypatch.setattr(db, "flush", flush)
    try:
        assert enqueue(db, inc, ["r2"])
        db.add(IncidentLog(incident_id=inc.id, author_name="Test", text="ausserer Commit"))
        db.commit()
        assert len(jobs(db, inc)) == 3
        assert db.query(IncidentLog).filter_by(incident_id=inc.id, text="ausserer Commit").one()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_alle_dispatcher_erhalten_praefix(monkeypatch):
    db, inc = _db_incident()
    calls = {}
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: True)
    async def sms_dispatch(*a, **kw):
        calls["sms"] = kw
        return sms.EinsatzinfoDispatchResult(True)
    async def push(*a, **kw):
        calls["push"] = a[2]
    async def teams(*a, **kw):
        calls["teams"] = kw
    async def mail(*a, **kw):
        calls["mail"] = kw
        return SimpleNamespace(sent=True, reason=None, recipient_count=1)
    monkeypatch.setattr(sms, "dispatch_einsatzinfo", sms_dispatch)
    monkeypatch.setattr("app.services.incident_notify._send_incident_push", push)
    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", teams)
    monkeypatch.setattr("app.services.einsatzinfo_mail_service.dispatch_einsatzinfo_mail", mail)
    try:
        enqueue(db, inc, ["r2"])
        db.commit()
        for job in jobs(db, inc):
            await getattr(outbox, f"_send_{job.channel}")(db, inc, job)
        assert calls["push"].startswith("NACHALARMIERUNG r2 ")
        for channel in ("sms", "teams", "mail"):
            assert calls[channel]["text_prefix"] == "NACHALARMIERUNG r2 "
    finally:
        db.close()


@pytest.mark.asyncio
async def test_mail_nachalarm_eigenes_protokoll_und_betreff(db, monkeypatch):
    from app.models.einsatzinfo_mail import MailEinsatzinfoLog, MailEinsatzinfoRecipient
    from app.services import einsatzinfo_mail_service as mail
    org_id = db.info["gateway"].org_id
    db.add(OrgSettings(org_id=org_id, einsatzinfo_mail_enabled=True))
    db.add(MailEinsatzinfoRecipient(org_id=org_id, email="nachalarm@example.test"))
    inc = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active")
    db.add(inc)
    db.commit()
    sent = []
    async def deliver(_db, _org_id, message, _smtp_cfg=None):
        sent.append(message)
    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *a: True)
    monkeypatch.setattr("app.services.mail_service.deliver", deliver)
    assert (await mail.dispatch_einsatzinfo_mail(org_id, inc.id)).sent
    kwargs = {"variant": "nach:code:r2", "text_prefix": "NACHALARMIERUNG r2 "}
    assert (await mail.dispatch_einsatzinfo_mail(org_id, inc.id, **kwargs)).sent
    assert (await mail.dispatch_einsatzinfo_mail(org_id, inc.id, **kwargs)).sent
    assert len(sent) == 2
    assert not sent[0]["Subject"].startswith("NACHALARMIERUNG")
    assert sent[1]["Subject"].startswith("NACHALARMIERUNG r2 ")
    assert db.query(MailEinsatzinfoLog).filter_by(org_id=org_id, incident_id=inc.id).count() == 2


@pytest.mark.asyncio
async def test_teams_nachalarm_im_tatsaechlichen_kartentitel(monkeypatch):
    from app.services import teams_alarm_service as teams
    db, inc = _db_incident()
    payloads = []
    async def post(url, payload, **kw):
        payloads.append(payload)
        return True, False, None
    monkeypatch.setattr(teams, "_post_payload", post)
    cfg = SimpleNamespace(include_map=False, include_gmaps_link=False, include_qr_link=False, include_board_link=False)
    try:
        await teams._post_via_webhook("https://example.test", inc, cfg, base_url="https://example.test", org=None,
                                     text_prefix="NACHALARMIERUNG r2 ")
        assert payloads[0]["attachments"][0]["content"]["body"][0]["text"].startswith("NACHALARMIERUNG r2 ")
    finally:
        db.close()


def test_lange_codesaetze_ueberschreiten_variantenspalte_nicht(setup_db):
    db, incident = _db_incident()
    try:
        codes = ["r2", "r3", "_drehleiter", "_hubrettung", "_kran", "_atemschutz"]
        assert outbox.enqueue_nachalarmierung(
            db, incident, org_id=1, key="code:x", codes=codes, source="test", base_url=None, push_url=None,
        )
        db.commit()
        jobs = db.query(IncidentAlarmJob).filter(
            IncidentAlarmJob.incident_id == incident.id, IncidentAlarmJob.variant != "").all()
        assert jobs and all(len(job.variant) <= 40 for job in jobs)
        # Jeder Code ist einzeln idempotent, auch bei gehashter Variante.
        assert not outbox.enqueue_nachalarmierung(
            db, incident, org_id=1, key="code:x", codes=["r2", "_kran"], source="test", base_url=None, push_url=None,
        )
    finally:
        db.close()
