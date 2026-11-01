"""iOS erhaelt genau eine APNs-Nachricht statt Android-Wake/Display."""
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
from app.services import push_service
from tests.test_device_api_fcm_token import _authenticate, _make_device_user


@pytest.fixture
def ios_messaging(monkeypatch):
    firebase_admin = ModuleType("firebase_admin")
    messaging = ModuleType("firebase_admin.messaging")
    for name in ("Message", "Notification", "APNSConfig", "APNSPayload", "Aps", "CriticalSound"):
        setattr(messaging, name, type(name, (SimpleNamespace,), {}))
    for name in ("UnregisteredError", "SenderIdMismatchError", "QuotaExceededError", "ThirdPartyAuthError"):
        setattr(messaging, name, type(name, (Exception,), {}))
    sent = []
    messaging.send = sent.append
    firebase_admin.messaging = messaging
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", messaging)
    monkeypatch.setattr(push_service, "_get_fcm_app", lambda _cfg=None: object())
    monkeypatch.setattr(push_service.settings, "IOS_CRITICAL_ALERTS_ENABLED", False)
    return messaging, sent


@pytest.mark.parametrize("channel, critical, level, ttl", [
    ("einsatz_alarm", False, "time-sensitive", 600),
    ("einsatz_alarm", True, "critical", 600),
    ("info", False, "active", 3600),
    (None, True, "active", 3600),
])
def test_ios_visible_notification(ios_messaging, monkeypatch, channel, critical, level, ttl):
    messaging, sent = ios_messaging
    monkeypatch.setattr(push_service.settings, "IOS_CRITICAL_ALERTS_ENABLED", critical)
    token = FcmToken(token="ios-token", platform="ios", capabilities="native_alarm")
    delivery = FcmDeliveryLog(id=123, error_code="old", error_detail="old")
    before = int(datetime.now(UTC).timestamp())
    result = push_service.send_fcm(
        token, "Alarm", "Test", url="/einsatz/42", channel_id=channel, delivery=delivery,
        extra_data={"incident_id": "42", "is_exercise": "0"},
    )
    after = int(datetime.now(UTC).timestamp())

    assert result == (True, None)
    assert len(sent) == 1
    message = sent[0]
    assert message.token == "ios-token"
    assert message.notification.title == "Alarm"
    assert message.notification.body == "Test"
    assert not hasattr(message, "android")
    assert message.data == {
        "url": "/einsatz/42", "title": "Alarm", "body": "Test", "channel_id": channel or "",
        "delivery_id": "123", "incident_id": "42", "is_exercise": "0",
    }
    headers = message.apns.headers
    assert headers["apns-priority"] == "10"
    assert headers["apns-push-type"] == "alert"
    assert isinstance(headers["apns-expiration"], str)
    assert before + ttl <= int(headers["apns-expiration"]) <= after + ttl
    aps = message.apns.payload.aps
    assert aps.thread_id == (channel or "ecp")
    assert aps.custom_data == {"interruption-level": level}
    if level == "critical":
        assert isinstance(aps.sound, messaging.CriticalSound)
        assert aps.sound.name == "default"
        assert aps.sound.critical is True
        assert aps.sound.volume == 1.0
    else:
        assert aps.sound == "default"
    assert delivery.success is True
    assert delivery.error_code is None
    assert delivery.error_detail is None


def test_ios_wake_only(ios_messaging, monkeypatch):
    _messaging, sent = ios_messaging
    monkeypatch.setattr(push_service.settings, "IOS_CRITICAL_ALERTS_ENABLED", True)
    token = FcmToken(token="ios-wake", platform="ios", capabilities="native_alarm")
    delivery = FcmDeliveryLog(id=456)
    assert push_service.send_fcm(
        token, "Alarm", "Test", channel_id="einsatz_alarm", wake_only=True, delivery=delivery,
    ) == (True, None)
    assert len(sent) == 1
    message = sent[0]
    assert not hasattr(message, "notification")
    assert not hasattr(message, "android")
    assert message.data["silent"] == "1"
    assert message.data["delivery_id"] == "456"
    assert message.data["url"] == "/"
    assert message.apns.headers["apns-push-type"] == "background"
    assert message.apns.headers["apns-priority"] == "5"
    assert vars(message.apns.payload.aps) == {"content_available": True}
    assert delivery.success is True
    assert delivery.error_code is None
    assert delivery.error_detail is None


@pytest.mark.parametrize("wake_only", [False, True])
@pytest.mark.parametrize("error_name, expected, pruned", [
    ("UnregisteredError", "unregistered_pruned", True),
    ("SenderIdMismatchError", "unregistered_pruned", True),
    ("QuotaExceededError", "quota_exceeded", False),
    ("ThirdPartyAuthError", "sender_id_mismatch", False),
    ("RuntimeError", "unknown", False),
])
def test_ios_failure(ios_messaging, wake_only, error_name, expected, pruned):
    messaging, sent = ios_messaging

    def fail(message):
        sent.append(message)
        raise getattr(messaging, error_name, RuntimeError)("Testfehler")

    messaging.send = fail
    with SessionLocal() as db:
        suffix = f"{error_name}-{wake_only}"
        user = User(username=f"ios-error-{suffix}", display_name="iOS", org_id=1, active=True)
        db.add(user)
        db.flush()
        token = FcmToken(user_id=user.id, token=f"ios-error-token-{suffix}", platform="ios")
        push_log = PushLog(title="Alarm", body="Test", source="einsatz_alarm", org_id=1)
        db.add_all([token, push_log])
        db.commit()
        token_id = token.id
        assert push_service.send_fcm(
            token, "Alarm", "Test", channel_id="einsatz_alarm", db=db,
            push_log_id=push_log.id, wake_only=wake_only,
        ) == (False, expected)
        db.commit()
        assert len(sent) == 1
        delivery = db.query(FcmDeliveryLog).filter_by(push_log_id=push_log.id).one()
        assert delivery.success is False
        assert delivery.error_code == expected
        assert expected in delivery.error_detail
        assert (db.get(FcmToken, token_id) is None) == pruned
        assert delivery.fcm_token_id == (None if pruned else token_id)


@pytest.mark.parametrize("capabilities", [None, "native_alarm"])
def test_ios_test_alarm_endpoint(client, ios_messaging, capabilities):
    _messaging, sent = ios_messaging
    user_id, device_id = _make_device_user(f"ios-test-alarm-{capabilities}")
    with SessionLocal() as db:
        token = f"ios-test-alarm-{capabilities}"
        db.add(FcmToken(user_id=user_id, token=token, platform="ios", capabilities=capabilities))
        db.commit()
    _authenticate(client, user_id, device_id)
    response = client.post("/api/v1/device/fcm-test-alarm", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "native_alarm": False, "error": None}
    assert len(sent) == 1
    assert sent[0].notification.title == "Testalarm"
    assert sent[0].apns.payload.aps.custom_data == {"interruption-level": "time-sensitive"}
    assert sent[0].data["test"] == "1"
    assert "alarm" not in sent[0].data
    with SessionLocal() as db:
        delivery = db.get(FcmDeliveryLog, int(sent[0].data["delivery_id"]))
        assert delivery.success is True
