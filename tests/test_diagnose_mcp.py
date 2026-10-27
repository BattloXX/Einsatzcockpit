"""Neue Tools behalten OAuth, Live-Rechte, Audit und begrenzte Antworten."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp.registry import DIAG_TOOLS
from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.models.user import AuditLog, User
from app.services.diagnose.registry import CheckResult, Finding
from app.services.diagnose.runner import persist_result
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call

admin_env = oauth.admin_env


@pytest.fixture(autouse=True)
def clean_diagnose():
    with SessionLocal() as db:
        db.query(DiagFinding).delete()
        db.query(DiagRun).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(DiagFinding).delete()
        db.query(DiagRun).delete()
        db.commit()


def finish(run_id):
    with SessionLocal() as db:
        row = db.get(DiagRun, run_id)
        row.status, row.active_slot, row.finished_at = "done", None, utcnow()
        db.commit()


def test_four_tools_schema_audit_and_start_limits(client, admin_env):
    token = oauth.tokens(client, admin_env)["access_token"]
    listed = oauth.admin_call(client, token, "tools/list")
    assert all(tool in listed.text for tool in DIAG_TOOLS)
    start = call(client, token, "admin_diagnose_starten", {"checks": ["worker.zustand"], "umfang": "voll"})
    run_id = start["evidenz"]["run_id"]
    assert start["korrelations_id"] == start["evidenz"]["korrelations_id"]
    with SessionLocal() as db:
        row = db.get(DiagRun, run_id)
        assert row.status == "queued" and row.checks == ["worker.zustand"]
        assert row.quelle == "mcp" and row.angefordert_von == admin_env["id"]
    active = call(client, token, "admin_diagnose_starten")
    assert active["evidenz"]["fehler"] == "Rate-Limit erreicht"
    status = call(client, token, "admin_diagnose_status", {"run_id": run_id})
    latest = call(client, token, "admin_diagnose_status")
    assert status["evidenz"]["status"] == "queued"
    assert latest["evidenz"]["run_id"] == run_id
    for name in ("admin_fehler_liste", "admin_fehler_details"):
        args = {"id": 99999} if name.endswith("details") else {}
        answer = call(client, token, name, args)
        assert set(answer) == set(start)
    finish(run_id)
    for _ in range(5):
        answer = call(client, token, "admin_diagnose_starten")
        assert answer["status"] == "ok"
        finish(answer["evidenz"]["run_id"])
    assert call(client, token, "admin_diagnose_starten")["evidenz"]["fehler"] == "Rate-Limit erreicht"
    with SessionLocal() as db:
        audits = list(db.scalars(select(AuditLog).where(AuditLog.user_id == admin_env["id"],
                                                       AuditLog.action == "admin_mcp.tool")))
        assert set(DIAG_TOOLS) <= {json.loads(r.payload_json)["tool"] for r in audits}
        assert all(token not in r.payload_json for r in audits)
        assert '"checks": "[MASKIERT]"' in " ".join(r.payload_json for r in audits)
        # Der gespeicherte Zeitbereich ist ein rollierendes Stundenkontingent.
        for row in db.scalars(select(DiagRun)):
            row.created_at = utcnow() - timedelta(hours=2)
        db.commit()
    assert call(client, token, "admin_diagnose_starten")["status"] == "ok"


def test_pagination_filters_details_and_masking(client, admin_env):
    token = oauth.tokens(client, admin_env)["access_token"]
    with SessionLocal() as db:
        run = DiagRun(status="running", active_slot=1, korrelations_id=str(uuid4()))
        db.add(run)
        db.commit()
        persist_result(db, run.id, "worker.zustand", CheckResult([
            Finding(str(i), "Test token=PRIVATE", severity,
                    {"anzahl": 2, "freitext": "Max Muster private@example.org"}, {"task": list(range(100))})
            for i, severity in enumerate(("info", "warn", "fail", "kritisch", "warn"))]), success=True)
    rows = []
    cursor = None
    while True:
        page = call(client, token, "admin_fehler_liste", {"limit": 2, "cursor": cursor})["evidenz"]
        rows.extend(page["befunde"])
        cursor = page["cursor"]
        if cursor is None:
            break
    assert len(rows) == len({r["id"] for r in rows}) == 5
    assert [r["schweregrad"] for r in rows] == ["kritisch", "fail", "warn", "warn", "info"]
    detail = call(client, token, "admin_fehler_details", {"finding_key": rows[0]["finding_key"]})["evidenz"]
    assert len(detail["betroffene_ids"]["task"]) == 50
    assert detail["hypothese"].startswith("Hypothese: ")
    assert detail["occurrences"] == 1
    assert detail["first_seen_at"] <= detail["last_seen_at"]
    encoded = json.dumps(detail)
    assert all(s not in encoded for s in ("PRIVATE", "Max Muster", "private@example.org"))
    filtered = call(client, token, "admin_fehler_liste", {"modul": "worker", "schweregrad": "warn", "status": "offen"})
    assert len(filtered["evidenz"]["befunde"]) == 2
    assert call(client, token, "admin_fehler_liste", {"seit": "2099-01-01T00:00:00Z"})["evidenz"]["befunde"] == []
    for args in ({"limit": 101}, {"limit": 0}, {"cursor": "untrusted"}, {"seit": "bad"}, {"status": "unknown"}):
        assert call(client, token, "admin_fehler_liste", args)["status"] == "unbekannt"
    assert call(client, token, "admin_fehler_details", {"id": 1, "finding_key": "a" * 64})["status"] == "unbekannt"
    assert call(client, token, "admin_diagnose_starten", {"checks": ["repair"]})["status"] == "unbekannt"


@pytest.mark.parametrize("tool", DIAG_TOOLS)
def test_live_revocation_for_new_tools(client, admin_env, tool):
    token = oauth.tokens(client, admin_env)["access_token"]
    with SessionLocal() as db:
        db.get(User, admin_env["id"]).admin_mcp_access = False
        db.commit()
    args = {"id": 1} if tool.endswith("details") else {}
    answer = oauth.admin_call(client, token, "tools/call", {"name": tool, "arguments": args})
    assert answer.status_code == 401


def test_global_tables_excluded_from_org_export():
    from app.services.org_export_service import EXCLUDE_TABLES, scope_rules
    assert {"diag_run", "diag_finding"} <= EXCLUDE_TABLES
    assert not {"diag_run", "diag_finding"} & set(scope_rules())
