"""GitHub-Auto-Update: Release-/Branch-Check, Token-Header, Zipball-Validierung."""
import io
import json
import zipfile
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
    monkeypatch.setattr(us, "_run_pip_install", lambda: "OK")
    monkeypatch.setattr(us, "_run_migrations", lambda: "OK")
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
