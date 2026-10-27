"""Risiken, Ablaufbindung, Live-Rechte, Kontingent und atomarer globaler Mutex."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.admin_mcp.repair_tools import collect_repair
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.admin_repair import AdminApproval, AdminRepairRun
from app.models.admin_security import utcnow
from app.models.user import AuditLog, UserRole
from app.services.repair import runner
from app.services.repair.registry import ACTIONS, Action, ActionRegistry, Ergebnis, Scope, Vorschau, digest
from tests.test_repair_actions import repair_env  # noqa: F401


class RepairTestScope(Scope):
    ident: int = 1


class TestAction:
    __test__ = False
    def vorbedingungen(self, db, scope):
        pass
    def simulieren(self, db, scope):
        return Vorschau(digest(scope.model_dump()), [{"id": scope.ident, "ausfuehrbar": True, "grund": "ok"}])
    def ausfuehren(self, db, scope, ctx):
        return Ergebnis(1)
    def verifizieren(self, db, scope):
        return Ergebnis()


@pytest.fixture
def test_actions(monkeypatch):
    from app.admin_mcp import repair_tools
    registry = ActionRegistry()
    for action in ACTIONS.values():
        registry.register(action)
    for risk in ("low", "medium", "high", "critical"):
        registry.register(Action("test." + risk, "Test", risk, RepairTestScope, "Nur Test", False, TestAction()))
    registry.seal()
    monkeypatch.setattr(runner, "ACTIONS", registry)
    monkeypatch.setattr(repair_tools, "ACTIONS", registry)
    return registry


def sim(db, user, action="test.medium", scope=None):
    result = collect_repair(db, "admin_reparatur_simulieren", user.id, user.session_epoch, str(uuid4()),
                            aktion_id=action, scope=scope or {"ident": 1})
    assert result["status"] == "ok", result
    return result["evidence"]


def call_execute(db, user, run, **extra):
    return runner.execute(db, run["run_id"], user.id, user.session_epoch, str(uuid4()),
                          extra.pop("simulation_hash", run["simulation_hash"]),
                          extra.pop("idempotency_key", uuid4().hex), **extra)


def setting(db, key, value):
    from app.models.master import SystemSettings
    row = db.get(SystemSettings, key)
    if row is None:
        row = SystemSettings(key=key)
        db.add(row)
    row.value = value
    db.commit()


@pytest.mark.parametrize("risk,auto,confirm,success", [
    ("low", False, False, False), ("low", False, True, True), ("low", True, False, True),
    ("medium", True, False, False), ("medium", False, True, True),
    ("high", False, False, False), ("high", False, True, False),
    ("critical", True, True, False),
])
def test_risk_matrix(repair_env, test_actions, risk, auto, confirm, success):
    db, user, *_ = repair_env
    if auto:
        setting(db, "admin_repair_low_auto", "test." + risk)
    run = sim(db, user, "test." + risk)
    if success:
        assert call_execute(db, user, run, bestaetigt=confirm)["status"] == "erfolgreich"
    else:
        with pytest.raises(ValueError):
            call_execute(db, user, run, bestaetigt=confirm)


@pytest.mark.parametrize("kind", ["missing", "expired", "used", "foreign_user", "foreign_action", "long_lifetime", "valid"])
def test_high_approval_binding(repair_env, test_actions, kind):
    db, user, *_ = repair_env
    run = sim(db, user, "test.high")
    row = db.get(AdminRepairRun, run["run_id"])
    token = runner.issue_approval(db, row, user.id, str(uuid4()))
    db.commit()
    approval = db.scalar(select(AdminApproval))
    if kind == "expired":
        approval.created_at, approval.expires_at = utcnow() - timedelta(minutes=11), utcnow() - timedelta(minutes=1)
    elif kind == "used":
        approval.used_at = utcnow()
    elif kind == "foreign_user":
        approval.user_id += 1
    elif kind == "foreign_action":
        approval.aktion_hash = "f" * 64
    elif kind == "long_lifetime":
        approval.expires_at = approval.created_at + timedelta(minutes=11)
    db.commit()
    if kind == "valid":
        result = call_execute(db, user, run, bestaetigt=True, approval_token=token, idempotency_key="approval-replay")
        assert result["status"] == "erfolgreich"
        assert db.get(AdminApproval, approval.id).used_at
        assert call_execute(db, user, run, bestaetigt=True, approval_token=token,
                            idempotency_key="approval-replay") == result
        with pytest.raises(ValueError):
            runner.consume_approval(db, row, user.id, token)
    else:
        with pytest.raises(ValueError, match="Freigabe"):
            call_execute(db, user, run, bestaetigt=True, approval_token=None if kind == "missing" else token)
    assert token not in " ".join(a.payload_json or "" for a in db.scalars(select(AuditLog)))


def test_hash_stale_data_and_resimulation_invalidates_approval(repair_env, test_actions):
    db, user, *_ = repair_env
    run = sim(db, user)
    with pytest.raises(ValueError, match="Simulation"):
        call_execute(db, user, run, bestaetigt=True, simulation_hash="f" * 64)
    row = db.get(AdminRepairRun, run["run_id"])
    row.simulated_at = utcnow() - timedelta(minutes=16)
    db.commit()
    with pytest.raises(ValueError, match="veraltet"):
        call_execute(db, user, run, bestaetigt=True)
    high = sim(db, user, "test.high")
    token = runner.issue_approval(db, db.get(AdminRepairRun, high["run_id"]), user.id, "c")
    db.commit()
    again = collect_repair(db, "admin_reparatur_simulieren", user.id, user.session_epoch, "c",
                           run_id=high["run_id"], scope={"ident": 2})["evidence"]
    with pytest.raises(ValueError, match="Freigabe"):
        call_execute(db, user, again, bestaetigt=True, approval_token=token)


@pytest.mark.parametrize("change", ["2fa", "switch", "access", "active", "role", "epoch", "einsatz", "gsl"])
def test_live_denial_between_simulation_and_execution(repair_env, test_actions, change):
    db, user, org, _ = repair_env
    run = sim(db, user)
    epoch = user.session_epoch
    if change == "2fa":
        setting(db, "admin_2fa_enabled", "false")
    elif change == "switch":
        setting(db, "admin_repair_enabled", "false")
    elif change == "access":
        user.admin_mcp_access = False
    elif change == "active":
        user.active = False
    elif change == "role":
        db.delete(db.scalar(select(UserRole).where(UserRole.user_id == user.id)))
    elif change == "epoch":
        user.session_epoch += 1
    elif change == "einsatz":
        from app.models.incident import Incident
        db.add(Incident(status="active", primary_org_id=org.id))
    else:
        from app.models.major_incident import MajorIncident
        db.add(MajorIncident(status="active", org_id=org.id, name="Test"))
    db.commit()
    with pytest.raises(ValueError):
        runner.execute(db, run["run_id"], user.id, epoch, "c", run["simulation_hash"], uuid4().hex, True)
    assert db.get(AdminRepairRun, run["run_id"]).status == "simuliert"


def test_live_recheck_after_claim(repair_env, test_actions):
    db, user, *_ = repair_env
    run = sim(db, user)
    count = 0
    def authorize():
        nonlocal count
        count += 1
        if count == 2:
            raise PermissionError("widerrufen")
    with pytest.raises(PermissionError):
        call_execute(db, user, run, bestaetigt=True, authorize=authorize)
    row = db.get(AdminRepairRun, run["run_id"])
    assert row.status == "fehlgeschlagen" and row.active_aktion is None


def test_idempotency_scope_and_audit_rate_limit(repair_env, test_actions):
    db, user, *_ = repair_env
    first = sim(db, user)
    answer = call_execute(db, user, first, bestaetigt=True, idempotency_key="idempotent-key")
    assert call_execute(db, user, first, idempotency_key="idempotent-key") == answer
    second = sim(db, user, scope={"ident": 2})
    with pytest.raises(ValueError, match="anderen Scope"):
        call_execute(db, user, second, bestaetigt=True, idempotency_key="idempotent-key")
    with pytest.raises(ValueError, match="Idempotency"):
        call_execute(db, user, second, bestaetigt=True, idempotency_key="")
    assert db.scalar(select(AdminRepairRun).where(AdminRepairRun.id == first["run_id"])).idempotency_key \
        == hash_api_key("idempotent-key")
    for _ in range(18):
        collect_repair(db, "admin_reparatur_status", user.id, user.session_epoch, "c", run_id=first["run_id"])
    limited = collect_repair(db, "admin_reparatur_status", user.id, user.session_epoch, "c", run_id=first["run_id"])
    assert limited["evidence"]["fehler"] == "Rate-Limit erreicht"
    audit = " ".join(a.payload_json or "" for a in db.scalars(select(AuditLog)))
    assert "idempotent-key" not in audit
    assert all("korrelations_id" in json.loads(a.payload_json) for a in db.scalars(select(AuditLog)))


def test_two_different_runs_same_action_claim_once(repair_env, test_actions):
    db, user, _, factory = repair_env
    runs = [sim(db, user)["run_id"] for _ in range(2)]
    barrier = Barrier(2)
    def claim(ident):
        with factory() as session:
            set_tenant_context(session, None)
            barrier.wait(timeout=10)
            return runner.claim_run(session, ident, uuid4().hex)
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, runs))
    assert sorted(results) == [False, True]
    db.expire_all()
    winner = db.scalar(select(AdminRepairRun).where(AdminRepairRun.status == "laufend"))
    assert winner.active_aktion == "test.medium"
    assert not runner.claim_run(db, winner.id, uuid4().hex)


def test_changed_search_data_requires_new_dryrun(repair_env):
    from tests.test_repair_actions import search_job
    db, user, org, _ = repair_env
    job = search_job(db, org.id)
    run = sim(db, user, "suche.rueckstand_anstossen", {"org_id": org.id, "ids": [job.id]})
    job.versuche += 1
    db.commit()
    with pytest.raises(ValueError, match="Datenbestand"):
        call_execute(db, user, run, bestaetigt=True)


def test_two_concurrent_executions_only_one_action(repair_env, test_actions, monkeypatch):
    import time
    db, user, _, factory = repair_env
    action = test_actions["test.medium"].impl
    invoked = []
    def perform(db, scope, ctx):
        invoked.append(ctx.run_id)
        time.sleep(0.15)
        return Ergebnis(1)
    monkeypatch.setattr(action, "ausfuehren", perform)
    runs = [sim(db, user) for _ in range(2)]
    user_id, epoch = user.id, user.session_epoch
    barrier = Barrier(2)
    def execute(run):
        with factory() as session:
            set_tenant_context(session, None)
            first = True
            def authorize():
                nonlocal first
                if first:
                    first = False
                    barrier.wait(timeout=10)
            try:
                return runner.execute(session, run["run_id"], user_id, epoch, "c", run["simulation_hash"],
                                      uuid4().hex, True, authorize=authorize)["status"]
            except ValueError:
                return "blockiert"
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(execute, runs))
    assert sorted(result) == ["blockiert", "erfolgreich"]
    assert len(invoked) == 1


def test_partial_temp_failure_persisted_and_repeatable(repair_env, tmp_path, monkeypatch):
    import os
    from app.config import settings
    db, user, org, _ = repair_env
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    directory = tmp_path / "_mcp_uploads" / str(org.id)
    directory.mkdir(parents=True)
    names = sorted([uuid4().hex + ".pdf", uuid4().hex + ".pdf"])
    for name in names:
        (directory / name).write_bytes(b"temporary")
        os.utime(directory / name, (1, 1))
    run = sim(db, user, "dateien.verwaiste_temp_loeschen", {"org_id": org.id, "dateien": names})
    unlink = os.unlink
    def failing_unlink(name, *args, **kwargs):
        if name == names[1]:
            raise OSError("secret path must never appear in audit")
        return unlink(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", failing_unlink)
        with pytest.raises(OSError):
            call_execute(db, user, run, bestaetigt=True)
    row = db.get(AdminRepairRun, run["run_id"])
    assert row.status == "fehlgeschlagen" and row.active_aktion is None and row.ergebnis["geaendert"] == 1
    assert not (directory / names[0]).exists() and (directory / names[1]).exists()
    again = sim(db, user, "dateien.verwaiste_temp_loeschen", {"org_id": org.id, "dateien": names})
    assert call_execute(db, user, again, bestaetigt=True)["ergebnis"]["geaendert"] == 1
    assert not list(directory.iterdir())
    audit = " ".join(a.payload_json or "" for a in db.scalars(select(AuditLog)))
    assert "secret path" not in audit


def test_verification_schedules_existing_diagnose_check(repair_env, test_actions):
    from app.models.diagnose import DiagFinding, DiagRun
    db, user, *_ = repair_env
    run = sim(db, user)
    parent = DiagRun(korrelations_id=str(uuid4()), active_slot=None, status="done")
    db.add(parent)
    db.flush()
    finding = DiagFinding(run_id=parent.id, finding_key=uuid4().hex * 2, check_id="gateway.jobs", modul="gateway",
                         schweregrad="warn", titel="Test", ursache_hypothese="Test", empfehlung="Test")
    db.add(finding)
    db.flush()
    db.get(AdminRepairRun, run["run_id"]).diag_finding_id = finding.id
    db.commit()
    result = collect_repair(db, "admin_reparatur_verifizieren", user.id, user.session_epoch, str(uuid4()),
                            run_id=run["run_id"], folgediagnose=True)
    follow = db.get(DiagRun, result["evidence"]["folgediagnose"]["run_id"])
    assert follow.status == "queued" and follow.checks == ["gateway.jobs"]


def test_simulation_expires_while_claiming(repair_env, test_actions):
    db, user, *_ = repair_env
    run = sim(db, user)
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 2:
            db.get(AdminRepairRun, run["run_id"]).simulated_at = utcnow() - timedelta(minutes=16)
            db.flush()
    with pytest.raises(ValueError, match="veraltet"):
        call_execute(db, user, run, bestaetigt=True, authorize=authorize)
    assert db.get(AdminRepairRun, run["run_id"]).status == "fehlgeschlagen"
