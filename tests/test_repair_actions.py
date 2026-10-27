"""Produktionsaktionen: Bestandsvergleich, Fencing, Grenzen und Dateisicherheit."""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.admin_security import utcnow
from app.models.gateway import PrintJob
from app.models.incident import IncidentAlarmJob
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.mcp import MCPUpload
from app.models.sms import SmsDispatchAttempt, SmsDispatchJob
from app.models.suche import SearchOutbox
from app.models.user import Role, User, UserRole
from app.services.repair.actions import JobsScope, SearchScope, TempScope
from app.services.repair.registry import ACTIONS, RepairContext, Scope, simulate


@pytest.fixture
def repair_env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'repair.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    db = factory()
    set_tenant_context(db, None)
    org = FireDept(name="Repair Test", slug="repair-test")
    role = Role(code="system_admin", label="Systemadmin")
    user = User(username="repair-admin", display_name="Repair Admin", password_hash=hash_password("test"),
                admin_mcp_access=True)
    db.add_all([org, role, user])
    db.flush()
    db.add_all([UserRole(user_id=user.id, role_id=role.id), OrgSettings(org_id=org.id, suche_aktiv=True)])
    for key in ("admin_2fa_enabled", "admin_mcp_enabled", "admin_repair_enabled"):
        db.add(SystemSettings(key=key, value="true"))
    db.commit()
    yield db, user, org, factory
    db.close()
    engine.dispose()


def snapshot(db):
    db.commit()
    conn = db.get_bind().raw_connection()
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def add_jobs(db, org_id):
    alarm = IncidentAlarmJob(org_id=org_id, incident_id=1, channel="mail", status="pending", attempt_count=0,
                             lease_until=utcnow() - timedelta(minutes=20))
    sms = SmsDispatchJob(id=uuid4().hex, org_id=org_id, job_key=uuid4().hex, payload_hash="0" * 64,
        phone="SECRET-PHONE", text="SECRET-TEXT", status="claimed", generation=1, claim_token="old-token",
        lease_until=utcnow() - timedelta(minutes=20), expires_at=utcnow() + timedelta(hours=1),
        next_attempt_at=utcnow(), providers=[], created_at=utcnow())
    printing = PrintJob(org_id=org_id, gateway_id=1, document_type="objektblatt", status="sent", delivery="lease",
        phase="leased", lease_generation=1, lease_token="old-token", attempts=1, idempotency_key=uuid4().hex,
        lease_expires_at=utcnow() - timedelta(minutes=20))
    db.add_all([alarm, sms, printing])
    db.flush()
    db.add(SmsDispatchAttempt(org_id=org_id, job_id=sms.id, generation=1, created_at=utcnow()))
    db.commit()
    return alarm, sms, printing


def test_outbox_dry_run_execute_repeat_verify(repair_env):
    db, user, org, _ = repair_env
    alarm, sms, printing = add_jobs(db, org.id)
    scope = JobsScope(org_id=org.id, alarm_ids=[alarm.id], sms_ids=[sms.id], druck_ids=[printing.id])
    action = ACTIONS["outbox.haengende_claims_freigeben"]
    before = snapshot(db)
    preview = simulate(db, action, scope)
    assert preview.public()["ausfuehrbar"] == 3
    assert snapshot(db) == before
    assert "SECRET" not in str(preview.public()) and "old-token" not in str(preview.public())
    result = action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test", preview.daten_hash))
    db.commit()
    assert result.geaendert == 3
    db.expire_all()
    assert sms.status == printing.status == "queued" and alarm.status == "pending"
    assert sms.claim_token is None and printing.lease_token is None and printing.lease_generation == 2
    assert sms.text == "SECRET-TEXT" and sms.generation == 1
    attempt = db.scalar(select(SmsDispatchAttempt))
    assert attempt.outcome == "not_dispatched" and attempt.finished_at
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert action.impl.verifizieren(db, scope).uebersprungen == 0


@pytest.mark.parametrize("field,value", [
    ("status", "acknowledged"), ("may_have_sent", True), ("received_at", "now"), ("attempted_at", "now"),
    ("accepted_at", "now"), ("finished_at", "now"), ("delivery_status", "sent"), ("generation", 5),
    ("provider", "gateway"), ("connection_id", "connection"), ("gateway_id", 1), ("lease_until", "future"),
])
def test_sms_delivery_evidence_untouched(repair_env, field, value):
    db, user, org, _ = repair_env
    _, sms, _ = add_jobs(db, org.id)
    setattr(sms, field, utcnow() if value == "now" else utcnow() + timedelta(hours=1) if value == "future" else value)
    db.commit()
    action = ACTIONS["outbox.haengende_claims_freigeben"]
    scope = JobsScope(org_id=org.id, sms_ids=[sms.id])
    before = snapshot(db)
    assert simulate(db, action, scope).public()["ausfuehrbar"] == 0
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert snapshot(db) == before


