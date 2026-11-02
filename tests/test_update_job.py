"""Hintergrund-Updates ohne echte Git-, pip-, systemctl- oder Alembic-Aufrufe."""

import hashlib
import json
import os
import signal
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services import update_job as job
from app.services import update_service as us

RELOAD_SERVER = us._reload_server
AUDIT = job._audit
ALIVE = job._alive


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)
    monkeypatch.setattr(us, "is_docker", lambda: False)
    monkeypatch.setattr(settings, "GUNICORN_PID_FILE", "")
    monkeypatch.setattr(job, "_alive", lambda pid: bool(pid))
    monkeypatch.setattr(job, "_audit", MagicMock())
    monkeypatch.setattr(us, "get_github_token", lambda db: "private-test-token")
    monkeypatch.setattr(job, "SessionLocal", MagicMock())
    monkeypatch.setattr(job.bs, "aktueller_alembic_head", lambda: "head-new")
    monkeypatch.setattr(us, "_reload_server", lambda: True)
    monkeypatch.setattr(job.subprocess, "Popen", MagicMock(return_value=MagicMock(pid=777)))
    return tmp_path


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "input.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("app/new.py", "new")
        zf.writestr("pyproject.toml", 'version = "2"')
    return path


def start(archive):
    return job.start_job("zip", "ZIP", 12, zip_path=archive, install_deps=True)


def test_queued_private_files_and_argv(archive, environment):
    status = start(archive)
    assert len(status["job_id"]) == 32 and status["state"] == "queued"
    assert status["started_at"].endswith("Z")
    root = job.directory()
    for suffix in ("zip", "params.json", "log"):
        assert (root / f"{status['job_id']}.{suffix}").stat().st_mode & 0o777 == 0o600
    assert (root / "status.json").stat().st_mode & 0o777 == 0o600
    argv = job.subprocess.Popen.call_args.args[0]
    assert argv[1:] == ["-m", "app.cli", "update-run", status["job_id"]]
    kwargs = job.subprocess.Popen.call_args.kwargs
    assert kwargs["start_new_session"] and kwargs["close_fds"]
    assert kwargs["stdout"] is kwargs["stderr"]
    assert "env" not in kwargs  # Server-Umgebung unverändert erben
    for path in root.glob("*.json"):
        assert "private-test-token" not in path.read_text()
    assert "install_deps" not in (root / "status.json").read_text()
    assert "expected_sha256" not in (root / "status.json").read_text()
    assert str(archive) not in " ".join(argv)


def test_job_lifecycle_progress_and_cleanup(archive, monkeypatch):
    status = start(archive)
    params = job.directory() / f"{status['job_id']}.params.json"
    zip_path = job.directory() / f"{status['job_id']}.zip"

    def apply(path, *, expected_sha256, install_deps, progress):
        assert path == zip_path and path.stat().st_mode & 0o777 == 0o600
        assert not params.exists() and install_deps
        assert job._read()["state"] == "running"
        for step in job.STEPS[:-1]:
            progress(step, "running")
            assert job._read()["steps"][job.STEPS.index(step)]["status"] == "running"
            progress(step, "success")
        print("private-test-token")  # Auch Bibliotheksausgaben nicht ins Log übernehmen.
        return {"success": True, "message": "private-test-token", "token": "private-test-token"}

    def reload():
        persisted = job._read()
        assert persisted["state"] == "restarting"
        assert persisted["expected_head"] == "head-new"
        assert persisted["expected_version"] == "unbekannt"
        assert not zip_path.exists() and not params.exists()
        return True

    monkeypatch.setattr(us, "apply_update", apply)
    monkeypatch.setattr(us, "_reload_server", reload)
    job.run_job(status["job_id"])
    current = job.get_status()
    assert current["state"] == "restarting"
    assert [step["status"] for step in current["steps"]] == ["success"] * 4 + ["running"]
    assert all(step["started_at"] for step in current["steps"])
    assert "private-test-token" not in (job.directory() / "status.json").read_text()
    assert "private-test-token" not in (job.directory() / f"{status['job_id']}.log").read_text()


