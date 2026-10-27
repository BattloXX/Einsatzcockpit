"""Neue Lesetools verwenden unveraendert Live-Rechte, Audit und MCP-Budgets."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import log_buffer
from app.admin_mcp import code_tools
from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp.registry import CODE_TOOLS
from app.config import settings
from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.models.user import AuditLog, User
from app.services.github_readonly import unavailable
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call

admin_env = oauth.admin_env
SECRET = "seed_readonly_token_abcdefghijklmn"
PLACE = {"datei": "app/services/code_analyse.py", "funktion": "group_logs", "exception_typ": "ValueError"}


@pytest.fixture(autouse=True)
def local_data(monkeypatch):
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", SECRET)
    monkeypatch.setattr(log_buffer, "get_entries", lambda *a: [{
        "ts": "2026-10-27 10:00:00", "level": "ERROR",
        "msg": 'File "app/services/code_analyse.py", line 1, in group_logs\nValueError: '
               + SECRET + ' alice@example.org +436641234567 192.168.1.4 Cookie: private=COOKIESEED'}])
    class LocalGitHub:
        def head_of_main(self):
            return {"status": "ok", "daten": {"sha": "abcdef13"}}
        def compare(self, sha):
            return {"status": "ok", "daten": {"commits_hinter_main": 2, "dateien": [
                {"datei": PLACE["datei"], "status": "modified"}], "dateien_begrenzt": False}}
        def workflow_runs_for_sha(self, sha):
            return {"status": "ok", "daten": []}
        def pr_for_commit(self, sha):
            return {"status": "ok", "daten": []}
    monkeypatch.setattr(code_tools, "GitHubReadonly", LocalGitHub)


def args_for(tool):
    return {"fehlerstelle": PLACE, "titel": SECRET + " alice@example.org"} if tool.endswith("erstellen") else {}


def test_tools_schema_masking_audit_and_visibility(client, admin_env):
    token = oauth.tokens(client, admin_env)["access_token"]
    listed = oauth.admin_call(client, token, "tools/list")
    assert all(name in listed.text for name in CODE_TOOLS)
    for tool in CODE_TOOLS:
        answer = call(client, token, tool, args_for(tool))
        assert set(answer) == {"zeitpunkt", "status", "modul", "schweregrad", "evidenz",
                               "ursachenhypothese", "empfehlung", "korrelations_id", "github_zugang"}
        assert answer["status"] == "ok"
        assert all(value not in json.dumps(answer) for value in (
            SECRET, "alice@example.org", "+436641234567", "192.168.1.4", "COOKIESEED"))
        if tool.endswith("erstellen"):
            text = answer["evidenz"]["markdown"]
            assert code_tools.PFLICHTSATZ in text
            assert all(value in text for value in ("CLAUDE.md", "Hypothese:", "unbekannt", "Testidee", "abcdef12"))
        elif tool.endswith("analyse"):
            assert answer["evidenz"]["fehlerstellen"][0]["seit_deploy_geaendert"] is True
        else:
            assert answer["evidenz"]["vergleich"]["daten"]["commits_hinter_main"] == 2
    with SessionLocal() as db:
        audits = list(db.scalars(select(AuditLog).where(AuditLog.user_id == admin_env["id"],
                                                       AuditLog.action == "admin_mcp.tool")))
        assert set(CODE_TOOLS) <= {json.loads(row.payload_json)["tool"] for row in audits}
        text = " ".join(row.payload_json for row in audits)
        assert not any(s in text for s in (SECRET, "alice@example.org", token))
        assert '"fehlerstelle": "[MASKIERT]"' in text
        assert db.scalar(select(DiagRun.id).where(DiagRun.angefordert_von == admin_env["id"])) is None


@pytest.mark.parametrize("tool", CODE_TOOLS)
def test_live_revocation(client, admin_env, tool):
    token = oauth.tokens(client, admin_env)["access_token"]
    with SessionLocal() as db:
        db.get(User, admin_env["id"]).admin_mcp_access = False
        db.commit()
    answer = oauth.admin_call(client, token, "tools/call", {"name": tool, "arguments": args_for(tool)})
    assert answer.status_code == 401


@pytest.mark.parametrize("tool", CODE_TOOLS)
def test_rate_and_size(client, admin_env, monkeypatch, tool):
    import app.admin_mcp.server as server
    token = oauth.tokens(client, admin_env)["access_token"]
    monkeypatch.setattr(server._tool_limiter, "hit", lambda *a: False)
    assert call(client, token, tool, args_for(tool))["evidenz"] == {"fehler": "Rate-Limit erreicht"}
    monkeypatch.setattr(server._tool_limiter, "hit", lambda *a: True)
    monkeypatch.setattr(server, "collect_code", lambda *a, **kw: {"status": "ok", "evidence": {"x": "a" * 66000}})
    assert call(client, token, tool, args_for(tool))["evidenz"] == {"fehler": "Antwortgroesse begrenzt"}


@pytest.mark.parametrize("token_value", ["", SECRET])
def test_ci_without_token_down_and_main_fallback(client, admin_env, monkeypatch, token_value):
    from app.services import github_readonly as gh
    import httpx
    gh._CACHE.clear()
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", token_value)
    monkeypatch.setattr(settings, "GIT_SHA", "")
    monkeypatch.setattr(gh, "_permit", lambda: True)
    def handler(request):
        if request.url.path.endswith("git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abcdef12"}})
        raise httpx.ConnectError(SECRET, request=request)
    monkeypatch.setattr(code_tools, "GitHubReadonly", lambda: gh.GitHubReadonly(httpx.MockTransport(handler)))
    token = oauth.tokens(client, admin_env)["access_token"]
    answer = call(client, token, "admin_ci_status")
    assert answer["status"] == "eingeschraenkt"
    assert answer["evidenz"]["abgefragte_sha"] == "abcdef12"
    assert SECRET not in json.dumps(answer)
    gh._CACHE.clear()


def test_finding_analysis_and_codex(client, admin_env):
    key = uuid4().hex * 2
    with SessionLocal() as db:
        run = DiagRun(status="done", active_slot=None, korrelations_id=str(uuid4()))
        db.add(run)
        db.flush()
        finding = DiagFinding(run_id=run.id, finding_key=key, check_id="gateway.jobs", modul="gateway",
            schweregrad="warn", titel=SECRET, evidenz={"msg": SECRET + " alice@example.org"},
            occurrences=3, status="offen", first_seen_at=utcnow(), last_seen_at=utcnow(),
            ursache_hypothese=SECRET, empfehlung=SECRET)
        db.add(finding)
        db.commit()
        finding_id, run_id = finding.id, run.id
    try:
        token = oauth.tokens(client, admin_env)["access_token"]
        answer = call(client, token, "admin_code_analyse", {"quelle": "befund", "finding_key": key})
        assert "app/services/alarm_outbox.py" in answer["evidenz"]["module"]
        answer = call(client, token, "admin_codex_auftrag_erstellen", {"finding_key": key})
        assert code_tools.PFLICHTSATZ in answer["evidenz"]["markdown"]
        assert SECRET not in json.dumps(answer) and "alice@example.org" not in json.dumps(answer)
    finally:
        with SessionLocal() as db:
            db.delete(db.get(DiagFinding, finding_id))
            db.delete(db.get(DiagRun, run_id))
            db.commit()


@pytest.mark.parametrize("tool,args", [
    ("admin_code_analyse", {"limit": 101}), ("admin_code_analyse", {"quelle": "other"}),
    ("admin_code_analyse", {"quelle": "befund"}), ("admin_code_analyse", {"level": "secret"}),
    ("admin_ci_status", {"sha": "../../"}), ("admin_codex_auftrag_erstellen", {}),
    ("admin_codex_auftrag_erstellen", {"fehlerstelle": {**PLACE, "datei": "../../secret"}}),
])
def test_invalid_parameters(client, admin_env, tool, args):
    token = oauth.tokens(client, admin_env)["access_token"]
    assert call(client, token, tool, args)["status"] == "unbekannt"


@pytest.mark.parametrize("tool", ["admin_code_analyse", "admin_codex_auftrag_erstellen"])
def test_local_tools_with_github_down(client, admin_env, monkeypatch, tool):
    monkeypatch.setattr(code_tools.GitHubReadonly, "compare", lambda *a: unavailable("timeout"))
    token = oauth.tokens(client, admin_env)["access_token"]
    answer = call(client, token, tool, args_for(tool))
    assert answer["status"] == "eingeschraenkt"
    if tool.endswith("analyse"):
        assert answer["evidenz"]["fehlerstellen"][0]["seit_deploy_geaendert"] is None
    else:
        assert code_tools.PFLICHTSATZ in answer["evidenz"]["markdown"]
    assert SECRET not in json.dumps(answer)


def test_optional_title_technical_words_only():
    assert code_tools.safe_title("Gateway Fehler pruefen") == "Gateway Fehler pruefen"
    text = code_tools.safe_title("Max Muster alice@example.org +436641234567 token=PRIVATE")
    assert not any(v in text for v in ("Max", "Muster", "alice", "4366", "PRIVATE"))
