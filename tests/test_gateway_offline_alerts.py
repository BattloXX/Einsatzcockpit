"""Gateway-Opt-out nutzt die bestehenden Ausfallkanaele und laesst Status/Leases unveraendert."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.dienst_monitor import DienstStatus
from app.models.gateway import Gateway
from app.models.master import FireDept, OrgSettings
from app.services.dienst_monitor_service import entscheide, pruefe_dienste


@pytest.fixture
def db(client):
    connection = SessionLocal()
    set_tenant_context(connection, None)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def monitor_org(db):
    org = FireDept(slug="offline-" + uuid4().hex[:12], name="Optout-Test", timezone="Europe/Vienna")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, dienst_monitor_enabled=True, dienst_monitor_karenz_min=5,
                       dienst_monitor_wiederholung_min=60, dienst_monitor_sms="+43000000000",
                       dienst_monitor_teams_webhook_url="https://example.test/test-only"))
    db.commit()
    return org.id


def gateway(db, org_id, *, name="Wichtig", kind="linux", online=False, enabled=True):
    now = datetime.now(UTC).replace(tzinfo=None)
    row = Gateway(org_id=org_id, name=name, kind=kind, device_token_hash=uuid4().hex,
                  last_seen_at=now if online else now - timedelta(hours=1),
                  status="online" if online else "offline", offline_alert_enabled=enabled)
    if online and kind == "windows_agent":
        row.agent_connection_id = uuid4().hex
        row.agent_presence_expires_at = now + timedelta(seconds=90)
    db.add(row)
    db.commit()
    return row


def print_check(db, org_id):
    return next(check for check in pruefe_dienste(db, org_id) if check.key == "print_gateway")


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
def test_optout_is_excluded_but_gateway_status_stays_offline(db, monitor_org, kind):
    main = gateway(db, monitor_org, kind=kind, online=True)
    optional = gateway(db, monitor_org, name="Optional", kind=kind, enabled=False)
    check = print_check(db, monitor_org)
    assert check.state == "ok"
    assert [part.ref for part in check.teile] == [f"gateway:{main.id}"]
    assert optional.status == "offline"
    assert optional.device_token_hash
    optional.offline_alert_enabled = True
    db.commit()
    check = print_check(db, monitor_org)
    assert check.state == "teilweise"
    assert "Optional" in check.detail
    assert "Wichtig" not in check.detail


def test_windows_monitor_uses_presence_not_old_inventory_or_last_seen(db, monitor_org):
    row = gateway(db, monitor_org, kind="windows_agent", online=True)
    row.agent_presence_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    row.agent_info = {"reported_at": datetime.now(UTC).isoformat(), "queues": [{"queue": "HP"}]}
    db.commit()
    assert print_check(db, monitor_org).state == "down"


def test_all_opted_out_has_no_outage_or_recovery(db, monitor_org):
    gateway(db, monitor_org, enabled=False)
    check = print_check(db, monitor_org)
    assert not check.relevant
    assert check.state == "unknown"
    previous = DienstStatus(org_id=monitor_org, key="print_gateway", fail_cycles=3, ok_cycles=0,
                            outage_notified_at=datetime.now(UTC).replace(tzinfo=None))
    assert entscheide(check, previous, 0, 1, datetime.now(UTC)).art is None


def test_optout_does_not_disable_serial_alarm(db, monitor_org):
    row = gateway(db, monitor_org, enabled=False, online=True)
    row.wut_config = {"host": "test-wt"}
    row.serial_connected = False
    db.commit()
    checks = {check.key: check for check in pruefe_dienste(db, monitor_org)}
    assert not checks["print_gateway"].relevant
    assert checks["alarm_seriell"].state == "down"


@pytest.mark.asyncio
async def test_selected_outage_uses_existing_sms_and_teams_once(db, monitor_org, monkeypatch):
    from app.services.dienst_monitor_loop import _process_org

    gateway(db, monitor_org)
    gateway(db, monitor_org, name="Optional", enabled=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add(DienstStatus(org_id=monitor_org, key="print_gateway", state="down", fail_cycles=2, ok_cycles=0,
                       down_since=now - timedelta(minutes=10), since=now - timedelta(minutes=10)))
    db.commit()
    sms = AsyncMock(return_value=SimpleNamespace(success=True))
    teams = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.sms_service.send_sms", sms)
    monkeypatch.setattr("app.services.teams_service.post_teams_karte", teams)
    await _process_org(monitor_org)
    assert sms.await_count == teams.await_count == 1
    assert "Wichtig" in str(sms.call_args)
    assert "Optional" not in str(sms.call_args)
    assert "Optional" not in str(teams.call_args)
    await _process_org(monitor_org)
    assert sms.await_count == teams.await_count == 1


@pytest.mark.asyncio
async def test_optout_blocks_all_notification_channels(db, monitor_org, monkeypatch):
    from app.services.dienst_monitor_loop import _process_org

    gateway(db, monitor_org, enabled=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add(DienstStatus(org_id=monitor_org, key="print_gateway", state="down", fail_cycles=3,
                       down_since=now - timedelta(minutes=10), outage_notified_at=now,
                       last_repeat_at=now - timedelta(hours=2)))
    db.commit()
    dispatch = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.dienst_monitor_dispatch.dispatch_meldung", dispatch)
    await _process_org(monitor_org)
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("kind", ["linux", "windows_agent"])
def test_ui_saves_optout_and_resets_group_without_false_recovery(client, db, kind):
    from tests.test_print_agent_ui import _admin
    csrf = _admin(client, "offline_ui_" + kind)
    row = gateway(db, 1, kind=kind)
    check = db.query(DienstStatus).filter(DienstStatus.org_id == 1, DienstStatus.key == "print_gateway").first()
    if check is None:
        check = DienstStatus(org_id=1, key="print_gateway")
        db.add(check)
    now = datetime.now(UTC).replace(tzinfo=None)
    check.state = "down"
    check.down_since = now - timedelta(minutes=10)
    check.outage_notified_at = now
    check.last_repeat_at = now
    check.fail_cycles = 4
    db.commit()
    response = client.get(f"/gateway/{row.id}")
    assert "Bei Offline alarmieren" in response.text
    assert 'name="enabled" value="true" checked' in response.text
    response = client.post(f"/gateway/{row.id}/offline-alarme", data={"_csrf": csrf},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Ausfallalarmierung gespeichert" in response.text
    assert "location.reload" not in response.text
    db.expire_all()
    assert db.get(Gateway, row.id).offline_alert_enabled is False
    assert db.get(Gateway, row.id).status == "offline"
    assert check.down_since is None
    assert check.outage_notified_at is None
    assert check.fail_cycles == 0
    assert "Offline-Alarme aus" in client.get("/gateway").text
    response = client.post(f"/gateway/{row.id}/offline-alarme", data={"_csrf": csrf, "enabled": "true"},
                           follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Gateway, row.id).offline_alert_enabled is True


def test_saving_unchanged_selection_keeps_existing_outage_clock(client, db):
    from tests.test_print_agent_ui import _admin
    csrf = _admin(client, "offline_idempotent")
    row = gateway(db, 1)
    status = db.query(DienstStatus).filter(DienstStatus.org_id == 1, DienstStatus.key == "print_gateway").first()
    now = datetime.now(UTC).replace(tzinfo=None)
    if status is None:
        status = DienstStatus(org_id=1, key="print_gateway")
        db.add(status)
    status.down_since = now
    db.commit()
    response = client.post(f"/gateway/{row.id}/offline-alarme", data={"_csrf": csrf, "enabled": "true"})
    assert response.status_code == 200
    db.expire_all()
    assert status.down_since == now


def test_optout_permissions_tenant_isolation_and_csrf(client, db, monitor_org):
    from tests.test_gateway_druckregeln_render import _login, _setup
    from tests.test_print_agent_ui import _admin
    csrf = _admin(client, "offline_security")
    own, foreign = gateway(db, 1), gateway(db, monitor_org)
    assert client.post(f"/gateway/{foreign.id}/offline-alarme", data={"_csrf": csrf}).status_code == 404
    assert client.post(f"/gateway/{own.id}/offline-alarme", data={}).status_code == 403
    invalid = client.post(f"/gateway/{own.id}/offline-alarme", data={"_csrf": csrf, "enabled": "invalid"})
    assert invalid.status_code == 422
    _setup("offline_recorder", "recorder")
    _login(client, "offline_recorder", "Test1234!")
    assert client.post(f"/gateway/{own.id}/offline-alarme",
                       data={"_csrf": client.cookies.get("ec_csrf")}).status_code == 403
    db.expire_all()
    assert own.offline_alert_enabled is True
    assert foreign.offline_alert_enabled is True


def test_offline_alert_optout_does_not_disable_print_routes(db):
    from app.services.print_agent_service import route_kandidaten
    from tests.test_print_agent_service import NOW, _kommandofunk

    first, _second, printer, first_route, second_route = _kommandofunk(db)
    first.offline_alert_enabled = False
    db.commit()
    assert [route.id for route in route_kandidaten(db, printer, NOW)] == [first_route.id, second_route.id]
