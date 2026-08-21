"""GitHub-Auto-Update: Release-/Branch-Check, Token-Header, Zipball-Validierung."""
import fcntl
import io
import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Header / Token ────────────────────────────────────────────────────────────

def test_github_headers_ohne_und_mit_token():
    from app.services.update_service import _github_headers
    ohne = _github_headers(None)
    assert "Authorization" not in ohne
    assert ohne["User-Agent"].startswith("Einsatzcockpit")
    mit = _github_headers("ghp_test123")
    assert mit["Authorization"] == "Bearer ghp_test123"


def test_get_github_token_roundtrip():
    from app.services.ai_service import encrypt_api_key
    from app.services.update_service import GITHUB_TOKEN_KEY, get_github_token

    class _Row:
        key = GITHUB_TOKEN_KEY
        value = encrypt_api_key("github_pat_abc")

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Row()
    assert get_github_token(db) == "github_pat_abc"

    db.query.return_value.filter.return_value.first.return_value = None
    assert get_github_token(db) is None


# ── Branch-Check / Branch-Liste (gemockte API) ───────────────────────────────

def _fake_github_json(antworten: dict):
    """Erzeugt einen _github_json-Ersatz, der je URL-Fragment eine Antwort liefert."""
    def _mock(url, token=None, timeout=10):
        for fragment, antwort in antworten.items():
            if fragment in url:
                if isinstance(antwort, Exception):
                    raise antwort
                return antwort
        raise AssertionError(f"Unerwartete URL: {url}")
    return _mock


def test_check_github_branch():
    from app.services import update_service
    antwort = {
        "sha": "abcdef1234567890",
        "commit": {
            "message": "Objektverwaltung: Fix\n\nDetails...",
            "author": {"name": "Johannes", "date": "2026-07-05T10:00:00Z"},
        },
    }
    with patch.object(update_service, "_github_json",
                      side_effect=_fake_github_json({"/commits/main": antwort})):
        info = update_service.check_github_branch("main")
    assert info["sha_short"] == "abcdef1"
    assert info["commit_message"] == "Objektverwaltung: Fix"
    assert info["commit_author"] == "Johannes"
    assert info["download_url"].endswith("/zipball/main")
    assert "error" not in info


def test_check_github_branch_fehler():
    from app.services import update_service
    with patch.object(update_service, "_github_json",
                      side_effect=_fake_github_json({"/commits/": OSError("404")})):
        info = update_service.check_github_branch("gibtsnicht")
    assert info["sha"] is None
    assert "404" in info["error"]


def test_list_github_branches():
    from app.services import update_service
    antwort = [
        {"name": "main", "commit": {"sha": "abc1234567"}},
        {"name": "feature/objektverwaltung", "commit": {"sha": "def7654321"}},
    ]
    with patch.object(update_service, "_github_json",
                      side_effect=_fake_github_json({"/branches": antwort})):
        branches = update_service.list_github_branches()
    assert branches == [
        {"name": "main", "sha": "abc1234"},
        {"name": "feature/objektverwaltung", "sha": "def7654"},
    ]


def test_list_github_branches_fehler_leere_liste():
    from app.services import update_service
    with patch.object(update_service, "_github_json", side_effect=OSError("offline")):
        assert update_service.list_github_branches() == []


# ── Zipball-Validierung (GitHub-Root-Ordner) ─────────────────────────────────

