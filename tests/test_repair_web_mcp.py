"""Echte Web-/MCP-Grenzen: Sichtbarkeit, CSRF, MFA, Token und Audit."""
import re
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.admin_mcp.registry import ADMIN_TOOLS, DIAG_TOOLS, REPAIR_TOOLS
from app.admin_mcp.repair_tools import collect_repair
from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.main import app
from app.models.admin_repair import AdminApproval, AdminRepairRun
from app.models.admin_security import utcnow
from app.models.diagnose import DiagFinding, DiagRun
from app.models.user import AuditLog
from app.services.repair.registry import ACTIONS, digest
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call
from tests.test_admin_totp import enroll
from tests.test_repair_actions import repair_env  # noqa: F401
from tests.test_repair_runner import setting, sim, test_actions  # noqa: F401

admin_env = oauth.admin_env


@pytest.fixture
def repair_mcp_env(admin_env):
    with oauth.SessionLocal() as db:
        from app.models.user import User
        user = db.get(User, admin_env["id"])
        _, codes = enroll(db, user)
        setting(db, "admin_2fa_enabled", "true")
        setting(db, "admin_repair_enabled", "true")
    yield {**admin_env, "codes": codes}
    with oauth.SessionLocal() as db:
        db.query(AdminApproval).delete()
        db.query(AdminRepairRun).delete()
        setting(db, "admin_repair_enabled", "false")


@pytest.mark.parametrize("switch", ["admin_2fa_enabled", "admin_repair_enabled"])
def test_mcp_tools_hidden_and_execution_blocked(client, repair_mcp_env, switch):
    env = repair_mcp_env
    token = oauth.tokens(client, env, code=env["codes"][0])["access_token"]
    listed = oauth.admin_call(client, token).text
    assert all(name in listed for name in REPAIR_TOOLS)
    assert all(name in listed for name in ADMIN_TOOLS | DIAG_TOOLS)
    with oauth.SessionLocal() as db:
        setting(db, switch, "false")
    listed = oauth.admin_call(client, token).text
    assert not any(name in listed for name in REPAIR_TOOLS)
    assert all(name in listed for name in ADMIN_TOOLS)
    for name in REPAIR_TOOLS:
        args = {"run_id": 1}
        if name == "admin_reparatur_ausfuehren":
            args.update(simulation_hash="f" * 64, idempotency_key="test-idempotency")
        elif name == "admin_reparatur_vorschlagen":
            args = {"id": 1}
        result = call(client, token, name, args)
        assert result["evidenz"]["fehler"] == ("2FA erforderlich" if switch == "admin_2fa_enabled"
                                               else "Reparaturen deaktiviert")


def test_all_five_tools_envelope_and_secret_free_audit(client, repair_mcp_env, monkeypatch):
    # Der Vertragstest verwendet keinen Fachbestand; das echte Einsatz-Gate
    # ist mit isolierten Einsatz-/GSL-Zeilen in test_repair_runner abgedeckt.
    monkeypatch.setattr("app.services.repair.runner.einsatz_aktiv", lambda db: False)
    env = repair_mcp_env
    token = oauth.tokens(client, env, code=env["codes"][0])["access_token"]
    with oauth.SessionLocal() as db:
        parent = DiagRun(korrelations_id=str(uuid4()), active_slot=None, status="done")
        db.add(parent)
        db.flush()
        finding = DiagFinding(run_id=parent.id, finding_key=uuid4().hex * 2, check_id="gateway.jobs", modul="gateway",
            schweregrad="warn", titel="Test", ursache_hypothese="Test", empfehlung="Test")
        db.add(finding)
        db.commit()
        ident, parent_id = finding.id, parent.id
    suggested = call(client, token, "admin_reparatur_vorschlagen", {"id": ident})
    run_id = suggested["evidenz"]["vorschlaege"][0]["run_id"]
    simulated = call(client, token, "admin_reparatur_simulieren", {"run_id": run_id,
        "scope": {"org_id": 1, "alarm_ids": [999999999]}})
    assert simulated["evidenz"]["vorschau"]["ausfuehrbar"] == 0
    executed = call(client, token, "admin_reparatur_ausfuehren", {"run_id": run_id,
        "simulation_hash": simulated["evidenz"]["simulation_hash"], "idempotency_key": "PRIVATE-KEY-SECRET",
        "bestaetigt": True, "approval_token": "PRIVATE-APPROVAL-TOKEN"})
    assert executed["evidenz"].get("status") == "erfolgreich", executed
    status = call(client, token, "admin_reparatur_status", {"run_id": run_id})
    verified = call(client, token, "admin_reparatur_verifizieren", {"run_id": run_id, "folgediagnose": True})
    assert verified["evidenz"]["verifikation"] == {"geaendert": 0, "uebersprungen": 0}
    assert all(set(row) == set(suggested) for row in (simulated, executed, status, verified))
    with oauth.SessionLocal() as db:
        audit = " ".join(a.payload_json or "" for a in db.scalars(select(AuditLog).where(AuditLog.user_id == env["id"])))
        assert all(secret not in audit for secret in (token, "PRIVATE-KEY-SECRET", "PRIVATE-APPROVAL-TOKEN"))
        actions = set(db.scalars(select(AuditLog.action).where(AuditLog.user_id == env["id"])))
        assert {"admin_repair." + stage for stage in ("vorgeschlagen", "simuliert", "freigegeben", "ausgefuehrt",
                                                       "verifiziert")} <= actions
        follow = verified["evidenz"]["folgediagnose"]
        if "run_id" in follow:
            assert db.get(DiagRun, follow["run_id"]).checks == ["gateway.jobs"]
            db.delete(db.get(DiagRun, follow["run_id"]))
        else:
            assert follow == {"fehler": "Rate-Limit erreicht"}
        db.delete(db.get(DiagFinding, ident))
        db.delete(db.get(DiagRun, parent_id))
        db.commit()


