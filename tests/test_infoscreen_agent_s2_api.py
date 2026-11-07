"""S2: Snapshot-API, persistierte Revision und Agent-Rotation."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.crypto import encrypt_secret
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDisplay, InfoscreenDeviceEvent
from app.models.objekt import AlarmInfoscreenToken, InfoscreenUrl
from app.services.infoscreen_agent_snapshot import state
from tests.conftest import TestingSession as SessionLocal

BASE = "/api/v1/infoscreen-agent"


def setup_display(org_id=1):
    suffix = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        device = InfoscreenDevice(org_id=org_id, name="S2", status="active",
                                  device_token_hash=hash_api_key("device-" + suffix))
        token = AlarmInfoscreenToken(org_id=org_id, name="S2", aktiv=True,
                                     token_hash=hash_api_key(suffix), token_enc=encrypt_secret(suffix))
        db.add_all([device, token])
        db.flush()
        display = InfoscreenDisplay(org_id=org_id, device_id=device.id, name="Halle",
                                    display_key="hall-" + suffix, alarm_token_id=token.id)
        db.add(display)
        db.commit()
        return device.id, display.id, display.display_key, {"Authorization": "Bearer device-" + suffix}, suffix


def test_config_state_etag_und_auth(client):
    _, _, key, headers, raw = setup_display()
    started = datetime.now(UTC)
    for path in ("/config", "/state?display=" + key):
        assert client.get(BASE + path).status_code == 401
        first = client.get(BASE + path, headers=headers)
        assert first.status_code == 200, first.text
        cached = client.get(BASE + path, headers=headers | {"If-None-Match": first.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        for match in ("*", "W/" + first.headers["etag"], '"falsch", W/' + first.headers["etag"]):
            assert client.get(BASE + path, headers=headers | {"If-None-Match": match}).status_code == 304
        assert client.get(BASE + path, headers=headers | {"If-None-Match": '"unbekannt"'}).status_code == 200
        if path == "/config":
            display = first.json()["displays"][0]
            assert display["url"] == "/infoscreen/alarm/" + raw
            assert display["timeline"]
            assert datetime.fromisoformat(display["timeline"][0]["from"]) <= started
            assert datetime.fromisoformat(display["timeline"][-1]["until"]) >= started + timedelta(hours=72)
        else:
            assert first.json()["url"] == "/infoscreen/alarm/" + raw
            assert first.headers["etag"] == '"' + str(first.json()["revision"]) + '"'
    assert client.get(BASE + "/state?display=fehlt", headers=headers).status_code == 404


def test_revision_idempotent_monoton_ohne_serverzeit_und_token_im_event():
    device_id, display_id, _, _, raw = setup_display()
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device, display = db.get(InfoscreenDevice, device_id), db.get(InfoscreenDisplay, display_id)
        now = datetime(2026, 11, 2, 12, tzinfo=UTC)
        first = state(db, device, display, now)
        assert first["revision"] == 1
        second = state(db, device, display, now + timedelta(seconds=20))
        assert second["revision"] == 1
        assert second["server_time"] != first["server_time"]
        display.aktiv = False
        db.commit()
        third = state(db, device, display, now)
        assert third["revision"] == 2 and third["mode"] == "locked"
        assert state(db, device, display, now)["revision"] == 2
        display.aktiv = True
        db.commit()
        assert state(db, device, display, now)["revision"] == 3
        for event in db.query(InfoscreenDeviceEvent).filter(InfoscreenDeviceEvent.device_id == device.id).all():
            assert raw not in str(event.payload_json)


def test_revision_parallele_leser_nur_ein_increment():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    device_id, display_id, _, _, _ = setup_display()
    barrier = Barrier(4)

    def read(_):
        with SessionLocal() as db:
            set_tenant_context(db, 1)
            device, display = db.get(InfoscreenDevice, device_id), db.get(InfoscreenDisplay, display_id)
            barrier.wait(timeout=10)
            return state(db, device, display, datetime(2026, 11, 2, 12, tzinfo=UTC))["revision"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(read, range(4))) == [1, 1, 1, 1]


def test_slot_rotation_nur_agent_token_und_org_gefiltert():
    from app.routers.ui_infoscreen_alarm import _baue_idle_urls

    device_id, display_id, _, _, _ = setup_display()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        display = db.get(InfoscreenDisplay, display_id)
        token = db.get(AlarmInfoscreenToken, display.alarm_token_id)
        url = InfoscreenUrl(org_id=1, label="Slot", url="/slot", dwell_sec=30)
        other = AlarmInfoscreenToken(org_id=1, name="Normal", aktiv=True, token_hash=uuid4().hex)
        db.add_all([url, other])
        db.commit()
        rotation = [{"url_id": url.id, "dwell_sec": 7}, {"builtin": "weather", "dwell_sec": 9}]
        assert _baue_idle_urls(db, token, "/weather", rotation) == [
            {"label": "Slot", "url": "/slot", "dwell_sec": 7},
            {"label": "Wetter", "url": "/weather", "dwell_sec": 9}]
        assert _baue_idle_urls(db, other, "/weather", rotation) == []


def test_config_aenderung_bricht_etag_und_revision_auch_ohne_powerwechsel(client):
    from datetime import time

    from app.models.infoscreen_agent import InfoscreenSchedule, InfoscreenScheduleSlot

    from app.models.master import FireDept

    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="s2-config-" + uuid4().hex, name="S2 Config")
        db.add(org)
        db.commit()
        org_id = org.id
    device_id, display_id, key, headers, raw = setup_display(org_id)
    first = client.get(BASE + "/config", headers=headers)
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        device = db.get(InfoscreenDevice, device_id)
        device.config_version += 1
        schedule = InfoscreenSchedule(org_id=org_id, name="S2", timezone="Europe/Vienna")
        db.add(schedule)
        db.flush()
        db.get(InfoscreenDisplay, display_id).schedule_id = schedule.id
        url = InfoscreenUrl(org_id=org_id, label="Plan", url="/plan", dwell_sec=30)
        db.add(url)
        db.flush()
        url_id = url.id
        slot = InfoscreenScheduleSlot(org_id=org_id, schedule_id=schedule.id, weekdays=127,
                                      start_time=time.min, end_time=time.min, power="on",
                                      rotation_json=[{"url_id": url_id, "dwell_sec": 30}])
        db.add(slot)
        db.commit()
        slot_id = slot.id
    changed = client.get(BASE + "/config", headers=headers | {"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200
    assert changed.json()["config_version"] == first.json()["config_version"] + 1
    before = client.get(BASE + "/state", params={"display": key}, headers=headers)
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        db.get(InfoscreenScheduleSlot, slot_id).rotation_json = [{"url_id": url_id, "dwell_sec": 60}]
        db.commit()
    after = client.get(BASE + "/state", params={"display": key},
                       headers=headers | {"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200
    assert after.json()["revision"] == before.json()["revision"] + 1
    assert after.json()["display_power"] == before.json()["display_power"]
    daten = client.get(f"/infoscreen/alarm/{raw}/daten")
    assert daten.status_code == 200
    assert daten.json()["idle_urls"] == [{"label": "Plan", "url": "/plan", "dwell_sec": 60}]


def test_aktivitaet_juengstes_ende_aktive_und_fremde_org():
    from app.models.incident import Incident
    from app.models.major_incident import MajorIncident
    from app.models.master import FireDept
    from app.services.infoscreen_aktivitaet import lade_aktivitaet

    now = datetime(2026, 11, 2, 12)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="s2-activity-" + uuid4().hex, name="S2 Aktivitaet")
        db.add(org)
        db.flush()
        db.add_all([
            Incident(primary_org_id=org.id, status="closed", closed_at=now - timedelta(minutes=3)),
            MajorIncident(org_id=org.id, name="Beendet", status="closed", ended_at=now - timedelta(minutes=2)),
            Incident(primary_org_id=1, status="closed", closed_at=now),
        ])
        db.flush()
        result = lade_aktivitaet(db, org.id)
        assert not result.gsl and not result.incident
        assert result.letztes_ende == now - timedelta(minutes=2)
        db.add_all([Incident(primary_org_id=org.id, status="active"),
                    MajorIncident(org_id=org.id, name="Aktiv", status="active")])
        db.flush()
        result = lade_aktivitaet(db, org.id)
        assert result.gsl and result.incident
        db.rollback()


def test_sperrseite_wird_vom_resolver_geliefert_und_ist_schwarz(client):
    _, display_id, key, headers, raw = setup_display()
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        db.get(InfoscreenDisplay, display_id).aktiv = False
        db.commit()
    result = client.get(BASE + "/state", params={"display": key}, headers=headers)
    assert result.status_code == 200
    assert result.json()["mode"] == "locked"
    assert result.json()["display_power"] == "on"
    assert raw not in result.text
    page = client.get(result.json()["url"])
    assert page.status_code == 200
    assert '<body></body>' in page.text
    assert raw not in page.text
    css = client.get("/static/infoscreen-agent/locked.css")
    assert css.status_code == 200
    assert "background: #000;" in css.text
