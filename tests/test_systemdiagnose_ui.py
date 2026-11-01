"""Systemdiagnose: echte Webgrenzen, CSRF, Maskierung und gemeinsame Services."""
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import sign_session
from app.admin_mcp.context import admin_session as SessionLocal
from app.models.admin_repair import AdminRepairRun
from app.models.diag_schedule import DiagNotification, DiagScheduleSlot
from app.models.diagnose import DiagFinding, DiagRun
from app.models.master import SystemSettings
from app.models.user import AuditLog, User, UserRole, Role
from app.routers.ui_systemdiagnose import SECTIONS
from app.services import diag_schedule
from tests.test_admin_mcp_oauth import admin_env  # noqa: F401

BASE = "/admin/systemdiagnose"


def setting(db, key, value):
    row = db.get(SystemSettings, key)
    if row is None:
        row = SystemSettings(key=key)
        db.add(row)
    row.value = value
    db.commit()


def login(client, ident, **kwargs):
    client.cookies.set("session", sign_session(ident, **kwargs), domain="testserver.local", path="/")


def post(client, path, **data):
    return client.post(BASE + path, data={"_csrf": client.cookies.get("ec_csrf"), **data})


@pytest.fixture
def cockpit(client, admin_env):
    with SessionLocal() as db:
        db.query(DiagNotification).delete()
        db.query(DiagScheduleSlot).delete()
        db.query(DiagFinding).delete()
        db.query(DiagRun).delete()
        setting(db, "admin_repair_enabled", "false")
        for key, value in diag_schedule.DEFAULTS.items():
            setting(db, key, value)
        user = db.get(User, admin_env["id"])
        user.admin_mcp_access = False  # Webseite hat kein MCP-Opt-in.
        db.commit()
    login(client, admin_env["id"])
    client.get(BASE)
    yield admin_env
    with SessionLocal() as db:
        db.query(AdminRepairRun).filter(AdminRepairRun.angefordert_von == admin_env["id"]).delete()
        db.query(DiagNotification).delete()
        db.query(DiagScheduleSlot).delete()
        db.query(DiagFinding).delete()
        db.query(DiagRun).delete()
        for key in diag_schedule.DEFAULTS:
            row = db.get(SystemSettings, key)
            if row:
                db.delete(row)
        setting(db, "admin_2fa_enabled", "false")
        setting(db, "admin_repair_enabled", "false")


@pytest.mark.parametrize("section", SECTIONS)
def test_all_sections_render_sqlite(client, cockpit, section):
    response = client.get(BASE + "/teil/" + section)
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    assert "location.reload" not in response.text


@pytest.mark.parametrize("role", ["admin", "org_admin", "readonly"])
def test_other_roles_forbidden(client, cockpit, role):
    with SessionLocal() as db:
        user = db.get(User, cockpit["id"])
        db.query(UserRole).filter(UserRole.user_id == user.id).delete()
        r = db.scalar(select(Role).where(Role.code == role))
        db.add(UserRole(user_id=user.id, role_id=r.id))
        db.commit()
    for path in (BASE, BASE + "/teil/audit", BASE + "/befund/1"):
        assert client.get(path).status_code == 403


def test_personal_and_mfa_gate(client, cockpit):
    from app.models.user import DeviceToken
    with SessionLocal() as db:
        db.add(DeviceToken(user_id=cockpit["id"], token_hash=uuid4().hex, label="Test"))
        db.commit()
    login(client, cockpit["id"], device=True)
    assert client.get(BASE).status_code == 403
    # Gueltige QR-Sitzung hat nur Recorder-Rechte (zentraler Wrapper).
    from app.models.incident import Incident, IncidentToken
    with SessionLocal() as db:
        inc = Incident(status="active", primary_org_id=1)
        db.add(inc)
        db.flush()
        db.add(IncidentToken(incident_id=inc.id, issued_by_user_id=cockpit["id"], token_hash=uuid4().hex))
        db.commit()
        incident_id = inc.id
    login(client, cockpit["id"], qr=True, incident_id=incident_id)
    assert client.get(BASE).status_code == 403
    login(client, cockpit["id"])
    with SessionLocal() as db:
        setting(db, "admin_2fa_enabled", "true")
    response = client.get(BASE, follow_redirects=False)
    assert response.status_code in (303, 302)
    assert "/mfa/verify" in response.headers["location"]
    assert client.get(BASE + "/teil/audit", headers={"HX-Request":"true"}).status_code == 403
    login(client, cockpit["id"], mfa_at=int(time.time()), session_started_at=int(time.time()))
    assert client.get(BASE).status_code == 200


@pytest.mark.parametrize("path", ["/start", "/codex/1", "/reparatur/vorschlagen", "/reparatur/simulieren",
    "/reparatur/ausfuehren", "/reparatur/verifizieren", "/einstellungen"])
def test_all_post_csrf(client, cockpit, path):
    assert client.post(BASE + path).status_code == 403


