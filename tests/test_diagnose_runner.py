"""Lebenszyklus, konkurrierende Runner, harte Grenzen und globale Retention."""
import asyncio
import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text

from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.services.diagnose import runner
from app.services.diagnose.registry import CHECKS, Check, CheckResult, Finding, execute_isolated
from tests import test_diagnose_checks as fixtures
from tests.test_diagnose_checks import insert

diag_engine = fixtures.diag_engine
factory = fixtures.factory


def queue(db, checks=None, **kw):
    row = DiagRun(checks=checks or ["worker.zustand"], umfang={"typ": "voll"},
                  korrelations_id=str(uuid4()), active_slot=1, **kw)
    db.add(row)
    db.commit()
    return row.id


def test_dedup_stable_key_resolution_and_failed_partial_checks(factory):
    finding = Finding({"id": 12, "typ": " test "}, "Test", evidenz={"anzahl": 1, "privat": "Max Muster"})
    assert finding.key("worker.zustand") == Finding({"typ": "test", "id": 12}, "anderer Titel").key("worker.zustand")
    assert finding.key("worker.zustand") != finding.key("anderer_check")
    with factory() as db:
        ident = queue(db)
        assert runner.claim_run(db) == ident
        for _ in range(2):
            runner.persist_result(db, ident, "worker.zustand", CheckResult([finding]), success=True)
        row = db.scalar(select(DiagFinding))
        assert row.occurrences == 2
        assert row.first_seen_at <= row.last_seen_at
        assert row.evidenz["privat"] == "[MASKIERT]"
        assert len(list(db.scalars(select(DiagFinding)))) == 1
        runner.persist_result(db, ident, "worker.zustand", CheckResult([], False), success=True)
        db.refresh(row)
        assert row.status == "offen"
        runner.persist_result(db, ident, "worker.zustand", CheckResult([], False), success=False)
        db.refresh(row)
        assert row.status == "offen"
        runner.persist_result(db, ident, "db.zustand", CheckResult(), success=True)
        db.refresh(row)
        assert row.status == "offen"
        runner.persist_result(db, ident, "worker.zustand", CheckResult(), success=True)
        db.refresh(row)
        assert row.status == "behoben"
        runner.persist_result(db, ident, "worker.zustand", CheckResult([finding]), success=True)
        db.refresh(row)
        assert row.status == "offen" and row.occurrences == 3
        row.status = "unterdrueckt"
        db.commit()
        runner.persist_result(db, ident, "worker.zustand", CheckResult(), success=True)
        db.refresh(row)
        assert row.status == "unterdrueckt"


def test_concurrent_atomic_claim(factory):
    with factory() as db:
        ident = queue(db)
    barrier = Barrier(2)
    def claim():
        with factory() as db:
            barrier.wait(timeout=10)
            return runner.claim_run(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: claim(), range(2)))
    assert values.count(ident) == 1 and values.count(None) == 1


def test_stale_recovery_and_retention(factory):
    with factory() as db:
        ident = queue(db)
        runner.claim_run(db)
        row = db.get(DiagRun, ident)
        row.fortschritt_am = utcnow() - timedelta(minutes=11)
        db.commit()
        assert runner.recover_stale(db) == 1
        db.refresh(row)
        assert row.status == "failed" and row.active_slot is None
        assert runner.recover_stale(db) == 0
        fresh = queue(db)
        runner.claim_run(db)
        assert runner.recover_stale(db) == 0
        db.get(DiagRun, fresh).created_at = utcnow() - timedelta(days=31)
        row.created_at = utcnow() - timedelta(days=31)
        db.commit()
        runner.retention(db)
        assert db.get(DiagRun, fresh) is not None  # laufende Laeufe nie loeschen
        assert db.get(DiagRun, ident) is None


def test_retention_preserves_open_suppressed_and_recent_findings(factory):
    with factory() as db:
        ident = queue(db)
        runner.claim_run(db)
        runner.persist_result(db, ident, "worker.zustand", CheckResult([
            Finding("open", "Offen"), Finding("fixed", "Behoben"), Finding("suppressed", "Unterdrueckt"),
            Finding("recent", "Neu behoben")]), success=True)
        for f in db.scalars(select(DiagFinding)):
            f.status = {"Offen": "offen", "Behoben": "behoben", "Unterdrueckt": "unterdrueckt",
                        "Neu behoben": "behoben"}[f.titel]
            f.behoben_am = utcnow()
            if f.titel != "Neu behoben":
                f.behoben_am = utcnow() - timedelta(days=31)
                f.last_seen_at = utcnow() - timedelta(days=31)
        row = db.get(DiagRun, ident)
        row.created_at = utcnow() - timedelta(days=31)
        row.status, row.active_slot = "done", None
        db.commit()
        runner.retention(db)
        assert set(db.scalars(select(DiagFinding.titel))) == {"Offen", "Unterdrueckt", "Neu behoben"}
        assert db.get(DiagRun, ident) is not None


