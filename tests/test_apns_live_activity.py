"""APNs-Vertrag, Empfaengerauswahl und Alarm-Hooks ohne Netzwerk."""
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.models.incident import IncidentAlarmJob
from app.models.live_activity import LiveActivityToken
from app.models.user import FcmToken, User
from app.services import alarm_outbox, incident_notify, push_service
from app.services import apns_live_activity as apns
from app.services.einsatz_live_service import build_incident_live_payload
from app.services.incident_live_notify import _dispatch_live_push
from tests.test_incident_live_notify import live_db as _shared_live_db

live_db = _shared_live_db


@pytest.fixture
def apns_transport(monkeypatch, tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "apns.p8"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    for name, value in {"APNS_KEY_PATH": str(path), "APNS_KEY_ID": "KEY123", "APNS_TEAM_ID": "TEAM123",
                        "APNS_BUNDLE_ID": "cloud.einsatzleiter.app", "APNS_SANDBOX": False}.items():
        monkeypatch.setattr(apns.settings, name, value)
    monkeypatch.setattr(apns, "_jwt_cache", None)
    requests = []
    result = {"status": 200, "reason": ""}

    def respond(request):
        requests.append(request)
        if result.get("exception"):
            raise httpx.ConnectError(f"secret {request.url}", request=request)
        return httpx.Response(result["status"], json={"reason": result["reason"]})

    client_type = httpx.Client

    def client(**kwargs):
        assert kwargs == {"http2": True, "timeout": 10.0}
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(apns.httpx, "Client", client)
    return requests, result, key


def _tokens(db, incident):
    user = User(username="apns_ios", password_hash="test", display_name="iOS", org_id=1)
    db.add(user)
    db.flush()
    db.add(FcmToken(user_id=user.id, token="fcm-ios", platform="ios"))
    start = LiveActivityToken(user_id=user.id, kind="start", token="abcdef123456")
    activity = LiveActivityToken(user_id=user.id, kind="activity", token="123456abcdef", incident_id=incident.id)
    db.add_all([start, activity])
    db.commit()
    return user, start, activity


def test_payloads_headers_jwt_cache(live_db, apns_transport, monkeypatch):
    db, incident = live_db
    _, start, activity = _tokens(db, incident)
    payload = build_incident_live_payload(db, incident)
    requests, _, key = apns_transport
    now = int(time.time())
    monkeypatch.setattr(apns.time, "time", lambda: now)
    apns.send_start(db, start, incident, payload)
    apns.send_update(db, activity, payload)
    apns.send_end(db, activity, payload)
    aps = [json.loads(r.content)["aps"] for r in requests]
    assert set(aps[0]) == {"timestamp", "event", "content-state", "attributes-type", "attributes", "alert"}
    assert set(aps[1]) == {"timestamp", "event", "content-state"}
    assert set(aps[2]) == {"timestamp", "event", "content-state", "dismissal-date"}
    assert [p["event"] for p in aps] == ["start", "update", "end"]
    assert aps[0]["attributes-type"] == "EinsatzActivityAttributes"
    assert aps[0]["attributes"] == {"incident_id": incident.id, "url": f"/einsatz/{incident.id}"}
    assert set(aps[0]["alert"]) == {"title", "body"}
    for index, content in enumerate(aps):
        state = content["content-state"]
        assert state == {
            "alarm_code": "B2", "address": payload["address"], "phase_index": payload["phase_index"],
            "phase_count": payload["phase_count"], "phase_label": payload["phase_label"],
            "started_at": datetime(2026, 8, 5, 8, tzinfo=UTC).timestamp(), "unit_count": 1,
            "is_exercise": False, "ended": index == 2,
        }
        assert isinstance(state["started_at"], (int, float))
        assert type(content["timestamp"]) is int
        assert now <= content["timestamp"] <= now + 5
    assert aps[2]["dismissal-date"] == aps[2]["timestamp"] + 900
    for req in requests:
        assert req.url.host == "api.push.apple.com"
        assert req.headers["apns-push-type"] == "liveactivity"
        assert req.headers["apns-topic"] == "cloud.einsatzleiter.app.push-type.liveactivity"
        assert req.headers["apns-priority"] == "10"
        assert now + 3600 <= int(req.headers["apns-expiration"]) <= now + 3605
    assert requests[0].url.path == f"/3/device/{start.token}"
    assert requests[1].url.path == f"/3/device/{activity.token}"
    encoded = requests[0].headers["authorization"].removeprefix("bearer ")
    assert jwt.get_unverified_header(encoded) == {"alg": "ES256", "kid": "KEY123", "typ": "JWT"}
    claims = jwt.decode(encoded, key.public_key(), algorithms=["ES256"])
    assert claims == {"iss": "TEAM123", "iat": now}
    assert requests[1].headers["authorization"] == requests[0].headers["authorization"]
    monkeypatch.setattr(apns.time, "time", lambda: now + 2401)
    assert apns._authorization() != encoded
    monkeypatch.setattr(apns.settings, "APNS_SANDBOX", True)
    apns.send_update(db, activity, payload)
    assert requests[-1].url.host == "api.sandbox.push.apple.com"


def test_start_recipients(live_db, apns_transport):
    db, incident = live_db
    user, start, activity = _tokens(db, incident)
    # Dasselbe Geraet hat schon eine laufende Activity; ein weiteres darf starten.
    second = LiveActivityToken(user_id=user.id, kind="start", token="aaaaaa", device_token_id=20)
    android = User(username="apns_android", password_hash="test", display_name="Android", org_id=1)
    foreign = User(username="apns_foreign", password_hash="test", display_name="Foreign", org_id=2)
    db.add_all([second, android, foreign])
    db.flush()
    db.add_all([
        LiveActivityToken(user_id=android.id, kind="start", token="bbbbbb"),
        LiveActivityToken(user_id=foreign.id, kind="start", token="cccccc"),
        FcmToken(user_id=android.id, token="fcm-android", platform="android"),
        FcmToken(user_id=foreign.id, token="fcm-foreign", platform="ios"),
    ])
    db.commit()
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "created")
    assert [r.url.path for r in apns_transport[0]] == ["/3/device/aaaaaa"]
    db.delete(activity)
    db.commit()
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "created")
    assert f"/3/device/{start.token}" in [r.url.path for r in apns_transport[0]]


