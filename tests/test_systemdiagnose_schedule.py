"""DB-Claims, Einsatzverschiebung und Sammelmail ohne fachliche Inhalte."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app.models.diag_schedule import DiagNotification, DiagScheduleSlot
from app.models.diagnose import DiagFinding, DiagRun
from app.services import diag_schedule as schedule
from app.services.diagnose import runner
from app.services.diagnose.registry import CHECKS, CheckResult, Finding
from tests.test_diagnose_checks import diag_engine, factory  # noqa: F401
from tests.test_systemdiagnose_ui import make_finding, setting

NOW = datetime(2026, 10, 28, 2, 0)  # 03:00 Europe/Vienna


def enable(db):
    for key, value in {**schedule.DEFAULTS, "diag_schedule_enabled":"true",
                       "diag_schedule_mail":"admin@example.org"}.items():
        setting(db, key, value)


def test_default_off_and_future_time(factory, monkeypatch):
    monkeypatch.setattr(schedule, "einsatz_aktiv", lambda db: False)
    with factory() as db:
        assert schedule.schedule_once(db, NOW) is None
        assert not list(db.scalars(select(DiagRun)))
        enable(db)
        assert schedule.schedule_once(db, NOW - timedelta(minutes=1)) is None
        assert schedule.schedule_once(db, NOW + timedelta(hours=7)) is None
        ident = schedule.schedule_once(db, NOW)
        assert ident
        row = db.get(DiagRun, ident)
        assert row.quelle == "schedule" and row.angefordert_von is None
        assert all(CHECKS[c].kosten == "leicht" for c in row.checks)
        assert schedule.schedule_once(db, NOW) is None


def test_parallel_loops_one_daily_claim(factory, monkeypatch):
    monkeypatch.setattr(schedule, "einsatz_aktiv", lambda db: False)
    with factory() as db:
        enable(db)
    barrier = Barrier(2)
    def loop(_):
        with factory() as db:
            barrier.wait(timeout=5)
            return schedule.schedule_once(db, NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(loop, range(2)))
    assert sum(r is not None for r in results) == 1
    with factory() as db:
        assert len(list(db.scalars(select(DiagScheduleSlot)))) == 1
        assert len(list(db.scalars(select(DiagRun)))) == 1
        row = db.scalar(select(DiagRun))
        row.status, row.active_slot = "done", None
        db.commit()
        assert schedule.schedule_once(db, NOW + timedelta(minutes=1)) is None


def test_einsatz_postpone_six_hours_light_checks(factory, monkeypatch):
    monkeypatch.setattr(schedule, "einsatz_aktiv", lambda db: True)
    with factory() as db:
        enable(db)
        setting(db, "diag_schedule_scope", "voll")
        assert schedule.schedule_once(db, NOW) is None
        assert schedule.schedule_once(db, NOW + timedelta(hours=5, minutes=59)) is None
        ident = schedule.schedule_once(db, NOW + timedelta(hours=6))
        row = db.get(DiagRun, ident)
        assert row.umfang["einsatzgedrosselt"]
        assert all(CHECKS[c].kosten == "leicht" for c in row.checks)


@pytest.fixture
def mail(monkeypatch):
    messages = []
    async def deliver(db, org_id, message, smtp):
        assert org_id is None
        messages.append(message)
    monkeypatch.setattr(schedule, "deliver", deliver)
    monkeypatch.setattr(schedule, "get_smtp_cfg", lambda db: {"from":"test@example.org"})
    return messages


def test_dedup_repeat_severity_batch_and_privacy(factory, mail):
    with factory() as db:
        enable(db)
        first = make_finding(db)
        second = make_finding(db, "kritisch")
        second.run_id = first.run_id
        warn = make_finding(db, "warn")
        warn.run_id = first.run_id
        db.commit()
        ident, key = first.run_id, first.finding_key
        schedule.notify_run(db, ident, NOW)
        schedule.notify_run(db, ident, NOW + timedelta(hours=25))
        assert len(mail) == 1  # pro Lauf nur eine Sammelmail, auch beim zweiten Aufruf
        text = mail[0].get_body(preferencelist=("plain",)).get_content()
        assert "kritisch" in text and "fail" in text and "warn" not in text
        assert all(value not in text for value in ("Alice", "PRIVATE", "alice@example.org"))
        assert "/admin/systemdiagnose" in text
        next_run = DiagRun(status="done", active_slot=None, korrelations_id="next-run")
        db.add(next_run)
        db.flush()
        first.run_id = second.run_id = next_run.id
        db.commit()
        schedule.notify_run(db, next_run.id, NOW + timedelta(hours=23))
        assert len(mail) == 1
        later = DiagRun(status="done", active_slot=None, korrelations_id="later-run")
        db.add(later)
        db.flush()
        first.run_id = second.run_id = later.id
        db.commit()
        schedule.notify_run(db, later.id, NOW + timedelta(hours=24))
        assert len(mail) == 2
        assert db.get(DiagNotification, key).count == 2


def test_resolved_then_reappeared_alerts_again(factory, mail):
    with factory() as db:
        enable(db)
        run = DiagRun(status="running", active_slot=1, korrelations_id="resolution")
        db.add(run)
        db.commit()
        finding = Finding("stable", "Gateway Fehler", "fail")
        runner.persist_result(db, run.id, "gateway.jobs", CheckResult([finding]), success=True)
        schedule.notify_run(db, run.id, NOW)
        assert len(mail) == 1
        runner.persist_result(db, run.id, "gateway.jobs", CheckResult([]), success=True)
        assert db.get(DiagNotification, finding.key("gateway.jobs")).last_sent_at is None
        runner.persist_result(db, run.id, "gateway.jobs", CheckResult([finding]), success=True)
        run.status, run.active_slot = "done", None
        other = DiagRun(status="done", active_slot=None, korrelations_id="reappeared")
        db.add(other)
        db.flush()
        db.scalar(select(DiagFinding)).run_id = other.id
        db.commit()
        schedule.notify_run(db, other.id, NOW + timedelta(minutes=2))
        assert len(mail) == 2


def test_send_failure_does_not_block_run(factory, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("SECRET")
    monkeypatch.setattr(schedule, "get_smtp_cfg", broken)
    with factory() as db:
        enable(db)
        row = make_finding(db)
        schedule.notify_run(db, row.run_id, NOW)
        assert db.get(DiagRun, row.run_id).status == "done"
        assert db.get(DiagNotification, row.finding_key).count == 1
        schedule.notify_run(db, row.run_id, NOW)


def test_minimum_critical_and_empty_recipients(factory, mail):
    with factory() as db:
        enable(db)
        ident = make_finding(db, "fail").run_id
        setting(db, "diag_schedule_severity", "kritisch")
        schedule.notify_run(db, ident, NOW)
        assert not mail
        setting(db, "diag_schedule_mail", "")
        ident = make_finding(db, "kritisch").run_id
        schedule.notify_run(db, ident, NOW)
        assert not mail


def test_migration_0329_upgrade_and_downgrade():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    spec = importlib.util.spec_from_file_location("migration0329", "alembic/versions/0329_diag_schedule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE diag_run (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert "diag_notification" in inspect(connection).get_table_names()
            module.downgrade()
            assert "diag_notification" not in inspect(connection).get_table_names()
    engine.dispose()
