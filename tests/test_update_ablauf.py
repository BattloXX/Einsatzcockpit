"""Sicherer Update-Ablauf mit ZIP-Dateien und echtem lokalem Git-Remote."""
import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import Request, UploadFile

from app.config import settings
from app.services import update_service as us


@pytest.fixture(autouse=True)
def isolated_root(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)


@pytest.fixture
def host(monkeypatch, tmp_path):
    root = tmp_path / "installation"
    (root / "app").mkdir(parents=True)
    (root / "app/main.py").write_text("alt")
    (root / "pyproject.toml").write_text("alt-projekt")
    monkeypatch.setattr(us, "APP_ROOT", root)
    monkeypatch.setattr(us, "is_docker", lambda: False)
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": True, "required": True, "message": "OK", "files": ["/backups/pre-update.sql.gz"]})
    monkeypatch.setattr(us, "_run_pip_install", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_run_migrations", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_alembic_state", lambda command: "revision_alt" if command == "current" else "revision_neu")
    monkeypatch.setattr(us, "_reload_server", lambda: True)
    return root


def archive(tmp_path, project="neu-projekt"):
    path = tmp_path / "release.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in {
            "app/main.py": "neu", "app/neu.py": "neu", "pyproject.toml": project,
            "alembic/versions/999_neu.py": "neue Migration", ".env": "secret",
        }.items():
            zf.writestr("release/" + name, content)
    return path


def test_zip_reihenfolge_und_neue_migration(host, monkeypatch, tmp_path):
    events = []
    backup = us._run_pre_migration_backup
    def sichern():
        assert (host / "app/main.py").read_text() == "alt"
        assert not (host / ".update-rollback").exists()
        events.append("backup")
        return backup()
    def pip():
        assert (host / "app/main.py").read_text() == "neu"
        assert (host / "alembic/versions/999_neu.py").exists()
        events.append("pip")
        return True, "OK"
    monkeypatch.setattr(us, "_run_pre_migration_backup", sichern)
    monkeypatch.setattr(us, "_run_pip_install", pip)
    monkeypatch.setattr(us, "_run_migrations", lambda: (events.append("migration") or True, "OK"))
    monkeypatch.setattr(us, "_reload_server", lambda: events.append("reload") or True)
    result = us.apply_update(archive(tmp_path))
    assert result["success"]
    assert events == ["backup", "pip", "migration", "reload"]
    assert result["migrations_from"] == "revision_alt"
    assert result["migrations_to"] == "revision_neu"
    assert not (host / ".env").exists()
    saved = Path(result["rollback_dir"])
    assert (saved / "files/app/main.py").read_text() == "alt"
    assert not (saved / "files/app/neu.py").exists()
    assert "app/neu.py" in json.loads((saved / "manifest.json").read_text())["created"]


def test_backup_fehler_veraendert_keinen_code(host, monkeypatch, tmp_path):
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": False, "required": True, "message": "Dump defekt", "files": []})
    pip = MagicMock()
    monkeypatch.setattr(us, "_run_pip_install", pip)
    result = us.apply_update(archive(tmp_path))
    assert not result["success"]
    assert "Code unverändert" in result["message"]
    assert (host / "app/main.py").read_text() == "alt"
    assert not (host / "app/neu.py").exists()
    assert not (host / ".update-rollback").exists()
    pip.assert_not_called()


@pytest.mark.parametrize("invalid", ["hash", "zip"])
def test_vorpruefung_vor_backup(host, monkeypatch, tmp_path, invalid):
    backup = MagicMock()
    monkeypatch.setattr(us, "_run_pre_migration_backup", backup)
    path = archive(tmp_path)
    if invalid == "zip":
        path.write_bytes(b"kein ZIP")
    result = us.apply_update(path, expected_sha256="0" * 64)
    assert not result["success"]
    backup.assert_not_called()
    assert (host / "app/main.py").read_text() == "alt"


