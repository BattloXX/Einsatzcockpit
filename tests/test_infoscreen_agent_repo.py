"""S9: Repo-Vertrag und Freigabe ohne Pakete."""
import shlex
from datetime import datetime

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenRelease
from app.services import infoscreen_agent_update_check as service
from app.services.infoscreen_agent_installer_cmd import installer_command
from tests.conftest import TestingSession as SessionLocal
from tests.test_infoscreen_agent_releases import BASE, paired


@pytest.fixture
def setup(client, monkeypatch):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for row in db.query(InfoscreenRelease).all():
            db.delete(row)
        db.commit()
    device_id, headers = paired(client)
    policy = {"maintenance_window": {"start": "22:00", "end": "06:00", "timezone": "Europe/Vienna"},
              "critical_allowed": False, "check_interval_h": 6, "pinned_version": None, "update_allowed": True}
    monkeypatch.setattr(service.releases, "update_policy", lambda *args: policy)
    return device_id, headers, policy


def post(client, headers, **changes):
    body = {"version": "2.0", "current": "1.0", "channel": "stable", "arch": "amd64"}
    body.update(changes)
    return client.post(BASE + "/update/check", headers=headers, json=body)


def test_discovery_and_strict_contract(client, setup):
    device_id, headers, _ = setup
    response = post(client, headers)
    assert response.status_code == 200, response.text
    assert response.json()["reason"] == "rollout"
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"allowed", "reason", "retry_after_s", "maintenance_window",
                                   "critical_allowed", "check_interval_h", "pinned_version"}
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(InfoscreenRelease).filter_by(version="2.0").one()
        assert row.storage_path is None and row.signature is None and row.rollout_percent == 0
        assert db.get(InfoscreenDevice, device_id).last_update_check_reason == "rollout"
    for changes in ({"version": "bad"}, {"version": 123}, {"extra": "x"}, {"current": "$(id)"}):
        assert post(client, headers, **changes).status_code == 422
    for changes in ({"arch": "arm64"}, {"channel": "beta"}, {"version": "0.9"}, {"version": "1.0"}):
        assert post(client, headers, **changes).json()["reason"] == "unknown"


def test_discovery_limits(client, setup, monkeypatch):
    device_id, headers, _ = setup
    assert post(client, headers, version="9999.0").json()["reason"] == "unknown"
    for version in ("2.0", "2.1", "2.2"):
        assert post(client, headers, version=version).json()["reason"] == "rollout"
    assert post(client, headers, version="2.3").json()["reason"] == "unknown"
    assert post(client, headers, version="2.0").json()["reason"] == "rollout"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(InfoscreenDevice, device_id).release_discovery_count == 3
        assert db.query(InfoscreenRelease).count() == 3
        device = db.get(InfoscreenDevice, device_id)
        device.release_discovery_count = 0
        db.commit()
    monkeypatch.setattr(service, "MAX_RELEASES", 3)
    assert post(client, headers, version="2.4").json()["reason"] == "unknown"


@pytest.mark.parametrize("blocked,locked,pin,quiet,rollout,critical,critical_allowed,expected", [
    (True, True, "3.0", False, 0, False, False, "blocked"),
    (False, True, "3.0", False, 0, False, False, "locked"),
    (False, False, "3.0", False, 0, False, False, "pinned"),
    (False, False, "2.0", False, 0, True, True, "incident"),
    (False, False, None, True, 0, True, True, "rollout"),
    (False, False, None, True, 100, False, True, "window"),
    (False, False, None, True, 100, True, False, "window"),
    (False, False, "2.0", True, 100, True, True, "ok"),
])
def test_decision_priority(client, setup, blocked, locked, pin, quiet, rollout, critical, critical_allowed, expected):
    device_id, _, policy = setup
    policy.update(update_allowed=quiet, critical_allowed=critical_allowed, pinned_version=pin)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        device = db.get(InfoscreenDevice, device_id)
        device.update_locked, device.update_pinned_version = locked, pin
        db.add(InfoscreenRelease(version="2.0", channel="stable", arch="amd64", blocked=blocked,
                                rollout_percent=rollout, critical=critical))
        db.commit()
        result = service.check(db, device, "2.0", "stable", "amd64", "1.0", datetime(2026, 11, 7, 12))
        assert result["reason"] == expected
        assert result["allowed"] == (expected == "ok")
        if expected in ("incident", "window"):
            assert result["retry_after_s"] > 0


@pytest.mark.parametrize("hour,inside", [(20, False), (21, True), (0, True), (4, True), (5, False)])
def test_midnight_timezone(hour, inside):
    window = {"start": "22:00", "end": "06:00", "timezone": "Europe/Vienna"}
    assert service.window_status(window, datetime(2026, 11, 7, hour))[0] == inside


