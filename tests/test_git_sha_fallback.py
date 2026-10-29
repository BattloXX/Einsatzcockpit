"""Lokale Deploy-Revision ohne Git-Prozess lesen."""

import pytest

from app.config import Settings
from app.core.git_sha import lese_git_sha

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("art", ["ref", "packed", "detached", "gitdatei", "worktree"])
def test_revision(tmp_path, art):
    git = tmp_path / ("verwaltung" if art in {"gitdatei", "worktree"} else ".git")
    git.mkdir()
    if art in {"gitdatei", "worktree"}:
        (tmp_path / ".git").write_text("gitdir: verwaltung\n")
    (git / "HEAD").write_text(SHA if art == "detached" else "ref: refs/heads/test\n")
    if art == "packed":
        (git / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/test\n")
    elif art != "detached":
        gemeinsam = git
        if art == "worktree":
            gemeinsam = tmp_path / "gemeinsam"
            (git / "commondir").write_text("../gemeinsam")
        (gemeinsam / "refs/heads").mkdir(parents=True)
        (gemeinsam / "refs/heads/test").write_text(SHA)
    assert lese_git_sha(tmp_path) == SHA


@pytest.mark.parametrize("inhalt", ["", "unknown", "abcdef12", "g" * 40, "ref: ../falsch", "ref: refs/heads/fehlt"])
def test_kaputte_inhalte(tmp_path, inhalt):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text(inhalt)
    assert lese_git_sha(tmp_path) == ""


def test_settings_fallback(monkeypatch):
    monkeypatch.setattr("app.config.lese_git_sha", lambda pfad: SHA)
    assert Settings(GIT_SHA="", _env_file=None).GIT_SHA == SHA
    assert Settings(GIT_SHA="unknown", _env_file=None).GIT_SHA == SHA
    assert Settings(GIT_SHA="abcdef12", _env_file=None).GIT_SHA == "abcdef12"