@pytest.mark.parametrize("failure", ["pip", "migration"])
def test_zip_rollback_entfernt_neue_dateien(host, monkeypatch, tmp_path, failure):
    reloader = MagicMock()
    monkeypatch.setattr(us, "_reload_server", reloader)
    monkeypatch.setattr(us, "_run_pip_install" if failure == "pip" else "_run_migrations", lambda: (False, "kaputt"))
    result = us.apply_update(archive(tmp_path))
    assert not result["success"]
    assert "wiederhergestellt" in result["message"]
    assert "/backups/pre-update.sql.gz" in result["message"]
    assert (host / "app/main.py").read_text() == "alt"
    assert (host / "pyproject.toml").read_text() == "alt-projekt"
    assert not (host / "app/neu.py").exists()
    assert not (host / "alembic/versions/999_neu.py").exists()
    if failure == "migration":
        assert "MariaDB-DDL" in result["message"]
    reloader.assert_not_called()


@pytest.mark.parametrize("after", ["revision_neu", None])
def test_zip_kein_rollback_nach_revisionen_oder_unbekanntem_stand(host, monkeypatch, tmp_path, after):
    states = iter(["revision_alt", "revision_neu", after])
    monkeypatch.setattr(us, "_alembic_state", lambda command: next(states))
    monkeypatch.setattr(us, "_run_migrations", lambda: (False, "DDL fehlgeschlagen"))
    result = us.apply_update(archive(tmp_path))
    assert not result["success"]
    assert "Kein automatischer Code-Rollback" in result["message"]
    assert "DB-Restore" in result["message"]
    assert "/backups/pre-update.sql.gz" in result["message"]
    assert (host / "app/main.py").read_text() == "neu"


@pytest.mark.parametrize("project,explicit,called", [("alt-projekt", False, False),
                                                     ("neu-projekt", False, True),
                                                     ("alt-projekt", True, True)])
def test_zip_pip_trigger(host, monkeypatch, tmp_path, project, explicit, called):
    pip = MagicMock(return_value=(True, "OK"))
    monkeypatch.setattr(us, "_run_pip_install", pip)
    assert us.apply_update(archive(tmp_path, project), install_deps=explicit)["success"]
    assert pip.called == called


def test_zip_rollback_retention(host, tmp_path):
    path = archive(tmp_path)
    dirs = []
    for _ in range(5):
        result = us.apply_update(path)
        assert result["success"]
        dirs.append(Path(result["rollback_dir"]))
    assert set((host / ".update-rollback").iterdir()) == set(dirs[-3:])


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def local_git(host, tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(host, "init", "-b", "main")
    git(host, "config", "user.email", "update@example.invalid")
    git(host, "config", "user.name", "Update Test")
    git(host, "config", "url." + str(remote) + ".insteadOf", "https://github.com/" + us.GITHUB_REPO + ".git")
    git(host, "add", "app", "pyproject.toml")
    git(host, "commit", "-m", "Ausgangsstand")
    before = git(host, "rev-parse", "HEAD")
    git(host, "checkout", "-b", "feature/test")
    (host / "app/main.py").write_text("feature")
    git(host, "commit", "-am", "Feature")
    after = git(host, "rev-parse", "HEAD")
    git(host, "tag", "v2.0")
    git(host, "push", str(remote), "main", "feature/test", "v2.0")
    git(host, "checkout", "main")
    return host, before, after


def test_git_branch_main_bleibt_unveraendert(local_git):
    root, before, after = local_git
    result = us.git_update("feature/test")
    assert result["success"], result
    assert git(root, "rev-parse", "main") == before
    assert git(root, "rev-parse", "HEAD") == after
    assert git(root, "symbolic-ref", "--short", "HEAD") == "feature/test"


def test_git_backup_gate_kein_checkout(local_git, monkeypatch):
    root, before, _ = local_git
    commands = []
    real = us._run_git
    def run(args, **kwargs):
        commands.append(args)
        return real(args, **kwargs)
    monkeypatch.setattr(us, "_run_git", run)
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": False, "required": True, "message": "kaputt", "files": []})
    result = us.git_update("feature/test")
    assert not result["success"]
    assert not any(cmd[0] == "checkout" for cmd in commands)
    assert git(root, "rev-parse", "HEAD") == before
    assert git(root, "symbolic-ref", "--short", "HEAD") == "main"


@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("failure", ["pip", "migration"])
def test_git_fehler_stellt_sha_und_head_zustand_wieder_her(local_git, monkeypatch, detached, failure):
    root, before, _ = local_git
    if detached:
        git(root, "checkout", "--detach", before)
    monkeypatch.setattr(us, "_run_pip_install" if failure == "pip" else "_run_migrations", lambda: (False, "kaputt"))
    result = us.git_update("feature/test", install_deps=True)
    assert not result["success"]
    assert git(root, "rev-parse", "HEAD") == before
    symbolic = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True)
    assert (symbolic.returncode != 0) == detached
    if not detached:
        assert symbolic.stdout.strip() == "main"


