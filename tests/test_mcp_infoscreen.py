"""MCP-Infoscreen: Live-Rechte, Org-Trennung, Datenschutz und schreibfreier Resolver."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.crypto import encrypt_secret
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDeviceEvent, InfoscreenDisplay, InfoscreenOverride
from app.models.master import OrgSettings
from app.models.objekt import AlarmInfoscreenToken
from app.models.user import UserRole
from app.models.weather import WeatherDashboardToken
from tests.test_mcp_objekte import _mcp, _rufe, _seed, _token

TOOLS = ("infoscreen_geraete_liste", "infoscreen_display_status")
SECRET = "SECRET-NOT-IN-RESULT"


def _device(org_id, *, status="active", recent=True):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        now = datetime.now(UTC).replace(tzinfo=None)
        d = InfoscreenDevice(org_id=org_id, name="Halle", status=status,
                             device_token_hash=hash_api_key(SECRET + str(org_id)),
                             pairing_code_hash=hash_api_key("CODE" + str(org_id)),
                             hostname="sensitive-host", last_ip="192.0.2.1", agent_version="1.2",
                             os_version="Ubuntu 24.04", channel="beta", update_status="failed",
                             update_error_code="health_failed", last_seen_at=now - timedelta(minutes=0 if recent else 5))
        db.add(d)
        db.flush()
        token = AlarmInfoscreenToken(org_id=org_id, name="Alarm", token_hash=hash_api_key(SECRET + str(org_id)),
                                    token_enc=encrypt_secret(SECRET + str(org_id)), aktiv=True)
        db.add(token)
        db.flush()
        display = InfoscreenDisplay(org_id=org_id, device_id=d.id, name="Monitor", display_key="HDMI-1",
                                    alarm_token_id=token.id, revision=7, resolution_hash="unchanged",
                                    reported_status={"power": "off", "page_state": "blank", "backend": "logical_blank",
                                                     "verified": False, "url": SECRET, "token": SECRET})
        db.add(display)
        db.commit()
        return d.id, display.id


@pytest.mark.parametrize("role,allowed", [("org_admin", True), ("admin", True), ("system_admin", True),
                                          ("readonly", False), ("recorder", False)])
def test_rollen_und_katalog(client, role, allowed):
    seed = _seed("isa-role-" + role, {"u": role})
    access = _token(client, seed, "u")
    listed = _mcp(client, access, "tools/list", {}, 1).text
    for tool in TOOLS:
        assert (tool in listed) == allowed
        result = _rufe(client, access, tool, **({"device_id": -1} if tool.endswith("status") else {}))
        if not allowed:
            assert "__fehler__" in result
        elif tool.endswith("liste"):
            assert result == {"geraete": []}


def test_status_cross_org_geheimnisse_und_keine_schreibzugriffe(client):
    seed = _seed("isa-own", {"u": "org_admin"})
    foreign = _seed("isa-foreign", {"u": "org_admin"})
    device_id, display_id = _device(seed["org_id"])
    foreign_id, _ = _device(foreign["org_id"])
    access = _token(client, seed, "u")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        # Auch inkonsistente Zuordnung darf keine fremden Displays offenlegen.
        db.add(InfoscreenDisplay(org_id=foreign["org_id"], device_id=device_id, name=SECRET, display_key="foreign"))
        before_events = db.query(InfoscreenDeviceEvent).count()
        db.commit()
    devices = _rufe(client, access, TOOLS[0])["geraete"]
    assert len(devices) == 1
    assert devices[0] == dict(id=device_id, name="Halle", status="online", agent_version="1.2",
                              os_version="Ubuntu 24.04", channel="beta", update_status="failed",
                              update_error_code="health_failed", last_heartbeat=devices[0]["last_heartbeat"],
                              display_count=1)
    assert devices[0]["last_heartbeat"].endswith("Z")
    result = _rufe(client, access, TOOLS[1], device_id=device_id)
    assert result == {"device_id": device_id, "displays": [dict(
        id=display_id, name="Monitor", mode="default", display_power="on", valid_until=None,
        power="off", page_state="blank", backend="logical_blank", verified=False, override_active=False)]}
    serialized = json.dumps([devices, result])
    for secret in (SECRET, "device_token", "pairing_code", "token_hash", "token_enc", "url", "rotation",
                   "sensitive-host", "192.0.2.1"):
        assert secret not in serialized
    assert _rufe(client, access, TOOLS[1], device_id=foreign_id) == _rufe(client, access, TOOLS[1], device_id=-1)
    assert "__fehler__" in _rufe(client, access, TOOLS[1], device_id=foreign_id)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        d = db.get(InfoscreenDisplay, display_id)
        assert (d.revision, d.resolution_hash) == (7, "unchanged")
        assert db.query(InfoscreenDeviceEvent).count() == before_events
        assert db.get(InfoscreenDevice, device_id).config_version == 1


@pytest.mark.parametrize("status,recent,presence,expected", [
    ("active", False, False, "offline"), ("active", False, True, "online"),
    ("revoked", True, True, "offline"), ("pending", True, False, "offline")])
def test_online_wie_admin_ui(client, status, recent, presence, expected):
    seed = _seed(f"isa-online-{status}-{recent}-{presence}", {"u": "org_admin"})
    device_id, _ = _device(seed["org_id"], status=status, recent=recent)
    if presence:
        with SessionLocal() as db:
            set_tenant_context(db, seed["org_id"])
            db.get(InfoscreenDevice, device_id).agent_presence_expires_at = (
                datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=75))
            db.commit()
    assert _rufe(client, _token(client, seed, "u"), TOOLS[0])["geraete"][0]["status"] == expected


def test_override_wirksam_und_gesperrt(client):
    seed = _seed("isa-override", {"u": "org_admin"})
    device_id, display_id = _device(seed["org_id"])
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        db.add(WeatherDashboardToken(org_id=seed["org_id"], label="Wetter", token_hash=hash_api_key(SECRET)))
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add(InfoscreenOverride(org_id=seed["org_id"], display_id=display_id, kind="manual", url_kind="weather",
                                  url_ref="/wetter/infoscreen/" + SECRET, von=now - timedelta(minutes=1),
                                  bis=now + timedelta(hours=1), reason=SECRET))
        db.commit()
    access = _token(client, seed, "u")
    result = _rufe(client, access, TOOLS[1], device_id=device_id)
    assert result["displays"][0]["mode"] == "manual"
    assert result["displays"][0]["override_active"] is True
    assert result["displays"][0]["valid_until"]
    assert SECRET not in json.dumps(result)
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        db.get(InfoscreenDevice, device_id).status = "revoked"
        db.commit()
    result = _rufe(client, access, TOOLS[1], device_id=device_id)
    assert result["displays"][0]["mode"] == "locked"
    assert result["displays"][0]["override_active"] is False


@pytest.mark.parametrize("change", ["module", "role"])
def test_live_entzug(client, change):
    seed = _seed("isa-live-" + change, {"u": "org_admin"})
    access = _token(client, seed, "u")
    assert all(t in _mcp(client, access, "tools/list", {}, 1).text for t in TOOLS)
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        if change == "module":
            db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().mcp_modul_aktiv = False
        else:
            for row in db.query(UserRole).filter_by(user_id=seed["users"]["u"]).all():
                db.delete(row)
        db.commit()
    listed = _mcp(client, access, "tools/list", {}, 1).text
    for tool in TOOLS:
        assert tool not in listed
        assert "__fehler__" in _rufe(client, access, tool, **({"device_id": -1} if tool.endswith("status") else {}))