@pytest.mark.parametrize("incident_table", ["incident", "major_incident"])
def test_active_incident_skips_medium_and_heavy(factory, monkeypatch, incident_table):
    with factory() as db:
        insert(db, incident_table, status="active", org_id=1) if incident_table == "major_incident" else insert(
            db, incident_table, status="active")
        ident = queue(db, ["einsatz.integritaet", "objekte.dokumente"])
    def forbidden(*args):
        pytest.fail("Mittlerer/schwerer Check darf waehrend Einsatz nicht laufen")
    monkeypatch.setattr(runner, "execute_isolated", forbidden)
    asyncio.run(runner.run_once(factory))
    with factory() as db:
        row = db.get(DiagRun, ident)
        assert row.status == "done"
        assert set(row.fortschritt["uebersprungen"].values()) == {"uebersprungen_wegen_einsatz"}
        assert len(row.fortschritt["uebersprungen"]) == 2
        assert not list(db.scalars(select(DiagFinding)))


def test_runner_check_failure_does_not_stop_or_resolve(factory, monkeypatch):
    with factory() as db:
        ident = queue(db, ["db.zustand", "worker.zustand"])
        runner.claim_run(db)
        runner.persist_result(db, ident, "db.zustand", CheckResult([Finding("old", "Alt")]), success=True)
        row = db.get(DiagRun, ident)
        row.status = "queued"
        db.commit()
    seen = []
    def execute(bind, check, seconds):
        seen.append(check.id)
        if check.id == "db.zustand":
            raise TimeoutError("secret=PRIVATE")
        return CheckResult()
    monkeypatch.setattr(runner, "execute_isolated", execute)
    asyncio.run(runner.run_once(factory))
    with factory() as db:
        assert seen == ["db.zustand", "worker.zustand"]
        assert db.get(DiagRun, ident).status == "done"
        assert set(db.scalars(select(DiagFinding.status))) == {"offen"}
        assert set(db.scalars(select(DiagFinding.titel))) == {"Alt", "check_fehlgeschlagen"}


def test_two_runners_execute_once(factory, monkeypatch):
    with factory() as db:
        ident = queue(db)
    seen = []
    def execute(bind, check, seconds):
        seen.append(check.id)
        time.sleep(0.05)
        return CheckResult()
    monkeypatch.setattr(runner, "execute_isolated", execute)
    async def both():
        await asyncio.gather(runner.run_once(factory), runner.run_once(factory))
    asyncio.run(both())
    assert seen == ["worker.zustand"]
    with factory() as db:
        assert db.get(DiagRun, ident).status == "done"


def test_total_time_limit(factory, monkeypatch):
    with factory() as db:
        ident = queue(db, ["worker.zustand", "db.zustand"])
    monkeypatch.setattr(runner, "RUN_TIMEOUT", 0)
    asyncio.run(runner.run_once(factory))
    with factory() as db:
        row = db.get(DiagRun, ident)
        assert row.status == "failed" and row.active_slot is None
        assert set(row.fortschritt["uebersprungen"]) == {"worker.zustand", "db.zustand"}


def blocking_check(db, budget):
    time.sleep(60)
    return CheckResult()


def test_hard_process_timeout(diag_engine):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        execute_isolated(diag_engine, Check("blocked", "test", "", "leicht", blocking_check), 0.2)
    assert time.monotonic() - started < 3


def test_process_executes_real_check(diag_engine):
    result = execute_isolated(diag_engine, CHECKS["einsatz.integritaet"])
    assert result.vollstaendig and result.findings == []


def test_migration_0327(tmp_path):
    spec = importlib.util.spec_from_file_location("migration0327", "alembic/versions/0327_diagnose.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0327" and migration.down_revision == "0326"
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as con:
        con.execute(text("CREATE TABLE user (id BIGINT PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(con))
        migration.upgrade()
        schema = inspect(con)
        for name in ("diag_run", "diag_finding"):
            assert "org_id" not in {c["name"] for c in schema.get_columns(name)}
        assert schema.get_foreign_keys("diag_finding")[0]["options"]["ondelete"] == "CASCADE"
        migration.downgrade()
        assert inspect(con).get_table_names() == ["user"]
    engine.dispose()