def test_git_release_detached(local_git):
    root, before, after = local_git
    assert us.git_update_release("v2.0")["success"]
    assert git(root, "rev-parse", "HEAD") == after
    assert git(root, "rev-parse", "main") == before
    assert subprocess.run(["git", "-C", str(root), "symbolic-ref", "-q", "HEAD"]).returncode != 0


def test_git_keine_rueckkehr_nach_angewendeten_revisionen(local_git, monkeypatch):
    root, _, after = local_git
    states = iter(["old", "new", "new"])
    monkeypatch.setattr(us, "_alembic_state", lambda command: next(states))
    monkeypatch.setattr(us, "_run_migrations", lambda: (False, "DDL"))
    result = us.git_update("feature/test")
    assert not result["success"]
    assert git(root, "rev-parse", "HEAD") == after
    assert "Kein automatischer Code-Rollback" in result["message"]


@pytest.mark.parametrize("ref", ["-main", "--upload-pack=x", "main--x", "a b", "a..b", "a/../b", "a.lock", "a//b"])
def test_ungueltige_refs_ohne_fetch(host, monkeypatch, ref):
    api = MagicMock()
    monkeypatch.setattr(us, "_github_json", api)
    assert "error" in us.check_github_branch(ref)
    assert not us.git_update(ref)["success"]
    assert not us.deploy_github_branch(ref, "url")["success"]
    assert not us.deploy_github_release("url", ref)["success"]
    api.assert_not_called()


def test_branch_url_pfad_bleibt_lesbar(host, monkeypatch):
    api = MagicMock(return_value={"sha": "abc"})
    monkeypatch.setattr(us, "_github_json", api)
    result = us.check_github_branch("feature/test")
    assert api.call_args.args[0].endswith("/commits/feature/test")
    assert result["download_url"].endswith("/zipball/feature/test")
    dl = MagicMock(return_value={"success": True})
    monkeypatch.setattr(us, "download_and_apply_github_update", dl)
    us.deploy_github_branch("feature/test", "untrusted")
    assert dl.call_args.args[0].endswith("/zipball/feature/test")


def test_token_nur_im_env_nie_argv(local_git, monkeypatch):
    real = subprocess.run
    commands = []
    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        return real(argv, **kwargs)
    monkeypatch.setattr(us.subprocess, "run", run)
    assert us.git_update("feature/test", token="geheim-token")["success"]
    encoded = base64.b64encode(b"x-access-token:geheim-token").decode()
    for argv, _ in commands:
        assert "geheim-token" not in " ".join(argv)
        assert encoded not in " ".join(argv)
    fetch = next(kwargs for argv, kwargs in commands if "fetch" in argv)
    assert fetch["env"]["GIT_CONFIG_VALUE_0"] == "AUTHORIZATION: basic " + encoded
    assert fetch["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"


def test_migration_timeout_und_traceback_ende(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)
    monkeypatch.setattr(settings, "UPDATE_MIGRATION_TIMEOUT_S", 1234)
    def run(argv, **kwargs):
        assert kwargs["timeout"] == 1234
        return subprocess.CompletedProcess(argv, 1, "", "Anfang" + "x" * 900 + "ENDE")
    monkeypatch.setattr(us.subprocess, "run", run)
    ok, msg = us._run_migrations()
    assert not ok
    assert msg.endswith("ENDE") and "Anfang" not in msg
    assert len(msg.removeprefix("Fehler: ")) == 800


def test_alembic_state_vergleicht_nur_revision_ids(monkeypatch):
    monkeypatch.setattr(us.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, "abc (head)\ndef (branchpoint)\n", ""))
    assert us._alembic_state("current") == "abc\ndef"