@pytest.mark.parametrize("failure", ["result", "exception", "reload", "params"])
def test_failure_cleanup(archive, monkeypatch, failure):
    status = start(archive)
    if failure == "params":
        (job.directory() / f"{status['job_id']}.params.json").write_text("invalid")
    def apply(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("private-test-token")
        return {"success": failure != "result", "message": "Fehler private-test-token"}
    monkeypatch.setattr(us, "apply_update", apply)
    monkeypatch.setattr(us, "_reload_server", lambda: False)
    job.run_job(status["job_id"])
    current = job.get_status()
    assert current["state"] == "failed" and current["finished_at"]
    if failure == "reload":
        assert us.RELOAD_ERROR in current["message"]
        assert "Code und Migration sind bereits eingespielt" in current["message"]
    assert not list(job.directory().glob("*.zip"))
    assert not list(job.directory().glob("*.params.json"))
    assert "private-test-token" not in json.dumps(current)


@pytest.mark.parametrize("state", ["queued", "running", "restarting", "verifying"])
def test_only_one_job(archive, state):
    current = start(archive)
    current["state"] = state
    job._save(current)
    with pytest.raises(ValueError, match="bereits in Arbeit"):
        start(archive)
    assert job.subprocess.Popen.call_count == 1


def test_orphan_failed(archive, monkeypatch):
    current = start(archive)
    current["state"] = "running"
    job._save(current)
    monkeypatch.setattr(job, "_alive", lambda pid: False)
    assert job.get_status()["state"] == "failed"
    assert job.get_status()["message"] == "Prozess unerwartet beendet"
    assert not list(job.directory().glob("*.zip"))
    assert start(archive)["job_id"] != current["job_id"]


def test_existing_service_lock_prevents_start(archive):
    with us._update_lock() as acquired:
        assert acquired
        with pytest.raises(ValueError, match="bereits in Arbeit"):
            start(archive)
    job.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("kind,target", [("bad", "main"), ("branch", "--main"), ("release", "../x")])
def test_invalid_parameters(kind, target):
    with pytest.raises(ValueError):
        job.start_job(kind, target, 12)
    job.subprocess.Popen.assert_not_called()


def test_hash_and_zip_validation(archive):
    for digest in ("invalid", "a" * 64):
        with pytest.raises(ValueError, match="SHA256"):
            job.start_job("zip", "ZIP", 12, zip_path=archive, expected_sha256=digest)
    archive.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="ZIP"):
        start(archive)
    job.subprocess.Popen.assert_not_called()


def test_docker_guard(monkeypatch):
    monkeypatch.setattr(us, "is_docker", lambda: True)
    with pytest.raises(ValueError, match="docker compose"):
        job.start_job("branch", "main", 12)
    job.subprocess.Popen.assert_not_called()


def test_spawn_failure_cleanup(archive, monkeypatch):
    monkeypatch.setattr(job.subprocess, "Popen", MagicMock(side_effect=OSError("secret")))
    assert start(archive)["state"] == "failed"
    assert not list(job.directory().glob("*.zip"))
    assert not list(job.directory().glob("*.params.json"))