def test_start_polling_active_and_hour_limit(client, cockpit):
    for i in range(6):
        response = post(client, "/start", umfang="voll", check="worker.zustand")
        assert response.status_code == 200
        assert 'hx-trigger="every 2s"' in response.text
        with SessionLocal() as db:
            row = db.scalar(select(DiagRun).where(DiagRun.active_slot == 1))
            assert row.quelle == "manual" and row.checks == ["worker.zustand"]
            ident = row.id
        assert "Rate-Limit erreicht" in post(client, "/start").text
        assert f"Lauf #{ident}" in client.get(BASE + "/teil/diagnose").text
        with SessionLocal() as db:
            row = db.get(DiagRun, ident)
            row.active_slot, row.status = None, "done"
            db.commit()
    assert "Rate-Limit erreicht" in post(client, "/start").text
    assert post(client, "/start", check="unknown").status_code == 400


def make_finding(db, severity="fail", key=None):
    run = DiagRun(status="done", active_slot=None, korrelations_id=str(uuid4()))
    db.add(run)
    db.flush()
    row = DiagFinding(run_id=run.id, finding_key=key or uuid4().hex*2, check_id="gateway.jobs", modul="gateway",
        schweregrad=severity, titel="Gateway token=PRIVATE Alice Muster alice@example.org",
        evidenz={"Alice Muster": "PRIVATE", "anzahl": 3}, betroffene_ids={"objekt": [123]},
        ursache_hypothese="Alice Muster PRIVATE", empfehlung="password=PRIVATE Alice Muster")
    db.add(row)
    db.commit()
    return row


def test_filters_detail_codex_audit_masking(client, cockpit, monkeypatch):
    from app.admin_mcp import code_tools
    from app.services.github_readonly import unavailable
    monkeypatch.setattr(code_tools.GitHubReadonly, "compare", lambda *a: unavailable("kein_token"))
    with SessionLocal() as db:
        row = make_finding(db)
        ident = row.id
        db.add(AuditLog(action="diag.test", payload_json='{"token":"PRIVATE", "name":"Alice Muster"}'))
        db.commit()
    response = client.get(BASE + "/teil/befunde?modul=gateway&schweregrad=fail&status=offen")
    assert f"#{ident}" in response.text
    assert f"#{ident}" not in client.get(BASE + "/teil/befunde?schweregrad=info").text
    assert client.get(BASE + "/teil/befunde?modul=unknown").status_code == 400
    for path in ("/teil/befunde", f"/befund/{ident}", "/teil/audit"):
        response = client.get(BASE + path)
        assert response.status_code == 200
        assert all(value not in response.text for value in ("Alice", "Muster", "PRIVATE", "alice@example.org"))
    response = post(client, f"/codex/{ident}")
    assert response.status_code == 200
    assert code_tools.PFLICHTSATZ in response.text
    assert "PRIVATE" not in response.text


def test_settings_protected_and_default_off(client, cockpit):
    response = client.get("/admin/system-einstellungen")
    assert response.status_code == 200 and "Systemdiagnose" in response.text
    for key in diag_schedule.DEFAULTS:
        response = client.post("/admin/system-einstellungen", data={"_csrf":client.cookies.get("ec_csrf"),
            "new_key":key, "new_value":"true"})
        assert response.status_code == 400
    values = {**diag_schedule.DEFAULTS, "diag_schedule_time":"08:35"}
    assert post(client, "/einstellungen", **values).status_code == 200
    assert post(client, "/einstellungen", **{**values,"diag_schedule_time":"25:00"}).status_code == 400
    with SessionLocal() as db:
        assert diag_schedule.configuration(db)["diag_schedule_enabled"] == "false"


def test_repair_switches_dry_run_and_stepup(client, cockpit):
    with SessionLocal() as db:
        ident = make_finding(db).id
    assert post(client, "/reparatur/vorschlagen", finding_id=ident).status_code == 400
    with SessionLocal() as db:
        setting(db, "admin_2fa_enabled", "true")
        setting(db, "admin_repair_enabled", "true")
    login(client, cockpit["id"], mfa_at=int(time.time()), session_started_at=int(time.time()))
    assert post(client, "/reparatur/vorschlagen", finding_id=ident).status_code == 200
    with SessionLocal() as db:
        run = db.scalar(select(AdminRepairRun).where(AdminRepairRun.angefordert_von == cockpit["id"]))
        run_id = run.id
        assert run.korrelations_id.startswith("web:")
    import json
    response = post(client, "/reparatur/simulieren", run_id=run_id,
                    scope=json.dumps({"org_id":1,"alarm_ids":[999999]}))
    assert response.status_code == 200 and "Vorschau" in response.text
    with SessionLocal() as db:
        run = db.get(AdminRepairRun, run_id)
        assert run.status == "simuliert"
        assert run.simulation_hash
    assert "Bestätigung" in response.text or "bestätigen" in response.text
