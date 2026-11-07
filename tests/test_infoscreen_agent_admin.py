"""S4: Admin-Rechte, Tenant-Isolation, CRUD, Pairing und nebenwirkungsfreie Vorschau."""

import re
import shlex
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import (
    InfoscreenDevice,
    InfoscreenDisplay,
    InfoscreenSchedule,
    InfoscreenScheduleException,
    InfoscreenScheduleSlot,
)
from app.models.objekt import AlarmInfoscreenToken, InfoscreenUrl
from app.services import infoscreen_agent_events as events
from app.services.infoscreen_agent_installer_cmd import installer_command
from tests.conftest import TestingSession as SessionLocal
from tests.test_systemstatus_nav import _login, _make_org_admin

BASE = "/admin/infoscreen-agent"


@pytest.fixture
def admin(client):
    username = "agent-s4-" + uuid4().hex[:10]
    org_id = _make_org_admin(username, username, "org_admin")
    _login(client, username, "Test1234!")
    return org_id


def post(client, path, data=None):
    return client.post(
        BASE + path, data={"_csrf": client.cookies.get("ec_csrf"), **(data or {})}, headers={"HX-Request": "true"}
    )


def create_device(client):
    response = post(client, "/geraete/neu", {"name": "Halle"})
    assert response.status_code == 200, response.text
    code = re.search(r"Pairing-Code: <strong>([A-Z0-9]{8})</strong>", response.text)[1]
    device_id = int(re.search(r"/geraete/(\d+)/status", response.text)[1])
    return device_id, code, response


def create_plan(client):
    response = post(
        client, "/plaene/0/speichern", {"name": "Wochenplan", "timezone": "Europe/Vienna", "ruhemodus": "aus"}
    )
    assert response.status_code == 200, response.text
    return int(re.search(r"/plaene/([1-9]\d*)/speichern", response.text)[1])


def slot_data(schedule_id, **kwargs):
    return {
        "schedule_id": str(schedule_id),
        "days": ["0", "1"],
        "start_time": "08:00",
        "end_time": "18:00",
        "power": "on",
        "priority": "0",
        "rotation_json": '[{"builtin":"weather","dwell_sec":30}]',
        **kwargs,
    }


def create_display(client, device_id, schedule_id="", token_id=""):
    response = post(
        client,
        "/displays/0/speichern",
        {
            "device_id": str(device_id),
            "display_key": "main",
            "name": "Hauptmonitor",
            "schedule_id": str(schedule_id),
            "alarm_token_id": str(token_id),
            "power_mode": "logical_blank",
            "aktiv": "1",
            "nachlauf_min": "10",
            "offline_wake_after_min": "5",
        },
    )
    assert response.status_code == 200, response.text
    return int(re.search(r"/displays/(\d+)/speichern", response.text)[1])


@pytest.mark.parametrize("environment", [False, True])
@pytest.mark.parametrize("fingerprint", ["", "sha256:123abc"])
def test_installer_quote_and_configuration(monkeypatch, environment, fingerprint):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", "managed")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://configured.example")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_KEY_FINGERPRINT", fingerprint)
    name = 'Halle \' ; $(touch /tmp/no) `echo nein` & "West"'
    command = installer_command("ABCD1234", name, environment=environment)
    curl, bash = command.split(" | ")
    assert shlex.split(curl) == ["curl", "-fsSL", "https://configured.example/infoscreen-agent/install.sh"]
    args = shlex.split(bash)
    assert args[-2:] == ["--name", name]
    assert ("--key-fingerprint" in args) == bool(fingerprint)
    assert ("EC_PAIRING_CODE=ABCD1234" in args) == environment
    assert ("--pairing-code" in args) != environment
    assert args[args.index("--server") + 1] == "https://configured.example"


