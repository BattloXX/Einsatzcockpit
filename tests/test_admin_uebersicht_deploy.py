"""Deploy-Abgleich der Uebersicht und gezielte Bereinigung alter Befunde."""
from uuid import uuid4

import pytest

from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp import registry
from app.config import settings
from app.models.diagnose import DiagFinding, DiagRun
from app.services import system_diagnose as diagnose
from app.services.diagnose.registry import Finding
from app.services.github_readonly import GitHubReadonly


@pytest.mark.parametrize("behind", [0, 2, None, "exception", "sha_unbekannt"])
def test_deploy_und_befunde(monkeypatch, behind):
    monkeypatch.setattr(settings, "GIT_SHA", "unknown" if behind == "sha_unbekannt" else "a" * 40)
    monkeypatch.setattr(registry.github_token, "status", lambda db: {"status": "ok"})
    for name in ("service_summary", "gateway_summary", "db_status", "worker_status", "redis_status", "version_info"):
        monkeypatch.setattr(diagnose, name, lambda *a: {
            "status": "ok", "evidence": {"workers": [], "worker_count": 0, "expired_workers": [], "leader_workers": []}})
    monkeypatch.setattr(registry, "collect_osm", lambda **kw: {"status": "ok", "evidence": {}})
    monkeypatch.setattr(GitHubReadonly, "head_of_main", lambda self: {"status": "ok", "daten": {"sha": "b" * 40}})

    def compare(self, sha):
        assert self._persist is False
        if behind == "exception":
            raise RuntimeError("GitHub nicht erreichbar")
        return {"status": "ok", "daten": {"commits_hinter_main": behind, "commits_vor_main": 1}} if (
            behind is not None) else {"status": "nicht verfuegbar"}

    monkeypatch.setattr(GitHubReadonly, "compare", compare)
    with SessionLocal() as db:
        run = DiagRun(status="done", active_slot=None, korrelations_id=str(uuid4()))
        db.add(run)
        db.flush()
        rows = []
        for kern in ("hinter_main", "sha_unbekannt", "github.token"):
            key = Finding(kern, "").key("deploy.version")
            row = db.query(DiagFinding).filter_by(finding_key=key).first()
            if row is None:
                row = DiagFinding(run_id=run.id, finding_key=key, check_id="deploy.version", modul="deploy",
                                  schweregrad="warn", titel=kern, ursache_hypothese="Test", empfehlung="Test")
                db.add(row)
            row.status = "offen"
            row.behoben_am = None
            rows.append(row)
        db.commit()
        result = registry.collect(db, "admin_system_uebersicht")
        deploy = result["evidence"]["deploy"]
        assert deploy["aktuell"] is (True if behind == 0 else False if behind == 2 else None)
        assert result["status"] == ("warn" if behind == 2 else "ok")
        if behind in (0, 2):
            assert deploy["commits_hinter_main"] == behind
            assert deploy["commits_vor_main"] == 1
            assert deploy["main_sha"] == "b" * 40
        else:
            assert deploy["hinweis"] == "GitHub-Abgleich nicht verfuegbar"
        db.rollback()
        db.expire_all()
        for row in rows[:2]:
            assert row.status == ("behoben" if behind == 0 else "offen")
            assert (row.behoben_am is not None) == (behind == 0)
        assert rows[2].status == "offen" and rows[2].behoben_am is None
