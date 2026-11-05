"""ASUEW-Outbox gegen die gemeinsame echte Testdatenbank, mit eigenen Mandanten."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingTroop, BreathingWarnung, BreathingZustellung, TroopMember, WARNUNG_KINDS
from app.models.incident import Incident, IncidentChange
from app.models.master import FireDept, OrgSettings
from app.models.user import Role, User, UserRole
from app.services import breathing_notify as bn, push_service
from tests.conftest import TestingSession


@pytest.fixture
def ctx(monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    suffix = uuid4().hex
    org = FireDept(slug=f"notify-{suffix}", name="ASUEW Versand")
    other = FireDept(slug=f"other-{suffix}", name="Andere Org")
    db.add_all([org, other])
    db.flush()
    users = []
    for index in range(5):
        user = User(username=f"notify-{suffix}-{index}", display_name=f"Empfaenger {index}",
                    org_id=other.id if index == 3 else org.id, active=index != 4,
                    phone=f"+43664000000{index}")
        db.add(user)
        users.append(user)
    db.flush()
    supervisor, leader, escalation, foreign, inactive = users
    settings = OrgSettings(org_id=org.id, asuew_eskalation_sms=True,
                           asuew_eskalation_user_ids=str(escalation.id))
    incident = Incident(primary_org_id=org.id, incident_leader_user_id=leader.id,
                        lis_operation_number="LIS-123", external_key="NICHT-VERWENDEN",
                        status="active", is_exercise=False)
    db.add_all([settings, incident])
    db.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    troop = BreathingTroop(incident_id=incident.id, name="Trupp Alpha", status="im_einsatz",
                           ueberwacher_user_id=supervisor.id, entry_at=now - timedelta(minutes=10))
    db.add(troop)
    db.commit()
    monkeypatch.setattr(bn, "SessionLocal", TestingSession)
    push = Mock(return_value=True)
    sms = Mock()

    async def send_sms(*args):
        sms(*args)
        return True

    context = SimpleNamespace(db=db, org=org, other=other, settings=settings, incident=incident,
                              troop=troop, supervisor=supervisor, leader=leader, escalation=escalation,
                              foreign=foreign, inactive=inactive, now=now, push=push, sms=sms,
                              send_sms=send_sms)
    try:
        yield context
    finally:
        db.rollback()
        # Keine offenen Zeilen an nachfolgende globale Outbox-Laeufe vererben.
        for warning in db.query(BreathingWarnung).filter(BreathingWarnung.incident_id == incident.id).all():
            for row in warning.zustellungen:
                if row.status in {"pending", "claimed"}:
                    row.status = "suppressed"
        db.commit()
        db.close()


def sync(c, kinds):
    warnings = bn.sync_warnungen(c.db, c.incident, c.troop, set(kinds), c.now)
    c.db.commit()
    return warnings


def rows(c):
    c.db.expire_all()
    return c.db.query(BreathingZustellung).join(BreathingWarnung).filter(
        BreathingWarnung.incident_id == c.incident.id,
    ).order_by(BreathingZustellung.id).all()


async def deliver(c, now=None, **kwargs):
    return await bn.liefere_faellige(now or c.now, push_sender=kwargs.get("push_sender", c.push),
                                   sms_sender=kwargs.get("sms_sender", c.send_sms), batch=kwargs.get("batch", 50))


@pytest.mark.parametrize("kind,expected", [
    ("one_third_due", [(0, 60, "push", "u"), (1, 180, "push", "l"), (2, 300, "sms", "l")]),
    ("two_third_due", [(0, 60, "push", "u"), (1, 180, "push", "l"), (2, 300, "sms", "l")]),
    ("withdraw", [(0, 0, "push", "u"), (1, 60, "push", "l"), (1, 60, "sms", "l")]),
    ("max_exceeded", [(0, 0, "push", "u"), (1, 180, "push", "l"), (1, 180, "sms", "l")]),
    ("funk_verloren", [(0, 0, "push", "u"), (0, 0, "push", "l"), (0, 0, "sms", "l")]),
    ("notfall", [(0, 0, "push", "u"), (0, 0, "sms", "u"), (0, 0, "push", "l"), (0, 0, "sms", "l")]),
    ("kein_sicherheitstrupp", [(0, 0, "push", "l")]),
    ("assp", [(0, 0, "push", "l")]),
    ("kein_heartbeat", [(0, 0, "push", "l"), (1, 180, "sms", "l")]),
])
def test_plan(ctx, kind, expected):
    c = ctx
    warnings = sync(c, [kind])
    assert len(warnings) == 1
    assert warnings[0].ausgeloest_at == c.now
    groups = {"u": [c.supervisor.id], "l": [c.leader.id, c.escalation.id]}
    assert {(r.stufe, int((r.naechster_versuch_at - c.now).total_seconds()), r.kanal, r.user_id)
            for r in rows(c)} == {(s, d, k, uid) for s, d, k, group in expected for uid in groups[group]}
    assert all(r.status == "pending" and r.empfaenger_label.startswith("Empfaenger") for r in rows(c))
    assert set(bn.ESKALATIONSPLAN) == set(WARNUNG_KINDS)


@pytest.mark.parametrize("minutes,seconds", [(7, 420), (0, 0), (-3, 0), (None, 180)])
def test_grace(ctx, monkeypatch, minutes, seconds):
    if minutes is None:
        original_get = ctx.db.get
        monkeypatch.setattr(ctx.db, "get", lambda model, key: (
            SimpleNamespace(escalation_grace_min=None) if model is FireDept else original_get(model, key)
        ))
    else:
        ctx.org.escalation_grace_min = minutes
        ctx.db.commit()
    sync(ctx, ["max_exceeded"])
    assert all(r.naechster_versuch_at == ctx.now + timedelta(seconds=seconds)
               for r in rows(ctx) if r.stufe == 1)


def test_idempotence_and_new_cycle(ctx):
    c = ctx
    first = sync(c, ["withdraw"])[0].id
    before = [(r.id, r.naechster_versuch_at) for r in rows(c)]
    assert sync(c, ["withdraw"]) == []
    c.now += timedelta(minutes=20)
    assert sync(c, ["withdraw"]) == []
    assert [(r.id, r.naechster_versuch_at) for r in rows(c)] == before
    c.troop.entry_at = c.now
    new = sync(c, ["withdraw"])
    assert len(new) == 1 and new[0].id != first
    assert new[0].zyklus_start == c.now
    assert c.db.get(BreathingWarnung, first).erledigt_at == c.now


@pytest.mark.parametrize("exercise,flag,phone,reason", [
    (True, True, True, "Übung"), (False, False, True, None),
    (False, True, False, "keine Telefonnummer"),
])
def test_sms_suppression(ctx, exercise, flag, phone, reason):
    c = ctx
    c.incident.is_exercise = exercise
    c.settings.asuew_eskalation_sms = flag
    if not phone:
        c.leader.phone = c.escalation.phone = None
    sync(c, ["withdraw"])
    sms = [r for r in rows(c) if r.kanal == "sms"]
    if not flag:
        assert sms == []
    else:
        assert len(sms) == 2
        assert all(r.status == "suppressed" and r.fehler == reason for r in sms)


@pytest.mark.asyncio
async def test_resolved_not_sent_and_close_all_cycles(ctx):
    c = ctx
    sync(c, ["withdraw", "notfall"])
    sent = rows(c)[0]
    sent.status, sent.gesendet_at = "sent", c.now
    c.db.commit()
    sync(c, ["notfall"])
    assert all(r.status in {"suppressed", "sent"} for r in rows(c) if r.warnung.kind == "withdraw")
    bn.schliesse_warnungen_fuer_trupp(c.db, c.troop, c.now)
    c.db.commit()
    assert await deliver(c, c.now + timedelta(hours=1)) == 0
    c.push.assert_not_called()
    c.sms.assert_not_called()
    assert all(w.erledigt_at == c.now for w in c.db.query(BreathingWarnung).filter_by(troop_id=c.troop.id))
    assert c.db.get(BreathingZustellung, sent.id).status == "sent"


def ids(c, group):
    return {u.id for u in bn.empfaenger_aufloesen(c.db, c.incident, c.troop, group)}


def test_recipients_configured_and_fallback(ctx):
    c = ctx
    c.settings.asuew_eskalation_user_ids = f"{c.escalation.id},{c.leader.id},{c.foreign.id},{c.inactive.id},{c.escalation.id}"
    c.db.commit()
    assert ids(c, "leitung") == {c.leader.id, c.escalation.id}
    assert ids(c, "ueberwacher") == {c.supervisor.id}
    c.troop.ueberwacher_user_id = None
    assert ids(c, "ueberwacher") == {c.leader.id}
    c.troop.ueberwacher_user_id = c.foreign.id
    assert ids(c, "ueberwacher") == set()
    c.troop.ueberwacher_user_id = c.inactive.id
    assert ids(c, "ueberwacher") == set()
    c.troop.ueberwacher_user_id = None
    c.incident.incident_leader_user_id = None
    c.settings.asuew_eskalation_user_ids = ""
    role = c.db.query(Role).filter_by(code="breathing_supervisor").one()
    for user in [c.supervisor, c.foreign, c.inactive]:
        c.db.add(UserRole(user_id=user.id, role_id=role.id))
    c.db.commit()
    assert ids(c, "leitung") == {c.supervisor.id}
    assert ids(c, "ueberwacher") == set()
    c.incident.primary_org_id = None
    assert ids(c, "leitung") == set()


@pytest.mark.asyncio
async def test_delivery_due_success_payload_audit(ctx):
    c = ctx
    sync(c, ["one_third_due"])
    assert await deliver(c) == 0
    assert await deliver(c, c.now + timedelta(seconds=60)) == 1
    assert c.push.call_args.args[1:] == (
        c.supervisor.id, "Atemschutz: Kontrolle fällig (1/3)",
        "Trupp Alpha: Kontrolle fällig (1/3) (Einsatz LIS-123)",
        f"/einsatz/{c.incident.id}/atemschutz", True,
        {"incident_id": str(c.incident.id), "asuew": "1", "kind": "one_third_due"},
    )
    delivered = [r for r in rows(c) if r.status == "sent"]
    assert len(delivered) == 1 and delivered[0].gesendet_at == c.now + timedelta(seconds=60)
    changes = c.db.query(IncidentChange).filter_by(incident_id=c.incident.id, action="troop.warnung_zugestellt").all()
    assert len(changes) == 1 and changes[0].user_id is None
    assert changes[0].entity_id == c.troop.id
    assert json.loads(changes[0].after_json) == {"kind": "one_third_due", "stufe": 0, "kanal": "push"}
    assert await deliver(c, c.now + timedelta(seconds=60)) == 0


@pytest.mark.asyncio
async def test_sms_success_and_job_id(ctx):
    c = ctx
    sync(c, ["notfall"])
    assert await deliver(c) == 6
    assert c.push.call_count == 3 and c.sms.call_count == 3
    for r in rows(c):
        assert r.status == "sent"
        if r.kanal == "sms":
            user = c.db.get(User, r.user_id)
            c.sms.assert_any_call(c.org.id, user.phone,
                                 "Trupp Alpha: Atemschutznotfall (Einsatz LIS-123)", f"asuew-{r.id}")


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_retry_backoff_and_terminal_failure(ctx, monkeypatch, raises):
    c = ctx
    sync(c, ["withdraw"])
    for r in rows(c)[1:]:
        r.status = "suppressed"
    c.db.commit()
    failure = Mock(side_effect=RuntimeError("Testfehler")) if raises else Mock(return_value=False)
    logged = Mock()
    monkeypatch.setattr(bn, "log_fehler", logged)
    now = c.now
    for attempt, delay in enumerate([30, 60, 120, None], start=1):
        assert await deliver(c, now, push_sender=failure, batch=1) == 0
        r = rows(c)[0]
        assert r.versuche == attempt and r.lease_bis is None
        assert r.status == ("pending" if delay else "failed")
        if delay:
            assert r.naechster_versuch_at == now + timedelta(seconds=delay)
            now = r.naechster_versuch_at
    assert failure.call_count == 4
    logged.assert_called_once()


@pytest.mark.asyncio
async def test_expired_lease_and_failed_second_claim(ctx):
    c = ctx
    sync(c, ["withdraw"])
    r = rows(c)[0]
    r.status, r.lease_bis = "claimed", c.now + timedelta(seconds=1)
    c.db.commit()
    assert await deliver(c) == 0
    r.lease_bis = c.now - timedelta(seconds=1)
    c.db.commit()
    assert await deliver(c) == 1
    with TestingSession() as second:
        set_tenant_context(second, None)
        assert not bn._claim_once(second, r.id, c.now)
    assert c.push.call_count == 1


@pytest.mark.asyncio
async def test_parallel_workers_do_not_send_twice(ctx):
    c = ctx
    sync(c, ["notfall"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_sms(*args):
        c.sms(*args)
        entered.set()
        await release.wait()
        return True

    first = asyncio.create_task(deliver(c, sms_sender=blocked_sms))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        second = asyncio.create_task(deliver(c))
        result2 = await asyncio.wait_for(second, timeout=5)
    finally:
        release.set()
    result1 = await first
    assert result1 + result2 == 6
    assert c.push.call_count == 3 and c.sms.call_count == 3
    assert all(r.status == "sent" for r in rows(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("cause,reason", [("closed", "erledigt"), ("inactive", "Einsatz nicht aktiv"),
                                        ("user", "Empfaenger nicht aktiv oder fremde Organisation")])
async def test_pre_send_checks(ctx, cause, reason):
    c = ctx
    w = sync(c, ["withdraw"])[0]
    r = rows(c)[0]
    if cause == "closed":
        w.erledigt_at = c.now
    elif cause == "inactive":
        c.incident.status = "closed"
    else:
        c.supervisor.active = False
    c.db.commit()
    assert await deliver(c) == 0
    assert rows(c)[0].id == r.id
    assert rows(c)[0].status == "suppressed" and rows(c)[0].fehler == reason
    c.push.assert_not_called()


@pytest.mark.asyncio
async def test_exercise_push_and_normal_channel(ctx):
    c = ctx
    c.incident.is_exercise = True
    sync(c, ["assp", "notfall"])
    assert await deliver(c) == 5
    c.sms.assert_not_called()
    for call in c.push.call_args_list:
        args = call.args
        assert args[2].startswith("ÜBUNG: ") and args[3].startswith("ÜBUNG: ")
        assert args[6]["is_exercise"] == "1"
        assert args[5] is (args[6]["kind"] == "notfall")


def test_texts_fallback_and_sms_length(ctx):
    c = ctx
    c.incident.lis_operation_number = None
    _, text = bn._texte(c.incident, c.troop, "withdraw", True)
    assert text.endswith(f"(Einsatz #{c.incident.id})") and "NICHT-VERWENDEN" not in text
    c.troop.name = "A" * 100
    c.incident.lis_operation_number = "B" * 100
    c.incident.is_exercise = True
    title, text = bn._texte(c.incident, c.troop, "max_exceeded", True)
    assert len(text) == 160 and title.startswith("ÜBUNG: ")


def test_integrity_error_savepoint_preserves_outer_transaction(ctx):
    c = ctx

    def conflict(*args):
        raise IntegrityError("Testkonflikt", {}, Exception("duplicate"))

    event.listen(BreathingWarnung, "before_insert", conflict)
    try:
        c.troop.name = "Aenderung bleibt"
        assert sync(c, ["withdraw"]) == []
    finally:
        event.remove(BreathingWarnung, "before_insert", conflict)
    assert rows(c) == []
    c.db.refresh(c.troop)
    assert c.troop.name == "Aenderung bleibt"


@pytest.mark.parametrize("channel,extra", [(None, None), ("einsatz_alarm", {"asuew": "1", "kind": "notfall"})])
def test_notify_user_forwards_optional_fcm_arguments(ctx, monkeypatch, channel, extra):
    c = ctx
    monkeypatch.setattr(push_service, "_push_cfg", lambda db: {"enabled": False})
    fanout = Mock(return_value=2)
    monkeypatch.setattr(push_service, "_notify_fcm_logged", fanout)
    if channel is None:
        result = push_service.notify_user(c.db, c.supervisor.id, "Titel", "Text", "/test", "test")
    else:
        result = push_service.notify_user(c.db, c.supervisor.id, "Titel", "Text", "/test", "test",
                                          channel_id=channel, extra_data=extra)
    assert result == 2
    assert fanout.call_args.args[1:7] == ({c.supervisor.id}, "Titel", "Text", "/test", {"enabled": False}, channel)
    assert fanout.call_args.kwargs == {"extra_data": extra}


def test_real_sender_adapters(ctx, monkeypatch):
    c = ctx
    push = Mock(return_value=1)
    monkeypatch.setattr(bn, "notify_user", push)
    extra = {"asuew": "1"}
    assert bn._push(c.db, c.supervisor.id, "Titel", "Text", "/test", True, extra)
    push.assert_called_once_with(c.db, c.supervisor.id, "Titel", "Text", url="/test", source="breathing",
                                 channel_id="einsatz_alarm", extra_data=extra)


@pytest.mark.asyncio
async def test_real_sms_adapter(ctx, monkeypatch):
    from unittest.mock import AsyncMock

    send = AsyncMock(return_value=True)
    monkeypatch.setattr(bn, "send_sms", send)
    assert await bn._sms(ctx.org.id, "+43664", "Text", "asuew-123")
    send.assert_awaited_once_with(ctx.org.id, "+43664", "Text", job_id="asuew-123")


@pytest.fixture
def watchdog(ctx, monkeypatch):
    from app.services import breathing_service as svc

    enabled = svc.breathing_effective_enabled
    monkeypatch.setattr(svc, "breathing_effective_enabled",
                        lambda org_id, db: org_id == ctx.org.id and enabled(org_id, db))
    monkeypatch.setattr(svc, "_now", lambda: ctx.now.replace(tzinfo=UTC))
    # Bereiter Sicherheitstrupp: sonst entsteht zusaetzlich der Einsatzhinweis kein_sicherheitstrupp.
    ctx.db.add(BreathingTroop(incident_id=ctx.incident.id, name="Sicherheitstrupp", status="bereit",
                              is_sicherheitstrupp=True))
    ctx.db.commit()
    return svc


def open_warnings(c):
    c.db.expire_all()
    return c.db.query(BreathingWarnung).filter(
        BreathingWarnung.troop_id == c.troop.id, BreathingWarnung.erledigt_at.is_(None),
    ).all()


def test_sync_twice_in_same_transaction_has_one_open_warning_per_kind(ctx):
    c = ctx
    kinds = {"withdraw", "notfall"}
    assert {w.kind for w in bn.sync_warnungen(c.db, c.incident, c.troop, kinds, c.now)} == kinds
    assert bn.sync_warnungen(c.db, c.incident, c.troop, kinds, c.now) == []
    c.db.flush()
    assert sorted(w.kind for w in open_warnings(c)) == sorted(kinds)


def test_watchdog_persists_events_across_sessions_and_closes_inactive_kind(ctx, watchdog):
    c = ctx
    c.troop.funk_verloren_at = c.now
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == [(c.incident.id, c.troop.id, "funk_verloren")]
    assert [w.kind for w in open_warnings(c)] == ["funk_verloren"]
    with TestingSession() as second:
        set_tenant_context(second, None)
        assert watchdog._collect_new_breathing_warnings(second) == []
    c.troop.notfall_at = c.now
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == [(c.incident.id, c.troop.id, "notfall")]
    assert [w.kind for w in open_warnings(c)] == ["notfall"]
    assert all(r.status == "suppressed" for r in rows(c)
               if c.db.get(BreathingWarnung, r.warnung_id).kind == "funk_verloren")
    assert watchdog._collect_new_breathing_warnings(c.db) == []


def prepare_max_time(c, watchdog):
    c.troop.planned_duration_min = 1
    c.troop.members.append(TroopMember(free_text_name="Testmitglied", start_press=300, current_press=300))
    c.db.flush()
    watchdog.record_kontrolle(c.db, c.troop, "one_third", "funk", 230)
    watchdog.record_kontrolle(c.db, c.troop, "two_third", "funk", 200)


@pytest.mark.parametrize("flag", [None, "notfall_at", "funk_verloren_at"])
def test_watchdog_returned_troop_closes_only_cleared_flags(ctx, watchdog, flag):
    c = ctx
    if flag:
        setattr(c.troop, flag, c.now)
    else:
        prepare_max_time(c, watchdog)
    c.db.commit()
    kind = {None: "max_exceeded", "notfall_at": "notfall", "funk_verloren_at": "funk_verloren"}[flag]
    assert watchdog._collect_new_breathing_warnings(c.db) == [(c.incident.id, c.troop.id, kind)]
    c.troop.status = "zurueck"
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == []
    assert [w.kind for w in open_warnings(c)] == ([kind] if flag else [])
    if flag:
        if flag == "notfall_at":
            watchdog.clear_notfall(c.db, c.troop)
        else:
            watchdog.set_funk_verloren(c.db, c.troop, False)
        c.db.commit()
        assert watchdog._collect_new_breathing_warnings(c.db) == []
        assert open_warnings(c) == []
    assert all(r.status == "suppressed" for r in rows(c))


def test_watchdog_disabled_org_creates_nothing(ctx, watchdog):
    c = ctx
    c.settings.atemschutz_ueberwachung_modul_aktiv = False
    c.troop.notfall_at = c.now
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == []
    assert open_warnings(c) == []
    assert rows(c) == []


@pytest.mark.asyncio
async def test_watchdog_max_time_stage_one_after_grace_and_ack_suppresses_pending(ctx, watchdog):
    c = ctx
    c.org.escalation_grace_min = 7
    prepare_max_time(c, watchdog)
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == [(c.incident.id, c.troop.id, "max_exceeded")]
    assert c.troop.escalated_at is None
    assert await deliver(c) == 1
    assert c.push.call_count == 1 and c.sms.call_count == 0
    assert await deliver(c, c.now + timedelta(minutes=7) - timedelta(seconds=1)) == 0
    assert await deliver(c, c.now + timedelta(minutes=7)) == 4
    assert c.push.call_count == 3 and c.sms.call_count == 2
    assert all(r.status == "sent" for r in rows(c))
    # A new cycle verifies acknowledgement before the delayed deliveries run.
    c.troop.entry_at = c.now - timedelta(minutes=5)
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == [(c.incident.id, c.troop.id, "max_exceeded")]
    watchdog.ack_warning(c.db, c.troop, "max_time")
    c.db.commit()
    assert watchdog._collect_new_breathing_warnings(c.db) == []
    assert open_warnings(c) == []
    assert all(r.status == "suppressed" for r in rows(c)[5:])
    assert len(rows(c)[5:]) == 5
    assert await deliver(c, c.now + timedelta(minutes=8)) == 0
    assert c.troop.escalated_at is None


@pytest.mark.asyncio
async def test_watchdog_sync_failure_rolls_back_and_loop_recovers(ctx, watchdog, monkeypatch):
    import app.db as database
    from app.services.broadcast import manager

    c = ctx
    c.troop.notfall_at = c.now
    c.db.commit()
    original_sync = bn.sync_warnungen
    calls = 0
    rollback = Mock(wraps=c.db.rollback)
    monkeypatch.setattr(c.db, "rollback", rollback)

    def failing_sync(*args):
        nonlocal calls
        calls += 1
        # Start a write transaction before the SQLite SAVEPOINT.
        c.troop.name = "Rollback-Test"
        c.db.flush()
        result = original_sync(*args)
        if calls == 1:
            raise RuntimeError("Nach Erzeugung fehlgeschlagen")
        return result

    monkeypatch.setattr(bn, "sync_warnungen", failing_sync)
    monkeypatch.setattr(database, "SessionLocal", lambda: c.db)
    close = Mock()
    monkeypatch.setattr(c.db, "close", close)
    from unittest.mock import AsyncMock
    broadcast = AsyncMock()
    monkeypatch.setattr(manager, "broadcast", broadcast)
    sleeps = 0

    async def sleep(delay):
        nonlocal sleeps
        sleeps += 1
        assert delay == 5
        if sleeps == 2:
            rollback.assert_called_once()
            assert open_warnings(c) == []
            assert c.troop.name == "Trupp Alpha"
            broadcast.assert_not_awaited()
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await watchdog._breathing_watchdog_loop()
    assert calls == 2 and close.call_count == 2
    assert [w.kind for w in open_warnings(c)] == ["notfall"]
    broadcast.assert_awaited_once_with(c.incident.id, {
        "type": "troop_warning", "troop_id": c.troop.id, "kind": "notfall",
    })
    assert watchdog.get_watchdog_heartbeat() == c.now.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_notify_loop_survives_errors_and_throttles_logging(monkeypatch, caplog):
    from unittest.mock import AsyncMock

    send = AsyncMock(side_effect=[RuntimeError("one"), RuntimeError("two"), 0, RuntimeError("three"), 0])
    monkeypatch.setattr(bn, "liefere_faellige", send)
    ticks = iter([0.0, 59.0, 60.0])
    monkeypatch.setattr(bn, "monotonic", lambda: next(ticks))
    sleeps = 0

    async def sleep(delay):
        nonlocal sleeps
        assert delay == 5
        sleeps += 1
        if sleeps == 5:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await bn.breathing_notify_loop()
    assert send.await_count == 5
    records = [r for r in caplog.records if r.name == bn.logger.name]
    assert len(records) == 2
    assert all(r.getMessage() == "Atemschutz-Versand: Fehler in der Zustellung" for r in records)
    assert all(r.exc_info[0] is RuntimeError for r in records)


def test_breathing_loops_registered(monkeypatch):
    from tests.background_loop_helpers import registered_background_loop_names

    names = registered_background_loop_names(monkeypatch)
    assert names.count("_breathing_watchdog_loop") == 1
    assert names.count("breathing_notify_loop") == 1


@pytest.mark.asyncio
async def test_heartbeat_delivery_alarm_and_sms_after_180_seconds(ctx):
    c = ctx
    sync(c, ["kein_heartbeat"])
    assert await deliver(c) == 2
    assert {call.args[1] for call in c.push.call_args_list} == {c.leader.id, c.escalation.id}
    assert all(call.args[5] is True for call in c.push.call_args_list)
    assert all(call.args[2] == "Atemschutz: Kein Überwachungsgerät in der App aktiv"
               for call in c.push.call_args_list)
    assert await deliver(c, c.now + timedelta(seconds=179)) == 0
    assert c.sms.call_count == 0
    assert await deliver(c, c.now + timedelta(seconds=180)) == 2
    assert {call.args[1] for call in c.sms.call_args_list} == {c.leader.phone, c.escalation.phone}