def _zip_bytes(eintraege: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, inhalt in eintraege.items():
            zf.writestr(name, inhalt)
    buf.seek(0)
    return buf


def test_validate_zip_akzeptiert_github_zipball(tmp_path):
    """GitHub-Zipballs verpacken alles unter einem Root-Ordner mit SHA-Suffix."""
    from app.services.update_service import validate_zip
    pfad = tmp_path / "zipball.zip"
    buf = _zip_bytes({
        "BattloXX-Einsatzcockpit-abc1234/pyproject.toml": b"[project]\nversion='1'",
        "BattloXX-Einsatzcockpit-abc1234/app/main.py": b"# app",
    })
    pfad.write_bytes(buf.read())
    valid, msg = validate_zip(pfad)
    assert valid, msg


def test_validate_zip_lehnt_fremdes_zip_ab(tmp_path):
    from app.services.update_service import validate_zip
    pfad = tmp_path / "fremd.zip"
    pfad.write_bytes(_zip_bytes({"readme.txt": b"nix"}).read())
    valid, msg = validate_zip(pfad)
    assert not valid


def test_apply_update_signatur_install_deps():
    """install_deps ist optional und defaultet auf False (Bestandsverhalten)."""
    import inspect

    from app.services.update_service import apply_update, download_and_apply_github_update
    params = inspect.signature(apply_update).parameters
    assert params["install_deps"].default is False
    dl_params = inspect.signature(download_and_apply_github_update).parameters
    assert "token" in dl_params and "install_deps" in dl_params


# ── Release-Check mit Token (gemockt) ────────────────────────────────────────

def test_check_github_release_nutzt_token():
    from app.services import update_service
    gesehen: dict = {}

    def _mock(url, token=None, timeout=10):
        gesehen["token"] = token
        return {"tag_name": "v9.9.9", "assets": [], "zipball_url": "https://x/zip"}

    with patch.object(update_service, "_github_json", side_effect=_mock):
        info = update_service.check_github_release(token="ghp_secret")
    assert gesehen["token"] == "ghp_secret"
    assert info["latest_tag"] == "9.9.9"
    assert info["tag_name"] == "v9.9.9"
    assert info["has_update"] is True


# ── Router-Registrierung ──────────────────────────────────────────────────────

def test_update_routen_registriert():
    from app.routers.ui_settings import router
    pfade = {r.path for r in router.routes}
    assert "/admin/system/update/check-branch" in pfade
    assert "/admin/system/update/github-branch" in pfade
    assert "/admin/system/update/github-token" in pfade
    # Bestandsrouten unveraendert
    assert "/admin/system/update/check-github" in pfade
    assert "/admin/system/update/github" in pfade


def test_deployed_ref_helpers():
    from app.routers.ui_settings import _deployed_ref

    class _Row:
        value = json.dumps({"branch": "main", "sha": "abc", "datum": "2026-07-05T10:00:00"})

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Row()
    ref = _deployed_ref(db)
    assert ref["branch"] == "main"

    db.query.return_value.filter.return_value.first.return_value = None
    assert _deployed_ref(db) is None


# ── Git-basierter Branch-Deploy (Web-Update == Konsole) ──────────────────────

def test_deploy_github_branch_prefers_git(monkeypatch):
    """Auf einem Git-Checkout läuft der Branch-Deploy über git (nicht Zip-Overlay)."""
    from app.services import update_service as us

    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    called = {}

    def fake_git_update(branch, token=None, install_deps=False):
        called["git"] = (branch, token, install_deps)
        return {"success": True, "via": "git"}

    monkeypatch.setattr(us, "git_update", fake_git_update)
    monkeypatch.setattr(us, "download_and_apply_github_update",
                        lambda *a, **k: {"success": True, "via": "zip"})

    res = us.deploy_github_branch("main", "http://zipball", token="tok", install_deps=True)
    assert res["via"] == "git"
    assert called["git"] == ("main", "tok", True)


def test_deploy_github_branch_falls_back_to_zip_without_git(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: False)
    monkeypatch.setattr(us, "download_and_apply_github_update",
                        lambda *a, **k: {"success": True, "via": "zip"})
    res = us.deploy_github_branch("main", "http://zipball")
    assert res["via"] == "zip"


def test_git_update_runs_fetch_and_hard_reset(monkeypatch):
    """git_update fetcht und setzt hart auf FETCH_HEAD zurück → sauberer Baum,
    identisch zu `git fetch && git reset --hard` auf der Konsole."""
    from app.services import update_service as us

    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    cmds = []

    def fake_git(args, timeout=300):
        cmds.append(args)
        if args[:1] == ["rev-parse"]:
            return 0, "aaaaaaa" if len(cmds) < 3 else "bbbbbbb", ""
        if args[:1] == ["diff"]:
            return 0, "app/main.py\napp/routers/x.py", ""
        return 0, "", ""

    monkeypatch.setattr(us, "_run_git", fake_git)
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": True, "required": True, "message": "OK", "files": []})
    monkeypatch.setattr(us, "_run_pip_install", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_run_migrations", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_reload_server", lambda: True)

    res = us.git_update("main", token="secrettoken", install_deps=True)
    assert res["success"] is True
    assert res["via"] == "git"
    assert res["files_updated"] == 2
    # Kernablauf: fetch dann reset --hard FETCH_HEAD
    assert ["fetch", "--force", "https://x-access-token:secrettoken@github.com/BattloXX/Einsatzcockpit.git", "main"] in cmds
    assert ["reset", "--hard", "FETCH_HEAD"] in cmds
    assert ["checkout", "-B", "main"] in cmds


def test_git_update_fetch_failure_scrubs_token(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: True)

    def fake_git(args, timeout=300):
        if args[:1] == ["rev-parse"]:
            return 0, "aaaaaaa", ""
        if args[:1] == ["fetch"]:
            return 128, "", "fatal: could not read from https://x-access-token:secrettoken@github.com/..."
        return 0, "", ""

    monkeypatch.setattr(us, "_run_git", fake_git)
    res = us.git_update("main", token="secrettoken")
    assert res["success"] is False
    assert "secrettoken" not in res["message"]
    assert "***" in res["message"]


def test_git_update_no_git_checkout(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: False)
    res = us.git_update("main")
    assert res["success"] is False


def _write_update_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("release/pyproject.toml", b"[project]\nname='x'")
        zf.writestr("release/app/main.py", b"new-main")
        for name, value in entries.items():
            zf.writestr("release/" + name, value)


def _mock_update_tail(monkeypatch, us):
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": True, "required": True, "message": "OK", "files": []})
    monkeypatch.setattr(us, "_run_migrations", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_reload_server", lambda: True)


def test_apply_update_copies_and_protects_app_storage(tmp_path, monkeypatch):
    from app.services import update_service as us
    root = tmp_path / "install"
    (root / "app_storage/incident_media").mkdir(parents=True)
    (root / "app_storage/incident_media/photo.txt").write_text("original")
    archive = tmp_path / "release.zip"
    _write_update_zip(archive, {
        "app/feature.py": b"feature", "app_storage/incident_media/photo.txt": b"clobbered"})
    monkeypatch.setattr(us, "APP_ROOT", root)
    _mock_update_tail(monkeypatch, us)
    result = us.apply_update(archive)
    assert result["success"] is True
    assert (root / "app/feature.py").read_bytes() == b"feature"
    assert (root / "app_storage/incident_media/photo.txt").read_text() == "original"
    assert result["files_skipped"] == 1


@pytest.mark.parametrize("which", ["deps", "migrations"])
def test_zip_tail_failure_is_unsuccessful_and_does_not_reload(tmp_path, monkeypatch, which):
    from app.services import update_service as us
    archive = tmp_path / "release.zip"
    _write_update_zip(archive, {})
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(us, "APP_ROOT", root)
    _mock_update_tail(monkeypatch, us)
    reloader = MagicMock()
    monkeypatch.setattr(us, "_reload_server", reloader)
    if which == "deps":
        monkeypatch.setattr(us, "_run_pip_install", lambda: (False, "pip kaputt"))
        result = us.apply_update(archive, install_deps=True)
    else:
        monkeypatch.setattr(us, "_run_migrations", lambda: (False, "alembic kaputt"))
        result = us.apply_update(archive)
    assert result["success"] is False
    assert result["server_reloaded"] is False
    reloader.assert_not_called()


@pytest.mark.parametrize("required,expected", [(True, False), (False, True)])
def test_backup_gate_required_or_warning(tmp_path, monkeypatch, required, expected):
    from app.services import update_service as us
    archive = tmp_path / "release.zip"
    _write_update_zip(archive, {})
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(us, "APP_ROOT", root)
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": False, "required": required, "message": "dump kaputt", "files": []})
    monkeypatch.setattr(us, "_run_migrations", lambda: (True, "OK"))
    monkeypatch.setattr(us, "_reload_server", lambda: True)
    result = us.apply_update(archive)
    assert result["success"] is expected


def test_run_pip_install_erkennt_stille_user_site_ausweichung(monkeypatch, tmp_path):
    """pip meldet Exit-Code 0, weicht aber (Berechtigungsproblem) auf eine fuer
    das venv unsichtbare User-Installation aus - das muss als Fehler erkannt werden,
    nicht als Erfolg (sonst crasht der naechste Start mit ModuleNotFoundError, ohne
    dass der Update-Schritt je einen Fehler gezeigt haette)."""
    from app.services import update_service as us
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)

    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        assert env is not None and env.get("PYTHONNOUSERSITE") == "1"
        return subprocess.CompletedProcess(
            argv, returncode=0,
            stdout="Defaulting to user installation because normal site-packages is not writeable\n"
                   "Successfully installed itsdangerous-2.2.0\n",
            stderr="",
        )

    monkeypatch.setattr(us.subprocess, "run", fake_run)
    ok, message = us._run_pip_install()
    assert ok is False
    assert "beschreibbar" in message or "Berechtigungsproblem" in message


def test_run_pip_install_ok_ohne_user_site_ausweichung(monkeypatch, tmp_path):
    from app.services import update_service as us
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)

    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        return subprocess.CompletedProcess(argv, returncode=0, stdout="OK\n", stderr="")

    monkeypatch.setattr(us.subprocess, "run", fake_run)
    ok, message = us._run_pip_install()
    assert (ok, message) == (True, "OK")