@pytest.mark.parametrize("field,value", [("phase", "submitting"), ("phase", "spooled"), ("spool_ref", "123"),
    ("attempts", 3), ("delivery", "push"), ("status", "done"), ("push_route_id", "route")])
def test_print_evidence_untouched(repair_env, field, value):
    db, user, org, _ = repair_env
    _, _, printing = add_jobs(db, org.id)
    setattr(printing, field, value)
    db.commit()
    scope = JobsScope(org_id=org.id, druck_ids=[printing.id])
    action = ACTIONS["outbox.haengende_claims_freigeben"]
    before = snapshot(db)
    assert not simulate(db, action, scope).objekte[0]["ausfuehrbar"]
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert snapshot(db) == before


def test_alarm_sending_is_never_retried_and_cross_org(repair_env):
    db, user, org, _ = repair_env
    alarm, sms, printing = add_jobs(db, org.id)
    alarm.status, alarm.attempt_count = "sending", 1
    db.commit()
    action = ACTIONS["outbox.haengende_claims_freigeben"]
    scope = JobsScope(org_id=org.id, alarm_ids=[alarm.id])
    assert simulate(db, action, scope).objekte[0]["grund"] == "versand_nicht_ausschliessbar"
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    foreign = JobsScope(org_id=org.id + 1, alarm_ids=[alarm.id], sms_ids=[sms.id], druck_ids=[printing.id])
    assert simulate(db, action, foreign).public()["ausfuehrbar"] == 0
    assert action.impl.ausfuehren(db, foreign, RepairContext(1, user.id, "test")).geaendert == 0


def test_sms_attempt_and_transport_evidence(repair_env):
    from app.models.gateway import GatewayMessage
    db, user, org, _ = repair_env
    _, sms, _ = add_jobs(db, org.id)
    attempt = db.scalar(select(SmsDispatchAttempt))
    action = ACTIONS["outbox.haengende_claims_freigeben"]
    scope = JobsScope(org_id=org.id, sms_ids=[sms.id])
    attempt.route_id = "route"
    db.commit()
    assert simulate(db, action, scope).public()["ausfuehrbar"] == 0
    attempt.route_id = None
    db.add(GatewayMessage(id=uuid4().hex, org_id=org.id, gateway_kind="sms", gateway_id=1, connection_id="c",
        worker_id="w", message={}, job_key=f"{sms.id}:1", state="sent", created_at=utcnow(), expires_at=utcnow()))
    db.commit()
    before = snapshot(db)
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert snapshot(db) == before


def search_job(db, org_id):
    job = SearchOutbox(org_id=org_id, quelle="objekt", quell_id="1", status="retry", versuche=1,
                       naechster_versuch_am=utcnow() + timedelta(minutes=30))
    db.add(job)
    db.commit()
    return job


def test_search_existing_queue_only(repair_env):
    db, user, org, _ = repair_env
    job = search_job(db, org.id)
    action = ACTIONS["suche.rueckstand_anstossen"]
    scope = SearchScope(org_id=org.id, ids=[job.id])
    before = snapshot(db)
    preview = simulate(db, action, scope)
    assert preview.public()["ausfuehrbar"] == 1 and snapshot(db) == before
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test", preview.daten_hash)).geaendert == 1
    db.commit()
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert action.impl.verifizieren(db, scope).uebersprungen == 0
    assert job.status == "retry" and job.versuche == 1
    db.get(OrgSettings, org.id).suche_aktiv = False
    db.commit()
    with pytest.raises(ValueError, match="nicht aktiviert"):
        simulate(db, action, scope)


@pytest.fixture
def temp_files(repair_env, tmp_path, monkeypatch):
    from app.config import settings
    db, user, org, _ = repair_env
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    directory = tmp_path / "_mcp_uploads" / str(org.id)
    directory.mkdir(parents=True)
    names = [uuid4().hex + ".pdf" for _ in range(5)]
    for name in names:
        path = directory / name
        path.write_bytes(b"temp")
        os.utime(path, (1, 1))
    return directory, names


def test_temp_dry_run_execute_repeat_verify(repair_env, temp_files):
    db, user, org, _ = repair_env
    directory, names = temp_files
    action = ACTIONS["dateien.verwaiste_temp_loeschen"]
    scope = TempScope(org_id=org.id, dateien=names)
    before = snapshot(db)
    preview = simulate(db, action, scope)
    assert preview.public()["ausfuehrbar"] == 5
    assert len(list(directory.iterdir())) == 5 and snapshot(db) == before
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test", preview.daten_hash)).geaendert == 5
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert action.impl.verifizieren(db, scope).uebersprungen == 0
    assert not list(directory.iterdir())