def test_real_service_progress_order(archive, monkeypatch):
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {"success": True, "required": True, "files": []})
    monkeypatch.setattr(us, "_alembic_state", lambda command: "head-new")
    monkeypatch.setattr(us, "_run_pip_install", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_run_migrations", lambda: (True, "OK"))
    reloader = MagicMock()
    monkeypatch.setattr(us, "_reload_server", reloader)
    steps = []
    assert us.apply_update(archive, progress=lambda *args: steps.append(args))["success"]
    assert steps == [(name, state) for name in job.STEPS[:-1] for state in ("running", "success")]
    reloader.assert_not_called()


def _reloader(monkeypatch):
    monkeypatch.setattr(us, "_reload_server", RELOAD_SERVER)
    return us


@pytest.mark.parametrize("configured", [True, False])
def test_reload_sighup(monkeypatch, tmp_path, configured):
    module = _reloader(monkeypatch)
    path = tmp_path / ("configured.pid" if configured else "gunicorn.pid")
    path.write_text("4321")
    if configured:
        monkeypatch.setattr(settings, "GUNICORN_PID_FILE", str(path))
    kill = MagicMock()
    monkeypatch.setattr(module.os, "kill", kill)
    runner = MagicMock()
    monkeypatch.setattr(module.subprocess, "run", runner)
    assert module._reload_server()
    assert kill.call_args_list[-1].args == (4321, signal.SIGHUP)
    runner.assert_not_called()


@pytest.mark.parametrize("content", ["-1", "invalid", "4321", "missing"])
@pytest.mark.parametrize("returncode", [0, 1])
def test_reload_sudo_n_and_failure(monkeypatch, tmp_path, content, returncode):
    module = _reloader(monkeypatch)
    if content != "missing":
        (tmp_path / "gunicorn.pid").write_text(content)
    monkeypatch.setattr(module.os, "kill", MagicMock(side_effect=ProcessLookupError))
    runner = MagicMock(return_value=subprocess.CompletedProcess([], returncode))
    monkeypatch.setattr(module.subprocess, "run", runner)
    assert module._reload_server() == (returncode == 0)
    assert runner.call_args.args[0] == ["sudo", "-n", "systemctl", "restart", "einsatzleiter"]
    assert runner.call_args.kwargs["timeout"] == 15


def restarting(archive, monkeypatch, *, seconds=0):
    status = start(archive)
    status.update(state="restarting", server_pid=os.getpid() + 1,
                  restart_started_at=(datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(),
                  expected_head="head-new", expected_sha="sha-new")
    job._save(status)
    monkeypatch.setattr(job, "_server_started_after", lambda *args: True)
    db = job.SessionLocal.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value = ["head-new"]
    monkeypatch.setattr(us, "_run_git", lambda args: (0, "sha-new", ""))
    return status, db


def test_verify_success(archive, monkeypatch):
    restarting(archive, monkeypatch)
    job.verify_after_restart()
    status = job.get_status()
    assert status["state"] == "success" and status["result"]["server_reloaded"]
    assert status["finished_at"]
    job._audit.assert_called_once()


@pytest.mark.parametrize("mismatch", ["db", "head", "git", "pid", "old-worker", "db-error"])
def test_verify_mismatch_and_timeout(archive, monkeypatch, mismatch):
    status, db = restarting(archive, monkeypatch, seconds=settings.UPDATE_VERIFY_TIMEOUT_S + 1)
    if mismatch == "db":
        db.execute.return_value.scalars.return_value = ["head-old"]
    elif mismatch == "head":
        monkeypatch.setattr(job.bs, "aktueller_alembic_head", lambda: "head-other")
    elif mismatch == "git":
        monkeypatch.setattr(us, "_run_git", lambda args: (0, "sha-old", ""))
    elif mismatch == "pid":
        status["server_pid"] = os.getpid()
        job._save(status)
    elif mismatch == "old-worker":
        monkeypatch.setattr(job, "_server_started_after", lambda *args: False)
    else:
        db.execute.side_effect = RuntimeError("DB unavailable")
    job.verify_after_restart()
    current = job.get_status()
    assert current["state"] == "unconfirmed"
    assert "manuell prüfen" in current["message"]


def test_verify_pending_and_startup_readonly(archive, monkeypatch):
    _, db = restarting(archive, monkeypatch)
    db.execute.return_value.scalars.return_value = ["old"]
    job.verify_after_restart(audit=False)
    assert job.get_status()["state"] == "verifying"
    db.execute.return_value.scalars.return_value = ["head-new"]
    job.verify_after_restart(audit=False)
    assert job.get_status()["state"] == "success"
    job._audit.assert_not_called()
    db.commit.assert_not_called()
    job.verify_after_restart()
    job._audit.assert_called_once()


def test_start_time_check():
    assert job._server_started_after((datetime.now(UTC) - timedelta(days=1)).isoformat())
    assert not job._server_started_after((datetime.now(UTC) + timedelta(days=1)).isoformat())


@pytest.mark.parametrize("kind", ["release", "branch"])
def test_github_token_loaded_in_child(monkeypatch, kind):
    status = job.start_job(kind, "v2" if kind == "release" else "main", 12)
    assert "private-test-token" not in repr(job.subprocess.Popen.call_args)
    monkeypatch.setattr(us, "_github_json", lambda *args: {"tag_name": "v2", "zipball_url": "url", "sha": "abcdef"})
    deploy = MagicMock(return_value={"success": False, "message": "private-test-token"})
    monkeypatch.setattr(us, "deploy_github_" + kind, deploy)
    job.run_job(status["job_id"])
    assert deploy.call_args.kwargs["token"] == "private-test-token"
    assert "private-test-token" not in json.dumps(job.public_status())


def login(client, name, role="system_admin"):
    from tests.test_ui_db_backup import _benutzer_anlegen, _login
    _benutzer_anlegen(name, role)
    return _login(client, name)


@pytest.mark.parametrize("kind", ["zip", "release", "branch"])
def test_post_starts_job_without_deploy(client, archive, monkeypatch, kind):
    from app.routers import ui_settings as ui
    csrf = login(client, "job-post-" + kind)
    monkeypatch.setattr(ui, "is_docker", lambda: False)
    monkeypatch.setattr(ui, "check_github_release", lambda **kwargs: {"tag_name": "v2", "download_url": "url"})
    monkeypatch.setattr(ui, "validate_release_tag", lambda *args, **kwargs: None)
    monkeypatch.setattr(ui, "check_github_branch", lambda *args, **kwargs:
                        {"sha": "abcdef", "sha_short": "abc", "download_url": "url"})
    deploy = MagicMock(side_effect=AssertionError("Deploy im Request"))
    monkeypatch.setattr(us, "apply_update", deploy)
    monkeypatch.setattr(us, "deploy_github_release", deploy)
    monkeypatch.setattr(us, "deploy_github_branch", deploy)
    data = {"_csrf": csrf, "tag_name": "v2", "branch": "main"}
    url = "/admin/system/update"
    kwargs = {}
    if kind == "zip":
        content = archive.read_bytes()
        kwargs["files"] = {"release_zip": ("release.zip", content)}
        data["expected_sha256"] = hashlib.sha256(content).hexdigest()
    else:
        url += "/github" if kind == "release" else "/github-branch"
    response = client.post(url, data=data, headers={"HX-Request": "true"}, **kwargs)
    assert response.status_code == 200, response.text
    assert "every 2s" in response.text and "Update eingereiht" in response.text
    job.subprocess.Popen.assert_called_once()
    deploy.assert_not_called()
    status = client.get("/admin/system/update/status").json()["job"]
    assert status["kind"] == kind and "private-test-token" not in json.dumps(status)
    assert "every 2s" in client.get("/admin/system/update").text


@pytest.mark.parametrize("role", ["admin", "org_admin", "readonly"])
def test_route_system_admin_only(client, role):
    csrf = login(client, "job-forbidden-" + role, role)
    assert client.get("/admin/system/update/status").status_code == 403
    assert client.post("/admin/system/update/github-branch", data={"_csrf": csrf}).status_code == 403
    job.subprocess.Popen.assert_not_called()


def test_status_html_json_terminal_and_log_tail(client, archive):
    login(client, "job-status")
    status = start(archive)
    status.update(state="failed", message="Fehler", result={"success": False})
    job._save(status)
    log = job.directory() / f"{status['job_id']}.log"
    log.write_text("\n".join(f"Zeile {i}" for i in range(60)) + "\nghp_privateSecret")
    response = client.get("/admin/system/update/status")
    assert response.status_code == 200
    value = response.json()["job"]
    assert len(value["log"].splitlines()) == 40 and "Zeile 0\n" not in value["log"]
    assert "ghp_privateSecret" not in response.text
    partial = client.get("/admin/system/update/status", headers={"HX-Request": "true"})
    assert "every 2s" not in partial.text and "Update-Fortschritt" in partial.text
    assert "ghp_privateSecret" not in partial.text
    assert "Update-Fortschritt" in client.get("/admin/system/update/status", headers={"Accept": "text/html"}).text


def test_completion_audit_once(archive, monkeypatch):
    status = start(archive)
    status.update(state="success", expected_sha="abcdef")
    monkeypatch.setattr(job, "_audit", AUDIT)
    audit = MagicMock()
    monkeypatch.setattr(job, "write_audit", audit)
    job._audit(status)
    job._audit(status)
    audit.assert_called_once()
    assert audit.call_args.args[1] == "system.update_job"
    assert audit.call_args.kwargs["payload"] == {
        "job_id": status["job_id"], "kind": "zip", "ziel": "ZIP", "state": "success"}
    job.SessionLocal.return_value.__enter__.return_value.commit.assert_called_once()


def test_duplicate_route_keeps_polling(client, archive):
    csrf = login(client, "job-duplicate")
    start(archive)
    response = client.post("/admin/system/update", headers={"HX-Request": "true"},
                           data={"_csrf": csrf, "expected_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
                           files={"release_zip": ("release.zip", archive.read_bytes())})
    assert response.status_code == 409 and "Update bereits in Arbeit" in response.text
    assert "every 2s" in response.text and "before-swap" in response.text
    assert 'id="update-status"' in response.text
    job.subprocess.Popen.assert_called_once()


def test_missing_git_sha_cannot_succeed(archive, monkeypatch):
    status = start(archive)
    monkeypatch.setattr(us, "apply_update", lambda *args, **kwargs: {"success": True})
    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    monkeypatch.setattr(us, "_run_git", lambda args: (1, "", "secret"))
    reload = MagicMock()
    monkeypatch.setattr(us, "_reload_server", reload)
    job.run_job(status["job_id"])
    assert job.get_status()["state"] == "failed"
    assert "Git-Zielstand nicht lesbar" in job.get_status()["message"]
    reload.assert_not_called()


@pytest.mark.parametrize("state,expected", [("R", True), ("S", True), ("Z", False), ("X", False)])
def test_dead_process_including_zombie(monkeypatch, state, expected):
    monkeypatch.setattr(job.os, "kill", MagicMock())
    monkeypatch.setattr(job.Path, "read_text", lambda path: f"123 (worker name) {state} 1 2 3")
    assert ALIVE(123) is expected


def test_missing_process_is_dead(monkeypatch):
    monkeypatch.setattr(job.os, "kill", MagicMock(side_effect=ProcessLookupError))
    assert not ALIVE(123)