def test_pairing_dialog_status_host_audit_and_code_refresh(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", "managed")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://configured.example")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", False)
    device_id, code, response = create_device(client)
    assert "Installer noch nicht freigeschaltet" in response.text
    assert "every 5s" in response.text and "https://configured.example/infoscreen-agent/install.sh" in response.text
    status = client.get(BASE + f"/geraete/{device_id}/status", headers={"Host": "evil.example"})
    assert "Wartet auf Kopplung" in status.text
    assert code not in status.text
    assert "no-store" in response.headers["cache-control"]
    paired = client.post("/api/v1/infoscreen-agent/pair", json={"code": code})
    assert paired.status_code == 200, paired.text
    token = paired.json()["device_token"]
    response = client.get(BASE + f"/geraete/{device_id}/status")
    assert "gekoppelt" in response.text and token not in response.text
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        device = db.get(InfoscreenDevice, device_id)
        device.agent_presence_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=60)
        db.commit()
    assert "verbunden" in client.get(BASE + f"/geraete/{device_id}/status").text
    response = post(client, f"/geraete/{device_id}/rotieren")
    assert response.status_code == 200 and token not in response.text
    assert (
        client.post(
            "/api/v1/infoscreen-agent/heartbeat", json={}, headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        device = db.get(InfoscreenDevice, device_id)
        assert device.status == "pending" and device.device_token_hash is None
        from app.models.user import AuditLog

        audits = db.query(AuditLog).filter(AuditLog.action == "infoscreen_agent.pairing_created").all()
        assert audits and all(code not in str(a.payload_json) for a in audits)


def test_crud_preview_no_revision_and_broadcast(client, admin, monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_config_changed", publish)
    device_id, code, _ = create_device(client)
    client.post("/api/v1/infoscreen-agent/pair", json={"code": code})
    schedule_id = create_plan(client)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        from app.core.crypto import encrypt_secret

        token = AlarmInfoscreenToken(
            org_id=admin, name="Monitor", token_hash="a" * 64, token_enc=encrypt_secret("test"), aktiv=True
        )
        db.add(token)
        db.commit()
        token_id = token.id
    display_id = create_display(client, device_id, schedule_id, token_id)
    response = post(client, "/slots/0/speichern", slot_data(schedule_id))
    assert response.status_code == 200, response.text
    assert 'aria-label="Wochenraster' in response.text
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        slot_id = db.query(InfoscreenScheduleSlot.id).filter_by(schedule_id=schedule_id).scalar()
        version = db.get(InfoscreenDevice, device_id).config_version
        revision = db.get(InfoscreenDisplay, display_id).revision
    response = client.get(BASE + "/vorschau", params={"display_id": display_id, "weekday": 0, "clock": "10:00"})
    assert response.status_code == 200 and "Zeitplan" in response.text and "Wetter" in response.text
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert db.get(InfoscreenDevice, device_id).config_version == version
        assert db.get(InfoscreenDisplay, display_id).revision == revision
    assert post(client, f"/slots/{slot_id}/speichern", slot_data(schedule_id, label="Geaendert")).status_code == 200
    response = post(
        client,
        "/ausnahmen/0/speichern",
        {"schedule_id": schedule_id, "datum": "2026-12-24", "label": "Feiertag", "slots_ersatz": "[]"},
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        exception_id = db.query(InfoscreenScheduleException.id).filter_by(schedule_id=schedule_id).scalar()
    replacement = '[{"start_time":"20:00","end_time":"02:00","power":"off","rotation_json":[]}]'
    assert (
        post(
            client,
            f"/ausnahmen/{exception_id}/speichern",
            {"schedule_id": schedule_id, "datum": "2026-12-24", "slots_ersatz": replacement},
        ).status_code
        == 200
    )
    assert post(client, f"/ausnahmen/{exception_id}/loeschen", {"confirm": "ja"}).status_code == 200
    assert post(client, f"/slots/{slot_id}/loeschen", {"confirm": "ja"}).status_code == 200
    assert post(client, f"/plaene/{schedule_id}/loeschen", {"confirm": "ja"}).status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert db.get(InfoscreenDisplay, display_id).schedule_id is None
        assert db.get(InfoscreenDevice, device_id).config_version > version
    assert publish.await_count >= 8
    assert any(call.args == (admin, [display_id]) for call in publish.await_args_list)
    assert post(client, f"/displays/{display_id}/loeschen", {"confirm": "ja"}).status_code == 200
    assert post(client, f"/geraete/{device_id}/loeschen", {"confirm": "ja"}).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"start_time": "18:00"},
        {"days": []},
        {"priority": "1001"},
        {"rotation_json": '[{"builtin":"weather","dwell_sec":4}]'},
        {"rotation_json": '[{"builtin":"weather","dwell_sec":3601}]'},
        {"rotation_json": '[{"url_id":999999,"dwell_sec":30}]'},
        {"rotation_json": '[{"builtin":"arbitrary","dwell_sec":30}]'},
        {"rotation_json": "[1]"},
        {"rotation_json": "{}"},
        {"rotation_json": "["},
        {"valid_from": "2026-12-31", "valid_to": "2026-01-01"},
    ],
)
def test_slot_validation(client, admin, change):
    schedule_id = create_plan(client)
    response = post(client, "/slots/0/speichern", slot_data(schedule_id, **change))
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert not db.query(InfoscreenScheduleSlot).filter_by(schedule_id=schedule_id).count()


def test_overlap_midnight_and_acknowledgment(client, admin):
    schedule_id = create_plan(client)
    assert (
        post(
            client, "/slots/0/speichern", slot_data(schedule_id, days=["6"], start_time="22:00", end_time="03:00")
        ).status_code
        == 200
    )
    response = post(
        client, "/slots/0/speichern", slot_data(schedule_id, days=["0"], start_time="02:00", end_time="04:00")
    )
    assert response.status_code == 422 and "ueberlappen" in response.text
    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(schedule_id, days=["0"], start_time="02:00", end_time="04:00", ack_overlap="1"),
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(schedule_id, days=["0"], start_time="02:00", end_time="04:00", priority="1"),
        ).status_code
        == 200
    )