def test_bucket_stable():
    assert service.releases.rollout_bucket(12, "2.0") == service.releases.rollout_bucket(12, "2.0")
    assert len({service.releases.rollout_bucket(i, "2.0") for i in range(200)}) > 50


@pytest.mark.parametrize("mode", ["github", "managed"])
@pytest.mark.parametrize("env", [True, False])
def test_installer_modes(monkeypatch, mode, env):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", mode)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://ecp.example")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_REPO", "Owner/Agent")
    monkeypatch.setattr("app.services.infoscreen_agent_installer_cmd.release_fingerprint", lambda: "")
    command = installer_command("CODE' ; $(id)", "Halle `id`", environment=env)
    curl, bash = map(shlex.split, command.split(" | "))
    assert curl[-1] == ("https://github.com/Owner/Agent/releases/latest/download/install.sh" if mode == "github"
                        else "https://ecp.example/infoscreen-agent/install.sh")
    assert ("--token" in bash) == (mode == "github" and not env)
    assert ("--pairing-code" in bash) == (mode == "managed" and not env)
    assert "--key-fingerprint" not in bash
    assert bash[-1] == "Halle `id`"
    if mode == "github":
        monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_REPO", "Owner/Agent;id")
        with pytest.raises(ValueError):
            installer_command("CODE")


def test_auth(client, setup):
    _, headers, _ = setup
    assert post(client, {}).status_code == 401
    assert post(client, {"Authorization": "Bearer invalid"}).status_code == 401
    assert post(client, headers).status_code == 200


def test_rate_limit_per_device(client, setup, caplog):
    _, headers, _ = setup
    for _ in range(12):
        assert post(client, headers).status_code == 200
    response = post(client, headers)
    assert response.status_code == 429 and response.headers["cache-control"] == "no-store"
    _, second = paired(client)
    assert post(client, second).status_code == 200
    assert headers["Authorization"].split()[1] not in caplog.text


def test_day_reset_and_concurrent_discovery(client, setup):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta

    device_id, _, _ = setup
    now = datetime(2026, 11, 7, 12)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        device = db.get(InfoscreenDevice, device_id)
        device.release_discovery_day = (now - timedelta(days=1)).date()
        device.release_discovery_count = 3
        db.commit()

    def discover():
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            device = db.get(InfoscreenDevice, device_id)
            result = service.check(db, device, "2.0", "stable", "amd64", "1.0", now)
            db.commit()
            return result["reason"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: discover(), range(2))) == ["rollout", "rollout"]
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(InfoscreenRelease).count() == 1
        assert db.get(InfoscreenDevice, device_id).release_discovery_count == 1


def test_release_ui_roles_csrf_audit_event(client, setup, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models.user import AuditLog
    from app.services import infoscreen_agent_events as events
    from tests.test_infoscreen_agent_releases import login, post as ui_post

    device_id, headers, _ = setup
    login(client, "org_admin")
    assert ui_post(client, "/admin/agent-releases/versions/create").status_code == 403
    login(client, "system_admin")
    path = "/admin/agent-releases/versions/create"
    assert client.post(path, data={"version": "2.0", "channel": "stable", "arch": "amd64"}).status_code == 403
    assert ui_post(client, path, {"version": "bad", "channel": "stable", "arch": "amd64"}).status_code == 422
    response = ui_post(client, path, {"version": "2.0", "channel": "stable", "arch": "amd64"})
    assert response.status_code == 200 and "<table" in response.text
    assert '<details><summary>Verwalteter Modus (optional)</summary>' in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row_id = db.query(InfoscreenRelease).filter_by(version="2.0").one().id
        assert db.query(AuditLog).filter_by(action="infoscreen_agent.release_created", entity_id=str(row_id)).count()
    assert ui_post(client, path, {"version": "2.0", "channel": "stable", "arch": "amd64"}).status_code == 409
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_update_available", publish)
    response = ui_post(client, f"/admin/agent-releases/{row_id}/policy",
                       {"rollout_percent": "100", "critical": "1"})
    assert response.status_code == 200
    assert any(call.args == (1, device_id) for call in publish.await_args_list)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(AuditLog).filter_by(action="infoscreen_agent.release_policy", entity_id=str(row_id)).count()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", "managed")
    assert '<summary>Verwalteter Modus (optional)</summary>' not in client.get('/admin/agent-releases').text


def test_github_pairing_no_installer_gate(client, monkeypatch):
    from tests.test_infoscreen_agent_admin import BASE as ADMIN_BASE
    from tests.test_infoscreen_agent_releases import login, post as ui_post

    login(client, "org_admin")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", "github")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://safe.example")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", False)
    response = ui_post(client, ADMIN_BASE + "/geraete/neu", {"name": "Repo-Geraet"})
    assert response.status_code == 200
    assert "Installer noch nicht freigeschaltet" not in response.text
    assert "github.com/" in response.text and "--token" in response.text
