"""CalVer, bestätigte Release-Version und Reihenfolge der Release-Workflows."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

from app.services import update_job as job
from app.services import update_service as us

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def installation(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "APP_ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('version = "2026.11.01"\n')
    return tmp_path


@pytest.mark.parametrize("text,expected", [
    ("v2026.11.02.1", (2026, 11, 2, 1)), ("2026.11.02", (2026, 11, 2)),
    ("2026.11.02.0", (2026, 11, 2, 0)), ("unbekannt", None),
    ("2026.1.02", None), ("2026.11.02.1.2", None), ("vv2026.11.02", None),
    ("2026.11.02\n", None), ("2026.11.02-beta", None), ("", None),
])
def test_parse_calver(text, expected):
    assert us.parse_calver(text) == expected


@pytest.mark.parametrize("latest,current,expected", [
    ("2026.11.02", "2026.11.02", False), ("2026.11.02.0", "2026.11.02", False),
    ("2026.11.02.1", "2026.11.02", True), ("2026.11.02", "2026.11.02.1", False),
    ("2026.12.01", "2026.11.30", True), ("2027.01.01", "2026.12.31", True),
    ("2026.11.01", "2026.11.02", False), ("v2026.11.02", "2026.11.01", True),
    ("2026.11.02", "v2026.11.02", False), ("2026.11.02", "unbekannt", True),
    ("anderer-tag", "2026.11.02", True), ("anderer-tag", "anderer-tag", False),
])
def test_is_newer(latest, current, expected):
    assert us.is_newer(latest, current) is expected


@pytest.mark.parametrize("tag,current,update,downgrade", [
    ("v2026.11.02", "2026.11.02", False, False),
    ("v2026.11.02.0", "2026.11.02", False, False),
    ("v2026.11.02.1", "2026.11.02", True, False),
    ("v2026.11.01", "2026.11.02", False, True),
    ("v2026.11.02", "unbekannt", True, False),
    ("test", "2026.11.02", True, False),
    ("vv2026.11.01", "2026.11.02", True, False), ("test", "test", False, False),
    ("", "2026.11.02", False, False),
])
def test_github_release(monkeypatch, tag, current, update, downgrade):
    monkeypatch.setattr(us, "get_current_version", lambda: current)
    monkeypatch.setattr(us, "_github_json", lambda *a: {"tag_name": tag})
    result = us.check_github_release()
    assert result["has_update"] is update
    assert result["is_downgrade"] is downgrade


def installed(root, **changes):
    data = {"kind": "release", "tag": "v2026.11.02", "sha": "abc", "installed_at": "2026-11-02T12:00:00Z"}
    data.update(changes)
    path = root / "app_storage/update/installed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("sha,expected", [("abc", "2026.11.02"), ("other", "2026.11.01")])
def test_installed_git(installation, monkeypatch, sha, expected):
    installed(installation)
    (installation / ".git").write_text("gitdir: elsewhere")
    git = MagicMock(return_value=(0, sha, ""))
    monkeypatch.setattr(us, "_run_git", git)
    assert us.get_current_version() == expected
    git.assert_called_once_with(["rev-parse", "HEAD"], timeout=2)


@pytest.mark.parametrize("content", [None, "{", "[]", "null", '{"kind":"branch"}',
                                      '{"kind":"release","tag":"invalid"}'])
def test_missing_or_invalid_installed(installation, monkeypatch, content):
    (installation / ".git").mkdir()
    if content is not None:
        installed(installation).write_text(content)
    git = MagicMock(side_effect=AssertionError("Kein Git-Aufruf bei ungültigen Metadaten"))
    monkeypatch.setattr(us, "_run_git", git)
    assert us.get_current_version() == "2026.11.01"
    git.assert_not_called()


@pytest.mark.parametrize("version,expected", [
    ("2026.11.01", "2026.11.02"), ("2026.11.02", "2026.11.02"),
    ("2026.11.03", "2026.11.03"), ("unbekannt", "unbekannt"),
])
def test_installed_without_git(installation, monkeypatch, version, expected):
    (installation / "pyproject.toml").write_text(f'version = "{version}"')
    installed(installation)
    git = MagicMock(side_effect=AssertionError("ZIP braucht kein Git"))
    monkeypatch.setattr(us, "_run_git", git)
    assert us.get_current_version() == expected
    git.assert_not_called()


def test_version_not_cached(installation):
    installed(installation)
    assert us.get_current_version() == "2026.11.02"
    (installation / "pyproject.toml").write_text('version = "2026.11.03"')
    assert us.get_current_version() == "2026.11.03"


@pytest.mark.parametrize("state,kind,writes", [
    ("success", "release", True), ("failed", "release", False),
    ("unconfirmed", "release", False), ("success", "branch", False), ("success", "zip", False),
])
def test_completion_installed(installation, monkeypatch, state, kind, writes):
    root = job.directory()
    root.mkdir(parents=True)
    monkeypatch.setattr(job, "_audit", MagicMock())
    status = {"kind": kind, "ziel": "v2026.11.02", "result": {}, "steps": []}
    job._complete(status, state, "Test")
    path = root / "installed.json"
    assert path.exists() is writes
    if writes:
        data = json.loads(path.read_text())
        assert data["kind"] == "release" and data["tag"] == "v2026.11.02"
        assert data["sha"] is None and data["installed_at"].endswith("Z")
        assert path.stat().st_mode & 0o777 == 0o600
        assert not path.with_suffix(".json.tmp").exists()
        assert us.get_current_version() == "2026.11.02"


def test_installed_write_git_and_failure(installation, monkeypatch):
    job.directory().mkdir(parents=True)
    (installation / ".git").mkdir()
    monkeypatch.setattr(us, "_run_git", lambda *a, **kw: (0, "head-sha", ""))
    status = {"kind": "release", "ziel": "v2026.11.02"}
    job._save_installed(status)
    assert json.loads((job.directory() / "installed.json").read_text())["sha"] == "head-sha"
    monkeypatch.setattr(job, "_private_json", MagicMock(side_effect=OSError("nicht schreibbar")))
    job._save_installed(status)  # Best effort: bestätigten Erfolg nicht zurücknehmen.


def test_downgrade_template():
    env = Environment(loader=FileSystemLoader(REPO / "app/templates"))
    html = env.get_template("admin/_github_update.html").render(
        github={"is_downgrade": True, "has_update": False}, prerelease=False,
    )
    assert "Installierte Version ist neuer als das letzte Release" in html
    assert "Server-Update einspielen" not in html


def test_workflow_yaml():
    workflow = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text())
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"version", "notes", "prerelease", "target"}
    assert inputs["prerelease"]["default"] is False
    assert inputs["target"]["default"] == "main"
    assert workflow["permissions"] == {"contents": "write", "actions": "write"}
    # GITHUB_TOKEN-Pushes loesen keinen Docker-Build aus: ausdruecklich anstossen.
    schritte = " ".join(step.get("run", "") for step in workflow["jobs"]["release"]["steps"])
    assert 'gh workflow run docker-build.yml --ref "v$VERSION"' in schritte
    assert "gh workflow run docker-build.yml --ref main" in schritte
    assert workflow["concurrency"]["group"] == "release"
    steps = workflow["jobs"]["release"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    runs = [step["run"] for step in steps if "run" in step]
    assert all("${{ inputs." not in script for script in runs)
    commit = next(i for i, script in enumerate(runs) if "git commit" in script)
    tag = next(i for i, script in enumerate(runs) if "git tag " in script)
    push_tag = next(i for i, script in enumerate(runs) if 'git push origin "refs/tags/' in script)
    release = next(i for i, script in enumerate(runs) if "gh release create" in script)
    assert commit < tag <= push_tag < release
    assert 'git tag "v$VERSION" "$RELEASE_SHA"' in runs[tag]
    check = yaml.safe_load((REPO / ".github/workflows/version-bump.yml").read_text())
    assert check["on"]["release"]["types"] == ["published"]
    steps = check["jobs"]["verify"]["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.event.release.tag_name }}"
    for step in steps:
        assert "git push" not in step.get("run", "")
        assert "git commit" not in step.get("run", "")


@pytest.mark.parametrize("requested,old,tags,expected", [
    ("2026.12.03", "2000.01.01", [], "2026.12.03"),
    ("", "2000.01.01", [], "today"),
    ("", "today", [], "today.1"),
    ("", "today.2", [], "today.3"),
    ("", "2000.01.01", ["today", "today.1"], "today.2"),
    ("invalid", "2000.01.01", [], None),
    ("2026.12.03", "2000.01.01", ["2026.12.03"], None),
    ('$(touch injected)', "2000.01.01", [], None),
])
def test_workflow_version_script(installation, monkeypatch, requested, old, tags, expected):
    from datetime import UTC, datetime
    import subprocess

    today = datetime.now(UTC).strftime("%Y.%m.%d")
    old = old.replace("today", today)
    tags = ["v" + tag.replace("today", today) for tag in tags]
    if expected:
        expected = expected.replace("today", today)
    for name in ("pyproject.toml", "app/config.py", "README.md"):
        path = installation / name
        path.parent.mkdir(parents=True, exist_ok=True)
        source = (REPO / name).read_text()
        if name == "pyproject.toml":
            import re
            source = re.sub(r'^version = "[\d.]+"', f'version = "{old}"', source, flags=re.M)
        path.write_text(source)
    monkeypatch.chdir(installation)
    monkeypatch.setenv("REQUESTED_VERSION", requested)
    monkeypatch.setenv("GITHUB_OUTPUT", str(installation / "output"))
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "\n".join(tags))
    workflow = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text())
    run = workflow["jobs"]["release"]["steps"][1]["run"]
    script = run.split("python3 - <<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
    if expected is None:
        with pytest.raises(SystemExit):
            exec(compile(script, "release.yml", "exec"), {})
        assert not (installation / "output").exists()
    else:
        exec(compile(script, "release.yml", "exec"), {})
        assert (installation / "output").read_text() == f"version={expected}\n"
        assert f'version = "{expected}"' in (installation / "pyproject.toml").read_text()
        assert f'APP_VERSION: str = "{expected}"' in (installation / "app/config.py").read_text()
        readme = (installation / "README.md").read_text()
        assert f'version-{expected}-orange' in readme
        assert f'Aktuell: **{expected}**' in readme


@pytest.mark.parametrize("result", [(1, "", "Git fehlt"), RuntimeError("Git-Fehler")])
def test_installed_git_failure(installation, monkeypatch, result):
    installed(installation)
    (installation / ".git").mkdir()
    git = MagicMock(side_effect=result) if isinstance(result, Exception) else MagicMock(return_value=result)
    monkeypatch.setattr(us, "_run_git", git)
    assert us.get_current_version() == "2026.11.01"
    assert git.call_count == 1