@pytest.mark.parametrize("zone", ["Madeup/Vienna", "", "../etc/passwd"])
def test_invalid_timezone(client, admin, zone):
    assert (
        post(client, "/plaene/0/speichern", {"name": "Test", "timezone": zone, "ruhemodus": "aus"}).status_code == 422
    )


GETS = ["", "/geraete", "/displays", "/plaene", "/geraete/999999/status", "/vorschau?display_id=999999"]
POSTS = [
    "/geraete/neu",
    *[
        f"/geraete/999999/{action}"
        for action in ("umbenennen", "sperren", "entsperren", "rotieren", "code", "loeschen")
    ],
    *[
        f"/{entity}/999999/{action}"
        for entity in ("displays", "plaene", "slots", "ausnahmen")
        for action in ("speichern", "loeschen")
    ],
]


@pytest.mark.parametrize("role", ["readonly", "recorder", "incident_leader", "fahrtenbuch_admin", "objekt_verwalter"])
def test_all_routes_require_org_admin(client, role):
    _make_org_admin("agent-no-" + role, "agent-no-" + role, role)
    _login(client, "agent-no-" + role, "Test1234!")
    for path in GETS:
        assert client.get(BASE + path).status_code == 403, path
    for path in POSTS:
        assert post(client, path).status_code == 403, path


@pytest.mark.parametrize("path", POSTS)
def test_all_posts_csrf(client, admin, path):
    assert client.post(BASE + path, data={"name": "unwanted"}).status_code == 403


def test_cross_org_all_object_routes(client, admin):
    other = _make_org_admin("agent-other", "agent-other", "org_admin")
    with SessionLocal() as db:
        set_tenant_context(db, other)
        device = InfoscreenDevice(org_id=other, name="SECRET", status="pending")
        schedule = InfoscreenSchedule(org_id=other, name="SECRET", timezone="Europe/Vienna", ruhemodus="aus")
        db.add_all([device, schedule])
        db.flush()
        display = InfoscreenDisplay(org_id=other, device_id=device.id, name="SECRET", display_key="secret")
        slot = InfoscreenScheduleSlot(
            org_id=other,
            schedule_id=schedule.id,
            weekdays=1,
            start_time=datetime.min.time(),
            end_time=datetime.max.time(),
        )
        exception = InfoscreenScheduleException(org_id=other, schedule_id=schedule.id, datum=datetime.now(UTC).date())
        url = InfoscreenUrl(org_id=other, label="SECRET", url="https://example.com", aktiv=True)
        token = AlarmInfoscreenToken(org_id=other, name="SECRET", token_hash="b" * 64, aktiv=True)
        db.add_all([display, slot, exception, url, token])
        db.commit()
        ids = {
            "geraete": device.id,
            "displays": display.id,
            "plaene": schedule.id,
            "slots": slot.id,
            "ausnahmen": exception.id,
        }
        token_id, url_id = token.id, url.id
    for entity, row_id in ids.items():
        for action in (
            ("umbenennen", "sperren", "entsperren", "rotieren", "code", "loeschen")
            if entity == "geraete"
            else ("speichern", "loeschen")
        ):
            assert post(client, f"/{entity}/{row_id}/{action}", {"confirm": "ja"}).status_code == 404
    assert client.get(BASE + f"/geraete/{ids['geraete']}/status").status_code == 404
    assert client.get(BASE + f"/vorschau?display_id={ids['displays']}").status_code == 404
    for path in ("geraete", "displays", "plaene"):
        assert "SECRET" not in client.get(BASE + "/" + path).text
    device_id, _, _ = create_device(client)
    local_schedule = create_plan(client)
    base_form = {"device_id": device_id, "display_key": "local", "name": "Local", "power_mode": "auto"}
    for change in ({"device_id": ids["geraete"]}, {"schedule_id": ids["plaene"]}, {"alarm_token_id": token_id}):
        assert post(client, "/displays/0/speichern", base_form | change).status_code == 404
    assert post(client, "/slots/0/speichern", slot_data(ids["plaene"])).status_code == 404
    assert post(client, "/ausnahmen/0/speichern", {"schedule_id": ids["plaene"]}).status_code == 404
    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(local_schedule, rotation_json=f'[{"{"}"url_id":{url_id},"dwell_sec":30{"}"}]'),
        ).status_code
        == 422
    )