@pytest.mark.parametrize("marker,env", [(True, ""), (False, "docker"), (False, "")])
def test_docker_erkennung(monkeypatch, marker, env):
    monkeypatch.setattr(Path, "exists", lambda p: marker if str(p) == "/.dockerenv" else False)
    monkeypatch.setenv("EC_RUNTIME", env)
    assert us.is_docker() == (marker or env == "docker")


def test_docker_alle_deploys_ohne_io(monkeypatch):
    monkeypatch.setattr(us, "is_docker", lambda: True)
    io_call = MagicMock(side_effect=AssertionError("Unerwartete I/O"))
    monkeypatch.setattr(us, "_run_git", io_call)
    monkeypatch.setattr(us, "_run_pre_migration_backup", io_call)
    for fn, args in [(us.apply_update, (Path("fehlt"),)), (us.git_update, ("main",)),
                     (us.git_update_release, ("v2",)), (us.deploy_github_branch, ("main", "url")),
                     (us.deploy_github_release, ("url", "v2")), (us.download_and_apply_github_update, ("url",))]:
        result = fn(*args)
        assert not result["success"]
        assert "docker compose pull && docker compose up -d" in result["message"]
    io_call.assert_not_called()


def test_release_drafts_uebersprungen(monkeypatch):
    monkeypatch.setattr(us, "_github_json", lambda *a, **kw: [
        {"tag_name": "v3", "draft": True}, {"tag_name": "v2", "prerelease": True}])
    result = us.check_github_release(prerelease=True)
    assert result["tag_name"] == "v2"


@pytest.mark.parametrize("actual,draft,error", [("v2", False, False), ("v3", False, True),
                                               ("v2", True, True), (None, False, True)])
def test_release_tag_abgleich(host, monkeypatch, actual, draft, error):
    monkeypatch.setattr(us, "_github_json", lambda *a, **kw: {"tag_name": "v2", "draft": draft})
    result = us.validate_release_tag("v2", {"tag_name": actual})
    assert bool(result) == error


def route_args():
    return {"request": Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
            "db": MagicMock(), "user": MagicMock(id=12)}


def test_release_route_verwendet_formular_tag(host, monkeypatch):
    from app.routers import ui_settings as ui
    monkeypatch.setattr(ui, "is_docker", lambda: False)
    monkeypatch.setattr(ui, "get_github_token", lambda db: None)
    monkeypatch.setattr(ui, "check_github_release", lambda **kwargs: {"tag_name": "v2", "download_url": "url"})
    monkeypatch.setattr(us, "_github_json", lambda *a, **kw: {"tag_name": "v2"})
    monkeypatch.setattr(ui.templates, "TemplateResponse", lambda request, name, context: context)
    deploy = MagicMock(return_value={"state": "queued"})
    monkeypatch.setattr(ui.update_job, "start_job", deploy)
    result = ui.apply_github_update(**route_args(), prerelease=False, tag_name="v1", install_deps_raw="")
    assert not result["update_result"]["success"]
    deploy.assert_not_called()
    result = ui.apply_github_update(**route_args(), prerelease=False, tag_name="v2", install_deps_raw="1")
    assert result["job"]["state"] == "queued"
    assert deploy.call_args.args == ("release", "v2", 12)