@pytest.fixture
def repair_web(repair_env, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient
    db, user, _, factory = repair_env
    secret, codes = enroll(db, user)
    monkeypatch.setattr(main, "SessionLocal", factory)
    previous = app.dependency_overrides.copy()
    def override():
        with factory() as session:
            set_tenant_context(session, None)
            yield session
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    client.get("/login")
    client.cookies.set("session", sign_session(user.id, session_epoch=user.session_epoch, mfa_at=int(time.time())),
                       domain="testserver.local", path="/")
    yield client, secret, codes
    client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def test_approval_page_csrf_fresh_recovery_binding_and_headers(repair_env, repair_web, test_actions):
    db, user, *_ = repair_env
    client, _, codes = repair_web
    run = sim(db, user, "test.high")
    url = f"/admin/reparatur/freigabe/{run['run_id']}"
    page = client.get(url)
    assert page.status_code == 200 and "test.high" in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    assert 'name="_csrf"' in page.text and str(run["run_id"]) in page.text
    body = {"code": codes[0], "simulation_hash": run["simulation_hash"]}
    assert client.post(url, data=body).status_code == 403
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    wrong = client.post(url, data={**body, "simulation_hash": "wrong"}, headers=headers)
    assert "ungueltig" in wrong.text
    success = client.post(url, data=body, headers=headers)
    assert success.status_code == 200 and "Einmalige Freigabe" in success.text
    token = re.search(r'<code[^>]*>([^<]+)</code>', success.text).group(1)
    db.expire_all()
    approval = db.scalar(select(AdminApproval))
    assert approval.token_hash != token and approval.aktion_hash
    assert 0 < (approval.expires_at - approval.created_at).total_seconds() <= 600
    result = runner_execute(db, user, run, token)
    assert result["status"] == "erfolgreich"
    audit = " ".join(a.payload_json or "" for a in db.scalars(select(AuditLog)))
    assert token not in audit and codes[0] not in audit


def runner_execute(db, user, run, token):
    from app.services.repair.runner import execute
    return execute(db, run["run_id"], user.id, user.session_epoch, "c", run["simulation_hash"], uuid4().hex, True, token)


def test_approval_totp_replay_and_web_rate_limit(repair_env, repair_web, test_actions):
    from app.services import totp_service
    db, user, *_ = repair_env
    client, secret, codes = repair_web
    run = sim(db, user, "test.high")
    url = f"/admin/reparatur/freigabe/{run['run_id']}"
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    # Der Einrichtungscode ist bereits verbraucht; ein frischer Session-Marker genügt nicht.
    replay = totp_service.totp(secret, int(time.time()) // 30)
    assert "ungueltig" in client.post(url, data={"code": replay, "simulation_hash": run["simulation_hash"]},
                                      headers=headers).text
    for _ in range(4):
        assert client.post(url, data={"code": "000000", "simulation_hash": run["simulation_hash"]},
                           headers=headers).status_code == 200
    assert client.post(url, data={"code": codes[1], "simulation_hash": run["simulation_hash"]},
                       headers=headers).status_code == 429
    assert db.scalar(select(AdminApproval)) is None


def test_approval_page_live_user_and_default_switch_ui(repair_env, repair_web, test_actions):
    db, user, *_ = repair_env
    client, _, _ = repair_web
    run = sim(db, user, "test.high")
    page = client.get("/admin/system-einstellungen")
    assert page.status_code == 200 and "admin-mcp/reparatur" in page.text
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    response = client.post("/admin/system-einstellungen/admin-mcp/reparatur",
                           data={"enabled": "false", "low_auto": ""}, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert client.get(f"/admin/reparatur/freigabe/{run['run_id']}").status_code == 403
    setting(db, "admin_repair_enabled", "true")
    user.admin_mcp_access = False
    db.commit()
    assert client.get(f"/admin/reparatur/freigabe/{run['run_id']}").status_code == 403


def test_approval_fresh_totp_only_once(repair_env, repair_web, test_actions):
    from app.services import totp_service
    db, user, *_ = repair_env
    client, secret, _ = repair_web
    run = sim(db, user, "test.high")
    url = f"/admin/reparatur/freigabe/{run['run_id']}"
    headers = {"X-CSRF-Token": client.cookies.get("ec_csrf")}
    code = totp_service.totp(secret, int(time.time()) // 30 + 1)
    body = {"code": code, "simulation_hash": run["simulation_hash"]}
    assert "Einmalige Freigabe" in client.post(url, data=body, headers=headers).text
    assert "ungueltig" in client.post(url, data=body, headers=headers).text
    db.expire_all()
    assert len(list(db.scalars(select(AdminApproval)))) == 1


def test_approval_expired_during_code_verification_consumes_factor(repair_env, repair_web, test_actions, monkeypatch):
    from datetime import timedelta
    from app.services import totp_service
    db, user, *_ = repair_env
    client, _, codes = repair_web
    run = sim(db, user, "test.high")
    verify = totp_service.verify
    def expires_after_verify(session, user_id, code):
        success = verify(session, user_id, code)
        session.get(AdminRepairRun, run["run_id"]).simulated_at = utcnow() - timedelta(minutes=16)
        session.flush()
        return success
    monkeypatch.setattr(totp_service, "verify", expires_after_verify)
    response = client.post(f"/admin/reparatur/freigabe/{run['run_id']}",
        data={"code": codes[0], "simulation_hash": run["simulation_hash"]},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})
    assert response.status_code == 409
    db.expire_all()
    assert not verify(db, user.id, codes[0])
    assert db.scalar(select(AdminApproval)) is None
