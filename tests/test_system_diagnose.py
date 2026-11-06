"""Org-lose Diagnose und ausschliesslich administrative Build-Anzeige."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.models.worker_runtime import WorkerRuntime
from app.services import system_diagnose as diagnose


@pytest.fixture
def diagnose_db():
    engine = create_engine("sqlite://")
    WorkerRuntime.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _worker(db, worker_id, hostname, now, *, leader=True, expired=False):
    db.add(WorkerRuntime(
        worker_id=worker_id, hostname=hostname, pid=123, started_at=now - timedelta(minutes=1),
        heartbeat_at=now - timedelta(seconds=10),
        expires_at=now + timedelta(seconds=-1 if expired else 20),
        is_leader=leader, redis={}, loops={},
    ))
    db.flush()


@pytest.mark.parametrize("hosts, expected", [
    (["host-a"], "ok"),
    (["host-a", "host-b"], "ok"),
    (["host-a", "host-a"], "fail"),
    (["host-a", "host-b", "host-b"], "fail"),
    ([], "fail"),
])
def test_leaders_are_counted_per_host(diagnose_db, hosts, expected):
    now = datetime.now(UTC).replace(tzinfo=None)
    for index, host in enumerate(hosts):
        _worker(diagnose_db, f"worker-{index}", host, now)
    result = diagnose.worker_status(diagnose_db, now)
    assert result["status"] == expected
    assert result["checked_at"] == now
    assert sum(map(len, result["evidence"]["leaders_by_host"].values())) == len(hosts)


def test_expired_workers_are_evidence_but_not_leaders(diagnose_db):
    now = datetime.now(UTC).replace(tzinfo=None)
    _worker(diagnose_db, "active", "host", now)
    _worker(diagnose_db, "expired", "host", now, expired=True)
    result = diagnose.worker_status(diagnose_db, now)
    data = result["evidence"]
    assert result["status"] == "warn"
    assert data["worker_count"] == 1
    assert data["expired_workers"] == ["expired"]
    assert data["leader_workers"] == ["active"]
    assert data["leaders_by_host"] == {"host": ["active"]}
    assert data["heartbeat_age_s"] == {"active": 10, "expired": 10}
    assert [w.worker_id for w in diagnose.active_workers(diagnose_db, now)] == ["active"]
    assert diagnose.worker_status(diagnose_db, now + timedelta(seconds=20))["status"] == "fail"


@pytest.mark.parametrize("revision", [None, "old_revision", "head"])
def test_db_status_sqlite_is_read_only(diagnose_db, revision):
    scripts = ScriptDirectory("alembic")
    heads = sorted(scripts.get_heads())
    if revision is not None:
        diagnose_db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        for value in heads if revision == "head" else [revision]:
            diagnose_db.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": value})
        diagnose_db.commit()
    before = inspect(diagnose_db.get_bind()).get_table_names()
    result = diagnose.db_status(diagnose_db)
    assert result["status"] == ("ok" if revision == "head" else "warn")
    assert result["evidence"]["connected"] is True
    assert result["evidence"]["head_revisions"] == heads
    assert result["evidence"]["current_revisions"] == (
        heads if revision == "head" else [revision] if revision else [])
    assert isinstance(result["evidence"]["pool"], dict)
    assert inspect(diagnose_db.get_bind()).get_table_names() == before
    assert list(MigrationContext.configure(diagnose_db.connection()).get_current_heads()) == (
        heads if revision == "head" else [revision] if revision else [])


def test_db_status_has_pool_metrics(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnose.db'}")
    try:
        with Session(engine) as db:
            pool = diagnose.db_status(db)["evidence"]["pool"]
            assert set(pool) == {"size", "checkedin", "checkedout", "overflow"}
            assert all(isinstance(value, int) for value in pool.values())
    finally:
        engine.dispose()


def test_db_failure_does_not_expose_exception(diagnose_db, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("PRIVATE_DATABASE_URL", {}, Exception("SECRET_PASSWORD"))
    monkeypatch.setattr(diagnose_db, "execute", broken)
    result = diagnose.db_status(diagnose_db)
    assert result["status"] == "fail"
    assert result["evidence"]["error"] == "database_check_failed"
    assert "PRIVATE_DATABASE_URL" not in str(result)
    assert "SECRET_PASSWORD" not in str(result)


@pytest.mark.parametrize("configured, connected, expected", [
    (False, False, "warn"), (True, False, "fail"), (True, True, "ok"),
])
def test_redis_wraps_local_status(monkeypatch, configured, connected, expected):
    data = {"configured": configured, "connected": connected, "reconnects": 2}
    monkeypatch.setattr(diagnose.ws_bus, "status", lambda: data)
    result = diagnose.redis_status()
    assert result["status"] == expected
    assert result["evidence"] == data
    assert isinstance(result["checked_at"], datetime)


def test_version_info(monkeypatch):
    assert Settings.model_fields["GIT_SHA"].default == ""
    assert Settings.model_fields["BUILD_TIME"].default == ""
    for key, value in {"APP_VERSION": "test-version", "GIT_SHA": "0123456789abcdef",
                       "BUILD_TIME": "2026-10-27T12:30:00Z"}.items():
        monkeypatch.setattr(settings, key, value)
    result = diagnose.version_info()
    assert result["status"] == "ok"
    assert result["evidence"] == {"APP_VERSION": "test-version", "GIT_SHA": "0123456789abcdef",
                                  "BUILD_TIME": "2026-10-27T12:30:00Z",
                                  "build_time_quelle": "BUILD_TIME", "empfehlung": "Keine Aktion erforderlich."}
    monkeypatch.setattr(settings, "GIT_SHA", "")
    assert diagnose.version_info()["status"] == "warn"


@pytest.mark.parametrize("role_code", ["system_admin", "admin", "org_admin"])
def test_settings_version_only_for_system_admin(client, monkeypatch, role_code):
    monkeypatch.setattr(settings, "APP_VERSION", "phase0-private-version")
    monkeypatch.setattr(settings, "GIT_SHA", "0123456789abcdef")
    monkeypatch.setattr(settings, "BUILD_TIME", "2026-10-27T12:30:00Z")
    username = f"diagnose-{uuid4().hex}"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        role = db.query(Role).filter(Role.code == role_code).one()
        # System-Admins brauchen auch fuer die Versionsanzeige keine Org.
        org_id = None if role_code == "system_admin" else db.query(FireDept).first().id
        user = User(username=username, display_name=username, password_hash=hash_password("Test1234!"),
                    active=True, org_id=org_id)
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(UserRole(user_id=user_id, role_id=role.id))
        db.commit()
    try:
        client.get("/login")
        login = client.post("/login", data={"username": username, "password": "Test1234!",
                            "_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
        assert login.status_code == 302
        response = client.get("/admin/system-einstellungen", follow_redirects=False)
        if role_code == "system_admin":
            assert response.status_code == 200
            assert "Version / Commit / Build" in response.text
            assert "phase0-private-version" in response.text
            assert "0123456789ab" in response.text
            assert "0123456789abcdef" not in response.text
            assert "2026-10-27T12:30:00Z" in response.text
        else:
            assert response.status_code == 403
            assert "Version / Commit / Build" not in response.text
            assert "phase0-private-version" not in response.text
    finally:
        with SessionLocal() as db:
            set_tenant_context(db, None)
            for row in db.query(UserRole).filter(UserRole.user_id == user_id).all():
                db.delete(row)
            db.delete(db.get(User, user_id))
            db.commit()


def test_alte_abgelaufene_worker_warnen_nicht_mehr(diagnose_db, monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    _worker(diagnose_db, "active", "host", now)
    _worker(diagnose_db, "old", "host", now - timedelta(hours=25), expired=True)
    result = diagnose.worker_status(diagnose_db, now)
    assert result["status"] == "ok"
    assert "old" not in result["evidence"]["expired_workers"]
    assert "old" not in result["evidence"]["heartbeat_age_s"]


@pytest.mark.parametrize("state,expected", [("nicht_konfiguriert", "ok"), ("unknown", "warn")])
def test_service_summary_separates_unconfigured(diagnose_db, state, expected):
    from app.models.dienst_monitor import DienstStatus

    from app.models.master import OrgSettings

    OrgSettings.__table__.create(diagnose_db.get_bind())
    DienstStatus.__table__.create(diagnose_db.get_bind())
    diagnose_db.add(DienstStatus(id=1, org_id=1, key="sms_gateway", state=state))
    diagnose_db.flush()
    result = diagnose.service_summary(diagnose_db)
    assert result["status"] == expected
    assert result["evidence"]["dienste"]["sms_gateway"][state] == 1


@pytest.mark.parametrize("build", ["", "unknown", "invalid"])
def test_missing_build_time_warns_with_labelled_fallback(monkeypatch, build):
    monkeypatch.setattr(diagnose.update_service, "is_docker", lambda: True)
    def unexpected_git_read():
        pytest.fail("Docker darf keine Git-Commit-Zeit verwenden")
    monkeypatch.setattr(diagnose, "_git_commit_time", unexpected_git_read)
    monkeypatch.setattr(settings, "BUILD_TIME", build)
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    result = diagnose.version_info()
    assert result["status"] == "warn"
    evidence = result["evidence"]
    assert evidence["build_time_quelle"] == "Image-Datei-mtime (kein Buildnachweis)"
    assert datetime.fromisoformat(evidence["BUILD_TIME"]).tzinfo is not None
    assert "compose pull" in evidence["empfehlung"] and "--build-arg BUILD_TIME" in evidence["empfehlung"]


@pytest.fixture
def git_time_cache():
    diagnose._git_commit_time.cache_clear()
    yield
    diagnose._git_commit_time.cache_clear()


@pytest.mark.parametrize("build", ["", "unknown"])
def test_git_commit_time_is_build_evidence_and_cached(monkeypatch, git_time_cache, build):
    calls = []
    def git_log(args, timeout):
        calls.append((args, timeout))
        return 0, "2026-10-27T13:30:00+01:00", ""
    monkeypatch.setattr(diagnose.update_service, "is_docker", lambda: False)
    monkeypatch.setattr(diagnose.update_service, "is_git_checkout", lambda: True)
    monkeypatch.setattr(diagnose.update_service, "_run_git", git_log)
    monkeypatch.setattr(settings, "BUILD_TIME", build)
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    for _ in range(2):
        result = diagnose.version_info()
        assert result["status"] == "ok"
        assert result["evidence"]["BUILD_TIME"] == "2026-10-27T12:30:00Z"
        assert result["evidence"]["build_time_quelle"] == "Git-Commit-Zeit (HEAD)"
        assert result["evidence"]["empfehlung"] == "Keine Aktion erforderlich."
    assert calls == [(["log", "-1", "--format=%cI", "HEAD"], 2)]
    monkeypatch.setattr(settings, "GIT_SHA", "unknown")
    assert diagnose.version_info()["status"] == "warn"


@pytest.mark.parametrize("git_result", [(1, "", "git fehlt"), (0, "ungueltig", "")])
def test_git_read_failure_uses_file_fallback(monkeypatch, git_time_cache, git_result):
    monkeypatch.setattr(diagnose.update_service, "is_docker", lambda: False)
    monkeypatch.setattr(diagnose.update_service, "is_git_checkout", lambda: True)
    monkeypatch.setattr(diagnose.update_service, "_run_git", lambda *args, **kwargs: git_result)
    monkeypatch.setattr(settings, "BUILD_TIME", "unknown")
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    result = diagnose.version_info()
    assert result["status"] == "warn"
    evidence = result["evidence"]
    assert evidence["build_time_quelle"] == "Datei-mtime (kein Buildnachweis)"
    assert datetime.fromisoformat(evidence["BUILD_TIME"]).tzinfo is not None
    assert "BUILD_TIME" in evidence["empfehlung"]
    assert "compose" not in evidence["empfehlung"]


def test_non_git_installation_uses_file_fallback(monkeypatch):
    monkeypatch.setattr(diagnose.update_service, "is_docker", lambda: False)
    monkeypatch.setattr(diagnose.update_service, "is_git_checkout", lambda: False)
    monkeypatch.setattr(settings, "BUILD_TIME", "")
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    result = diagnose.version_info()
    assert result["status"] == "warn"
    assert result["evidence"]["build_time_quelle"] == "Datei-mtime (kein Buildnachweis)"
    assert "UTC-ISO-Zeitpunkt" in result["evidence"]["empfehlung"]
    assert "compose" not in result["evidence"]["empfehlung"]


def test_build_falls_back_to_process_start(monkeypatch):
    from pathlib import Path
    real_stat = Path.stat
    def missing_image_file(path, *args, **kwargs):
        if path == Path(diagnose.__file__):
            raise OSError("image file metadata unavailable")
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(settings, "BUILD_TIME", "unknown")
    monkeypatch.setattr(diagnose.update_service, "is_docker", lambda: True)
    monkeypatch.setattr(Path, "stat", missing_image_file)
    evidence = diagnose.version_info()["evidence"]
    assert evidence["build_time_quelle"] == "Prozessstart (kein Buildnachweis)"
    stamp = datetime.fromisoformat(evidence["BUILD_TIME"])
    assert stamp.tzinfo is not None and stamp <= datetime.now(UTC)


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
def test_gateway_summary_ueberwacht_nur_aktivierte_gateways(tmp_path, kind):
    from app.db import Base
    from app.services.diagnose.helpers import table
    from tests.test_diagnose_checks import insert

    engine = create_engine(f"sqlite:///{tmp_path / 'gateways.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.info["current_org_id"] = None
            optional = insert(db, "gateway", kind=kind, device_token_hash="optional", offline_alert_enabled=False)
            result = diagnose.gateway_summary(db)
            assert result["status"] == "ok"
            assert result["evidence"][kind] == {"eingerichtet": 1, "online": 0, "nicht_ueberwacht": 1}
            # Ein optionales Online-Gateway darf einen ueberwachten Ausfall nicht verdecken.
            source = table("gateway")
            db.execute(source.update().where(source.c.id == optional).values(
                agent_connection_id="c", agent_connection_token_hash="optional",
                agent_presence_expires_at=diagnose.presence.utc() + timedelta(minutes=5)))
            db.commit()
            monitored = insert(db, "gateway", kind=kind, device_token_hash="monitored", offline_alert_enabled=True)
            result = diagnose.gateway_summary(db)
            assert result["status"] == "warn"
            assert result["evidence"][kind] == {"eingerichtet": 2, "online": 1, "nicht_ueberwacht": 1}
            db.execute(source.update().where(source.c.id == monitored).values(offline_alert_enabled=False))
            db.commit()
            assert diagnose.gateway_summary(db)["status"] == "ok"
            insert(db, "print_job", gateway_id=optional, status="queued",
                   erstellt_am=diagnose.presence.utc() - timedelta(minutes=10), lease_expires_at=None)
            assert diagnose.gateway_summary(db)["status"] == "warn"
    finally:
        engine.dispose()