def test_zip_audit_ohne_inhalt(host, monkeypatch):
    from app.routers import ui_settings as ui
    monkeypatch.setattr(ui, "is_docker", lambda: False)
    monkeypatch.setattr(ui.templates, "TemplateResponse", lambda request, name, context: context)
    monkeypatch.setattr(ui.update_job, "start_job", lambda *a, **kw: {"state": "queued"})
    audit = MagicMock()
    monkeypatch.setattr(ui, "write_audit", audit)
    content = b"Inhalt darf nicht im Audit stehen"
    ui.apply_system_update(**route_args(), release_zip=UploadFile(io.BytesIO(content), filename="release.zip"),
                           expected_sha256="a" * 64, install_deps_raw="")
    assert audit.call_args.args[1] == "system.update_zip"
    assert audit.call_args.kwargs["payload"] == {
        "filename": "release.zip", "size": len(content), "sha256_prefix": hashlib.sha256(content).hexdigest()[:16]}


def test_dump_download_und_policy_audit(monkeypatch, tmp_path):
    from app.routers import ui_db_backup as ui
    path = tmp_path / "einsatzleiter_20261102_120000.sql.gz"
    path.write_bytes(b"dump")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(ui, "_dump_liste", lambda: [{"name": path.name}])
    audit = MagicMock()
    monkeypatch.setattr(ui, "write_audit", audit)
    args = route_args()
    ui.db_backup_download(path.name, db=args["db"], user=args["user"])
    assert audit.call_args.args[1] == "admin.db_backup.download"
    assert audit.call_args.kwargs["payload"] == {"filename": path.name}
    ui.db_backups_speichern(retention_days=14, max_count=9, db=args["db"], user=args["user"])
    assert audit.call_args.args[1] == "admin.db_backup.policy_changed"
    assert audit.call_args.kwargs["payload"] == {"retention_days": 14, "max_count": 9}


@pytest.mark.parametrize("action", ["zip", "release", "branch"])
def test_docker_routen_hinweis_statt_500(client, monkeypatch, action):
    from app.routers import ui_settings as ui
    from tests.test_ui_db_backup import _benutzer_anlegen, _login
    _benutzer_anlegen("docker_update_" + action, "system_admin")
    csrf = _login(client, "docker_update_" + action)
    monkeypatch.setattr(ui, "is_docker", lambda: True)
    page = client.get("/admin/system/update")
    assert page.status_code == 200
    assert "Release-ZIP hochladen" not in page.text
    if action == "zip":
        result = client.post("/admin/system/update", data={"_csrf": csrf},
                             files={"release_zip": ("release.zip", b"invalid")})
    else:
        result = client.post("/admin/system/update/" + ("github" if action == "release" else "github-branch"),
                             data={"_csrf": csrf, "tag_name": "v2"})
    assert result.status_code == 200
    assert "docker compose pull &amp;&amp; docker compose up -d" in result.text


def test_zip_deploy_ohne_git_binary(host, monkeypatch):
    command = MagicMock(side_effect=AssertionError("Git wird bei ZIP-Installationen nicht benötigt"))
    monkeypatch.setattr(us, "_run_git", command)
    monkeypatch.setattr(us, "download_and_apply_github_update", lambda *a, **kw: {"success": True})
    assert us.deploy_github_branch("feature/test", "url")["success"]
    assert us.deploy_github_release("url", "v2")["success"]
    command.assert_not_called()


def test_zip_inkonsistente_struktur_vor_backup(host, monkeypatch, tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("app/main.py", "neu")
        zf.writestr("nested/pyproject.toml", "neu")
    backup = MagicMock()
    monkeypatch.setattr(us, "_run_pre_migration_backup", backup)
    assert not us.apply_update(path)["success"]
    backup.assert_not_called()
    assert (host / "app/main.py").read_text() == "alt"


def test_migration_timeout_zeigt_stderr_ende(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 900, stderr=b"Anfang" + b"x" * 900 + b"ENDE")
    monkeypatch.setattr(us.subprocess, "run", timeout)
    ok, message = us._run_migrations()
    assert not ok
    assert "Zeitlimit" in message and message.endswith("ENDE")
    assert "Anfang" not in message