@pytest.mark.parametrize("status,reason,removed", [
    (410, "Unregistered", True), (400, "BadDeviceToken", True), (400, "DeviceTokenNotForTopic", True),
    (400, "Unregistered", True), (400, "BadPayload", False),
    (429, "TooManyRequests", False), (500, "InternalServerError", False),
])
def test_apns_errors(live_db, apns_transport, status, reason, removed):
    db, incident = live_db
    _, _, activity = _tokens(db, incident)
    row_id = activity.id
    apns_transport[1].update(status=status, reason=reason)
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "unit_status")
    db.commit()
    row = db.get(LiveActivityToken, row_id)
    assert (row is None) == removed
    if row:
        assert row.last_error.startswith(f"HTTP {status}")


def test_closed_cleanup_and_transport_failure(live_db, apns_transport, caplog):
    db, incident = live_db
    _, _, activity = _tokens(db, incident)
    secret = activity.token
    apns_transport[1]["exception"] = True
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "closed")
    db.commit()
    assert db.query(LiveActivityToken).filter_by(kind="activity").count() == 0
    assert secret not in caplog.text
    assert "ConnectError" in caplog.text


def test_closed_deletes_and_stale_cleanup(live_db, apns_transport):
    db, incident = live_db
    user, _, _ = _tokens(db, incident)
    old = LiveActivityToken(user_id=user.id, kind="activity", token="deadbeef", incident_id=incident.id,
                            updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=14))
    db.add(old)
    db.commit()
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "closed")
    db.commit()
    assert len(apns_transport[0]) == 1
    assert json.loads(apns_transport[0][0].content)["aps"]["event"] == "end"
    assert db.query(LiveActivityToken).filter_by(kind="activity").count() == 0


def test_unconfigured(live_db, apns_transport, monkeypatch, tmp_path):
    db, incident = live_db
    _tokens(db, incident)
    for path in ("", str(tmp_path / "missing"), str(tmp_path)):
        monkeypatch.setattr(apns.settings, "APNS_KEY_PATH", path)
        assert not apns.is_configured()
        apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "created")
    assert not apns_transport[0]


