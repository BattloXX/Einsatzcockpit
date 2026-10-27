"""Web-Reparaturen verwenden die existierende Engine, ohne MCP-Freigabe."""
import time

import pytest

from app.core.security import sign_session
from app.models.admin_repair import AdminRepairRun
from app.routers import ui_systemdiagnose as ui
from tests.test_repair_actions import repair_env  # noqa: F401
from tests.test_repair_runner import test_actions, setting  # noqa: F401
from tests.test_repair_web_mcp import repair_web  # noqa: F401


@pytest.fixture
def web_actions(repair_env, repair_web, test_actions, monkeypatch):
    monkeypatch.setattr(ui, "ACTIONS", test_actions)
    monkeypatch.setattr("app.services.repair.runner.einsatz_aktiv", lambda db: False)
    db, user, *_ = repair_env
    user.admin_mcp_access = False
    db.commit()
    return db, user, repair_web[0], repair_web[2]


def send(client, action, **data):
    return client.post("/admin/systemdiagnose/reparatur/" + action,
                       data={"_csrf":client.cookies.get("ec_csrf"), **data})


def simulate(client, db, user, risk):
    response = send(client, "simulieren", aktion_id="test."+risk, scope='{"ident":1}')
    assert response.status_code == 200, response.text
    row = db.query(AdminRepairRun).filter(AdminRepairRun.angefordert_von == user.id).order_by(
        AdminRepairRun.id.desc()).first()
    db.refresh(row)
    assert row.status == "simuliert" and row.korrelations_id.startswith("web:")
    return row


def test_medium_confirmation_hash_stepup_and_server_idempotency(web_actions):
    db, user, client, _ = web_actions
    row = simulate(client, db, user, "medium")
    args = {"run_id":row.id,"simulation_hash":row.simulation_hash,"bestaetigt":"true"}
    assert "warn" in send(client, "ausfuehren", **{**args,"simulation_hash":"invalid"}).text
    assert "warn" in send(client, "ausfuehren", **{**args,"bestaetigt":"false"}).text
    client.cookies.set("session", sign_session(user.id, mfa_at=int(time.time())-360, session_started_at=int(time.time())-600),
                       domain="testserver.local", path="/")
    assert send(client, "ausfuehren", **args).status_code == 403
    client.cookies.set("session", sign_session(user.id, mfa_at=int(time.time())),
                       domain="testserver.local", path="/")
    response = send(client, "ausfuehren", **args)
    assert response.status_code == 200 and "erfolgreich" in response.text
    db.refresh(row)
    key = row.idempotency_key
    assert key and row.status == "erfolgreich"
    response = send(client, "ausfuehren", **args)
    assert response.status_code == 200 and "erfolgreich" in response.text
    db.refresh(row)
    assert row.idempotency_key == key
    assert send(client, "verifizieren", run_id=row.id).status_code == 200


def test_high_existing_approval_and_execution(web_actions):
    db, user, client, codes = web_actions
    row = simulate(client, db, user, "high")
    assert f"/admin/reparatur/freigabe/{row.id}" in client.get(
        "/admin/systemdiagnose/teil/reparaturen").text
    url = f"/admin/reparatur/freigabe/{row.id}"
    assert client.get(url).status_code == 200
    assert client.post(url, data={"code":codes[0],"simulation_hash":row.simulation_hash}).status_code == 403
    response = client.post(url, data={"_csrf":client.cookies.get("ec_csrf"),"code":codes[0],
                                      "simulation_hash":row.simulation_hash})
    import re
    token = re.search(r'<code[^>]*>([^<]+)</code>', response.text).group(1)
    response = send(client, "ausfuehren", run_id=row.id, simulation_hash=row.simulation_hash,
                    bestaetigt="true", approval_token=token)
    assert response.status_code == 200 and "erfolgreich" in response.text


@pytest.mark.parametrize("switch", ["admin_2fa_enabled","admin_repair_enabled"])
def test_repair_gates_for_all_web_stages(web_actions, switch):
    db, user, client, _ = web_actions
    row = simulate(client, db, user, "medium")
    setting(db, switch, "false")
    for action in ("simulieren", "ausfuehren", "verifizieren", "vorschlagen"):
        assert send(client, action, run_id=row.id, finding_id=1).status_code == 400


def test_critical_simulation_only(web_actions):
    db, user, client, _ = web_actions
    row = simulate(client, db, user, "critical")
    response = send(client, "ausfuehren", run_id=row.id, simulation_hash=row.simulation_hash, bestaetigt="true")
    assert "warn" in response.text
    db.refresh(row)
    assert row.status == "simuliert"