def test_deploy_github_release_git_and_zip(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    git = MagicMock(return_value={"via": "git"})
    monkeypatch.setattr(us, "git_update_release", git)
    assert us.deploy_github_release("url", "v2.0", token="t", install_deps=True)["via"] == "git"
    git.assert_called_once_with("v2.0", token="t", install_deps=True)
    monkeypatch.setattr(us, "is_git_checkout", lambda: False)
    monkeypatch.setattr(us, "download_and_apply_github_update", lambda *a, **k: {"via": "zip"})
    assert us.deploy_github_release("url", "v2.0")["via"] == "zip"


def test_git_release_detached_and_checkout_failure(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    commands = []
    def fake_git(args, timeout=300):
        commands.append(args)
        if args[:1] == ["checkout"]:
            return 1, "", "checkout kaputt"
        return 0, "abc1234" if args[:1] == ["rev-parse"] else "", ""
    monkeypatch.setattr(us, "_run_git", fake_git)
    result = us.git_update_release("v2.0")
    assert ["fetch", "--force", "https://github.com/BattloXX/Einsatzcockpit.git", "v2.0"] in commands
    assert ["checkout", "--force", "--detach", "FETCH_HEAD"] in commands
    assert result["success"] is False


def test_git_migration_failure_does_not_reload(monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "is_git_checkout", lambda: True)
    monkeypatch.setattr(us, "_run_git", lambda args, timeout=300: (0, "abc1234", ""))
    monkeypatch.setattr(us, "_run_pre_migration_backup", lambda: {
        "success": True, "required": True, "message": "OK", "files": []})
    monkeypatch.setattr(us, "_run_migrations", lambda: (False, "kaputt"))
    reload_mock = MagicMock()
    monkeypatch.setattr(us, "_reload_server", reload_mock)
    result = us.git_update("main")
    assert result["success"] is False
    reload_mock.assert_not_called()


def test_update_lock_contention_then_release(tmp_path, monkeypatch):
    from app.services import update_service as us
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)
    lock = (tmp_path / ".update.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with us._update_lock() as acquired:
            assert acquired is False
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    with us._update_lock() as acquired:
        assert acquired is True


def _router_args(data: bytes = b"zip"):
    from fastapi import Request, UploadFile
    return {
        "request": Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
        "db": MagicMock(), "user": MagicMock(),
        "release_zip": UploadFile(io.BytesIO(data), filename="release.zip"),
    }


def test_manual_zip_hash_required_rejects_before_processing(monkeypatch):
    import inspect
    from app.routers import ui_settings as router_module
    assert not inspect.iscoroutinefunction(router_module.apply_system_update)
    monkeypatch.setattr(router_module.app_settings, "UPDATE_ZIP_REQUIRE_HASH", True)
    apply_mock = MagicMock()
    monkeypatch.setattr(router_module, "apply_update", apply_mock)
    monkeypatch.setattr(router_module.templates, "TemplateResponse",
                        lambda request, name, context: context)
    response = router_module.apply_system_update(**_router_args(), expected_sha256="")
    assert "SHA256" in response["error"]
    apply_mock.assert_not_called()


def test_manual_zip_hash_optional_when_setting_disabled(monkeypatch):
    from app.routers import ui_settings as router_module
    monkeypatch.setattr(router_module.app_settings, "UPDATE_ZIP_REQUIRE_HASH", False)
    apply_mock = MagicMock(return_value={"success": True})
    monkeypatch.setattr(router_module, "apply_update", apply_mock)
    monkeypatch.setattr(router_module.templates, "TemplateResponse",
                        lambda request, name, context: context)
    response = router_module.apply_system_update(**_router_args(), expected_sha256="")
    assert response["update_result"]["success"] is True
    assert apply_mock.call_args.kwargs["expected_sha256"] is None


def test_manual_zip_tempfile_removed_when_apply_raises(monkeypatch):
    from app.routers import ui_settings as router_module
    monkeypatch.setattr(router_module.app_settings, "UPDATE_ZIP_REQUIRE_HASH", True)
    seen = {}
    def explode(path, **kwargs):
        seen["path"] = path
        seen["hash"] = kwargs["expected_sha256"]
        assert path.exists()
        raise RuntimeError("boom")
    monkeypatch.setattr(router_module, "apply_update", explode)
    digest = "a" * 64
    with pytest.raises(RuntimeError, match="boom"):
        router_module.apply_system_update(**_router_args(), expected_sha256=digest)
    assert seen["hash"] == digest
    assert not seen["path"].exists()


def test_reload_server_reports_failure_when_sudo_restart_fails(monkeypatch):
    """Regression: `sudo systemctl restart` kann fehlschlagen (fehlende sudoers-
    Freigabe, Passwort-Prompt), ohne dass subprocess.run eine Exception wirft --
    _reload_server() muss den returncode auswerten, statt bei jedem Aufruf ohne
    Exception blind True zu melden (sonst zeigt die Update-Seite faelschlich
    "Server-Reload: Ja", obwohl der laufende Prozess nie neu gestartet wurde)."""
    from app.services import update_service as us

    monkeypatch.setattr(us, "APP_ROOT", Path("/nonexistent-app-root"))
    monkeypatch.setattr(
        us.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=1, stdout=b"", stderr=b"sudo: a password is required"),
    )
    assert us._reload_server() is False


def test_reload_server_reports_success_when_sudo_restart_succeeds(monkeypatch):
    from app.services import update_service as us

    monkeypatch.setattr(us, "APP_ROOT", Path("/nonexistent-app-root"))
    monkeypatch.setattr(
        us.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0, stdout=b"", stderr=b""),
    )
    assert us._reload_server() is True