def test_hook_throttle_close_and_exercise(live_db, monkeypatch):
    db, incident = live_db
    calls = []
    monkeypatch.setattr(apns, "dispatch_incident", lambda *args: calls.append(args[-1]))
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: None)
    _dispatch_live_push(incident.id, 1, "unit_status")
    _dispatch_live_push(incident.id, 1, "unit_status")
    _dispatch_live_push(incident.id, 1, "closed")
    assert calls == ["unit_status", "closed"]
    incident.is_exercise = True
    db.commit()
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: False)
    _dispatch_live_push(incident.id, 1, "closed")
    assert calls == ["unit_status", "closed"]


@pytest.mark.asyncio
async def test_outbox_start_once_across_paths(live_db, monkeypatch):
    db, incident = live_db
    calls = []
    monkeypatch.setattr(apns, "dispatch_incident", lambda *args: calls.append(args[-1]))
    monkeypatch.setattr(incident_notify, "_send_incident_push", AsyncMock())
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: None)
    job = IncidentAlarmJob(incident_id=incident.id, org_id=1, channel="push", variant="", context={})
    db.add(job)
    db.commit()
    await alarm_outbox._send_push(db, incident, job)
    _dispatch_live_push(incident.id, 1, "created")
    # Auch nach Ablauf der 90 Sekunden bleibt der Erstalarm beansprucht.
    db.refresh(incident)
    incident.live_push_at -= timedelta(seconds=91)
    db.commit()
    await alarm_outbox._send_push(db, incident, job)
    _dispatch_live_push(incident.id, 1, "created")
    assert calls == ["created"]


@pytest.mark.asyncio
async def test_live_start_before_outbox_and_nachalarm(live_db, monkeypatch):
    db, incident = live_db
    calls = []
    monkeypatch.setattr(apns, "dispatch_incident", lambda *args: calls.append(args[-1]))
    monkeypatch.setattr(incident_notify, "_send_incident_push", AsyncMock())
    monkeypatch.setattr(push_service, "notify_org_web", lambda *a, **kw: None)
    _dispatch_live_push(incident.id, 1, "created")
    for variant in ("", "nachalarm"):
        job = IncidentAlarmJob(incident_id=incident.id, org_id=1, channel="push", variant=variant, context={})
        db.add(job)
        db.commit()
        await alarm_outbox._send_push(db, incident, job)
    assert calls == ["created"]


@pytest.mark.asyncio
async def test_outbox_exercise_block(live_db, monkeypatch):
    db, incident = live_db
    incident.is_exercise = True
    db.commit()
    calls = []
    monkeypatch.setattr(apns, "dispatch_incident", lambda *args: calls.append(args))
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: False)
    monkeypatch.setattr(incident_notify, "_send_incident_wake_only", AsyncMock())
    job = IncidentAlarmJob(incident_id=incident.id, org_id=1, channel="push", variant="", context={})
    assert (await alarm_outbox._send_push(db, incident, job))[0] == "suppressed"
    assert calls == []


def test_recipient_failure_does_not_block_next(live_db, apns_transport, monkeypatch):
    db, incident = live_db
    user, _, activity = _tokens(db, incident)
    second = LiveActivityToken(user_id=user.id, kind="activity", token="ff001122", incident_id=incident.id)
    db.add(second)
    db.commit()
    original = apns.send_update

    def send(db, row, payload):
        if row.id == activity.id:
            raise ValueError("sensitive transport error")
        return original(db, row, payload)

    monkeypatch.setattr(apns, "send_update", send)
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "unit_status")
    db.commit()
    assert activity.last_error == "ValueError"
    assert [r.url.path for r in apns_transport[0]] == ["/3/device/ff001122"]


def test_closed_invalid_token(live_db, apns_transport):
    db, incident = live_db
    _tokens(db, incident)
    apns_transport[1].update(status=410, reason="Unregistered")
    apns.dispatch_incident(db, incident, 1, build_incident_live_payload(db, incident), "closed")
    db.commit()
    assert db.query(LiveActivityToken).filter_by(kind="activity").count() == 0