def test_block_disconnects_existing_socket(client, admin):
    device_id, code, _ = create_device(client)
    token = client.post("/api/v1/infoscreen-agent/pair", json={"code": code}).json()["device_token"]
    with client.websocket_connect("/ws/infoscreen-agent", headers={"Authorization": "Bearer " + token}) as socket:
        assert socket.receive_json() == {"type": "ping"}
        response = post(client, f"/geraete/{device_id}/sperren")
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        device = db.get(InfoscreenDevice, device_id)
        assert device.status == "revoked" and device.device_token_hash is None
        assert device.agent_connection_id is None


def test_display_token_unique_and_inactive_url_rejected(client, admin):
    device_id, _, _ = create_device(client)
    schedule_id = create_plan(client)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        token = AlarmInfoscreenToken(org_id=admin, name="Exklusiv", token_hash=uuid4().hex * 2, aktiv=True)
        active_url = InfoscreenUrl(org_id=admin, label="Aktive Seite", url="https://example.com/active", aktiv=True)
        inactive_url = InfoscreenUrl(
            org_id=admin, label="Inaktive Seite", url="https://example.com/inactive", aktiv=False
        )
        db.add_all([token, active_url, inactive_url])
        db.commit()
        token_id, active_id, inactive_id = token.id, active_url.id, inactive_url.id
    display_id = create_display(client, device_id, schedule_id, token_id)
    data = {
        "device_id": device_id,
        "display_key": "second",
        "name": "Zweiter",
        "power_mode": "auto",
        "alarm_token_id": token_id,
    }
    response = post(client, "/displays/0/speichern", data)
    assert response.status_code == 422 and "bereits" in response.text
    assert (
        post(
            client,
            f"/displays/{display_id}/speichern",
            data | {"display_key": "main", "alarm_token_id": "", "schedule_id": schedule_id},
        ).status_code
        == 200
    )
    assert post(client, "/displays/0/speichern", data).status_code == 200
    import json

    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(schedule_id, rotation_json=json.dumps([{"url_id": active_id, "dwell_sec": 5}])),
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(schedule_id, rotation_json=json.dumps([{"url_id": inactive_id, "dwell_sec": 30}]), priority="1"),
        ).status_code
        == 422
    )