def test_temp_reference_young_symlink_size_and_traversal(repair_env, temp_files, tmp_path):
    db, user, org, _ = repair_env
    directory, names = temp_files
    action = ACTIONS["dateien.verwaiste_temp_loeschen"]
    db.add(MCPUpload(upload_id=names[0].split(".")[0], token_hash="x", org_id=org.id, user_id=user.id,
                     dateiname="PERSONAL", expires_at=utcnow()))
    db.commit()
    os.utime(directory / names[1], None)
    (directory / names[2]).unlink()
    target = tmp_path / "outside"
    target.write_bytes(b"untouched")
    (directory / names[2]).symlink_to(target)
    scope = TempScope(org_id=org.id, dateien=names, max_bytes=4)
    preview = simulate(db, action, scope)
    reasons = {x["grund"] for x in preview.objekte}
    assert reasons == {"db_referenz", "juenger_als_24h", "keine_regulaere_datei", "ok", "groessenlimit"}
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 1
    assert target.read_bytes() == b"untouched" and (directory / names[2]).is_symlink()
    for bad in ("../file.pdf", "/tmp/test.pdf", "a.pdf", "../" + names[0], names[0] + "/child"):
        with pytest.raises(ValueError):
            TempScope(org_id=org.id, dateien=[bad])
    directory.rename(directory.with_name("saved"))
    directory.symlink_to(directory.with_name("saved"), target_is_directory=True)
    with pytest.raises(ValueError, match="unzul"):
        simulate(db, action, scope)


@pytest.mark.parametrize("commit_before", [False, True])
def test_scope_limits_sealed_registry_and_readonly(repair_env, commit_before):
    from app.services.repair.registry import Action, register_action
    db, *_ = repair_env
    assert len(ACTIONS) == 3
    with pytest.raises(ValueError):
        register_action("test.high", "Test", "high", Scope, "Test")(object)
    for raw in ({"org_id": 1, "ids": list(range(1, 52))}, {"org_id": 1, "ids": [1], "sql": "DELETE"},
                {"org_id": "1", "ids": [1]}):
        with pytest.raises(ValueError):
            SearchScope.model_validate(raw)
    class Bad:
        def vorbedingungen(self, db, scope):
            pass
        def simulieren(self, db, scope):
            if commit_before:
                db.commit()
            db.execute(SystemSettings.__table__.delete())
    action = Action("test", "Test", "high", Scope, "Test", False, Bad())
    before = snapshot(db)
    with pytest.raises(PermissionError):
        simulate(db, action, Scope())
    db.rollback()
    assert snapshot(db) == before


def test_temp_reference_rechecked_immediately_before_unlink(repair_env, temp_files, monkeypatch):
    from app.services.repair import actions
    db, user, org, _ = repair_env
    directory, names = temp_files
    original = actions.orphan_state
    calls = 0
    def reference_appears(db, scope, name, directory_fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            db.add(MCPUpload(upload_id=name.split(".")[0], token_hash="x", org_id=org.id,
                             user_id=user.id, dateiname="PRIVATE", expires_at=utcnow()))
            db.flush()
        return original(db, scope, name, directory_fd)
    monkeypatch.setattr(actions, "orphan_state", reference_appears)
    action = ACTIONS["dateien.verwaiste_temp_loeschen"]
    result = action.impl.ausfuehren(db, TempScope(org_id=org.id, dateien=[names[0]]),
                                   RepairContext(1, user.id, "test"))
    assert result.geaendert == 0 and (directory / names[0]).exists()


def test_temp_cross_org_reference_also_prevents_deletion(repair_env, temp_files):
    db, user, org, _ = repair_env
    directory, names = temp_files
    other = FireDept(name="Andere Org", slug="other-repair")
    db.add(other)
    db.flush()
    db.add(MCPUpload(upload_id=uuid4().hex, token_hash="x", org_id=other.id, user_id=user.id,
                     dateiname="PRIVATE", expires_at=utcnow(), pfad=f"_mcp_uploads/{org.id}/{names[0]}"))
    db.commit()
    action = ACTIONS["dateien.verwaiste_temp_loeschen"]
    scope = TempScope(org_id=org.id, dateien=[names[0]])
    assert simulate(db, action, scope).objekte[0]["grund"] == "db_referenz"
    assert action.impl.ausfuehren(db, scope, RepairContext(1, user.id, "test")).geaendert == 0
    assert (directory / names[0]).exists()


def test_temp_replaced_after_snapshot_is_never_deleted(repair_env, temp_files, monkeypatch):
    from app.services.repair import actions
    db, user, org, _ = repair_env
    directory, names = temp_files
    original = actions.orphan_state
    calls = 0
    def replace_after_snapshot(db, scope, name, directory_fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement = directory / (uuid4().hex + ".pdf")
            replacement.write_bytes(b"not simulated")
            os.utime(replacement, (1, 1))
            os.replace(replacement, directory / name)
        return original(db, scope, name, directory_fd)
    monkeypatch.setattr(actions, "orphan_state", replace_after_snapshot)
    action = ACTIONS["dateien.verwaiste_temp_loeschen"]
    result = action.impl.ausfuehren(db, TempScope(org_id=org.id, dateien=[names[0]]),
                                   RepairContext(1, user.id, "test"))
    assert result.geaendert == 0 and result.uebersprungen == 1
    assert (directory / names[0]).read_bytes() == b"not simulated"