def test_exception_overlap_limits_and_device_actions(client, admin, monkeypatch):
    import json
    from app.services import infoscreen_agent_admin as validation

    device_id, code, _ = create_device(client)
    response = post(client, f"/geraete/{device_id}/code")
    assert response.status_code == 200 and code not in response.text
    assert client.post("/api/v1/infoscreen-agent/pair", json={"code": code}).status_code == 401
    assert post(client, f"/geraete/{device_id}/umbenennen", {"name": "Neue Halle"}).status_code == 200
    assert "Neue Halle" in client.get(BASE).text
    assert post(client, f"/geraete/{device_id}/sperren").status_code == 200
    assert post(client, f"/geraete/{device_id}/code").status_code == 422
    assert post(client, f"/geraete/{device_id}/entsperren").status_code == 200
    assert post(client, f"/geraete/{device_id}/loeschen").status_code == 422
    schedule_id = create_plan(client)
    replacement = {"start_time": "22:00", "end_time": "02:00", "power": "off", "priority": 1}
    data = {"schedule_id": schedule_id, "datum": "2026-12-25", "slots_ersatz": json.dumps([replacement, replacement])}
    response = post(client, "/ausnahmen/0/speichern", data)
    assert response.status_code == 422 and "ueberlappen" in response.text
    assert post(client, "/ausnahmen/0/speichern", data | {"ack_overlap": "1"}).status_code == 200
    assert post(client, "/ausnahmen/0/speichern", data | {"ack_overlap": "1"}).status_code == 422
    monkeypatch.setattr(validation, "MAX_SLOTS", 1)
    assert post(client, "/slots/0/speichern", slot_data(schedule_id)).status_code == 200
    assert post(client, "/slots/0/speichern", slot_data(schedule_id, priority="1")).status_code == 422
    assert (
        post(
            client,
            "/slots/0/speichern",
            slot_data(schedule_id, rotation_json=json.dumps([{"builtin": "weather", "dwell_sec": 30}] * 31)),
        ).status_code
        == 422
    )


def test_installer_ignores_request_host_and_enabled_banner(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_DISTRIBUTION", "managed")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://safe.example")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", True)
    response = client.post(
        BASE + "/geraete/neu",
        data={"name": "Host-Test", "_csrf": client.cookies.get("ec_csrf")},
        headers={"Host": "evil.example", "HX-Request": "true"},
    )
    assert response.status_code == 200, response.text
    assert "https://safe.example/infoscreen-agent/install.sh" in response.text
    assert "evil.example" not in response.text
    # Der harte Schalter allein reicht seit S6 ohne gueltige Artefakte nicht.
    assert "Installer noch nicht freigeschaltet" in response.text


def test_pairing_status_and_default_preview_use_org_timezone(client, admin):
    from app.core.timezones import format_local_datetime_sec
    from app.models.master import FireDept

    with SessionLocal() as db:
        set_tenant_context(db, admin)
        db.get(FireDept, admin).timezone = "Pacific/Auckland"
        db.commit()
    device_id, _, response = create_device(client)
    display_id = create_display(client, device_id)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        device = db.get(InfoscreenDevice, device_id)
        expected = format_local_datetime_sec(device.pairing_expires_at, db.get(FireDept, admin))
    assert expected in response.text
    assert expected in client.get(BASE + f"/geraete/{device_id}/status").text
    preview = client.get(BASE + "/vorschau", params={"display_id": display_id})
    assert preview.status_code == 200 and "Pacific/Auckland" in preview.text


def test_deleting_last_display_wakes_device_after_commit(client, admin):
    device_id, code, _ = create_device(client)
    token = client.post("/api/v1/infoscreen-agent/pair", json={"code": code}).json()["device_token"]
    display_id = create_display(client, device_id)
    with client.websocket_connect("/ws/infoscreen-agent", headers={"Authorization": "Bearer " + token}) as socket:
        assert socket.receive_json()["reason"] == "reconnect"
        assert socket.receive_json() == {"type": "ping"}
        assert post(client, f"/displays/{display_id}/loeschen", {"confirm": "ja"}).status_code == 200
        assert socket.receive_json() == {
            "type": "invalidate",
            "revision": 0,
            "reason": "config_changed",
            "display_key": "main",
        }
        with SessionLocal() as db:
            set_tenant_context(db, admin)
            assert db.get(InfoscreenDisplay, display_id) is None


def test_moving_display_wakes_previous_device(client, admin, monkeypatch):
    wake = AsyncMock()
    monkeypatch.setattr(events, "publish_device_config_changed", wake)
    first_id, _, _ = create_device(client)
    second_id, _, _ = create_device(client)
    display_id = create_display(client, first_id)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        before = db.get(InfoscreenDevice, first_id).config_version
    response = post(
        client,
        f"/displays/{display_id}/speichern",
        {"device_id": second_id, "name": "Neuer Monitor", "display_key": "new", "power_mode": "auto", "aktiv": "1"},
    )
    assert response.status_code == 200, response.text
    wake.assert_awaited_once_with(admin, first_id, "main", 0)
    with SessionLocal() as db:
        set_tenant_context(db, admin)
        assert db.get(InfoscreenDevice, first_id).config_version == before + 1
        assert db.get(InfoscreenDisplay, display_id).device_id == second_id
