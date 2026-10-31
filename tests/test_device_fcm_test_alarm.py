from datetime import timedelta

import pytest

from app.db import SessionLocal
from app.models.user import FcmToken, PushLog
from app.services import push_service
from tests.test_device_api_fcm_token import _authenticate, _make_device_user

ENDPOINT = "/api/v1/device/fcm-test-alarm"


def _register_token(user_id, capabilities="native_alarm"):
    with SessionLocal() as db:
        token = f"test-alarm-token-{user_id}"
        db.add(FcmToken(user_id=user_id, token=token, capabilities=capabilities))
        db.commit()
    return token


def test_alarm_requires_auth(client, setup_db):
    assert client.post(ENDPOINT, json={"token": "test-alarm-token"}).status_code == 401


def test_alarm_rejects_foreign_and_missing_token(client, setup_db):
    owner_id, _ = _make_device_user("alarm_owner")
    user_id, device_id = _make_device_user("alarm_other")
    foreign_token = _register_token(owner_id)
    _authenticate(client, user_id, device_id)
    for token in (foreign_token, "missing"):
        response = client.post(ENDPOINT, json={"token": token})
        assert response.status_code == 404
        assert response.json() == {"detail": "Token nicht gefunden"}


@pytest.mark.parametrize("bearer", [False, True])
@pytest.mark.parametrize("capabilities, result", [("native_alarm", (True, None)), (None, (False, "quota_exceeded"))])
def test_alarm_response_and_log(client, setup_db, monkeypatch, bearer, capabilities, result):
    user_id, device_id = _make_device_user(f"alarm_ok_{bearer}_{capabilities}")
    token = _register_token(user_id, capabilities)
    headers = {"Authorization": f"Bearer raw-alarm_ok_{bearer}_{capabilities}"} if bearer else {}
    if not bearer:
        _authenticate(client, user_id, device_id)
    calls = []

    def fake_send(row, title, body, **kwargs):
        assert row.user_id == user_id
        assert title == "Testalarm"
        assert body == "Probealarm – so klingt ein Einsatzalarm"
        assert kwargs["url"] == "/"
        assert kwargs["channel_id"] == "einsatz_alarm"
        assert kwargs["extra_data"] == {"test": "1", "incident_id": "0", "is_exercise": "0"}
        assert kwargs["cfg"] is not None
        assert kwargs["db"].get(PushLog, kwargs["push_log_id"]).source == "fcm_alarm_test"
        calls.append(kwargs["push_log_id"])
        return result

    monkeypatch.setattr(push_service, "send_fcm", fake_send)
    response = client.post(ENDPOINT, json={"token": token}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"ok": result[0], "native_alarm": capabilities is not None, "error": result[1]}
    with SessionLocal() as db:
        entry = db.get(PushLog, calls[0])
        assert entry.source == "fcm_alarm_test"
        assert entry.sent_count == int(result[0])
    response = client.post(ENDPOINT, json={"token": token}, headers=headers)
    assert response.status_code == 429
    assert len(calls) == 1
    with SessionLocal() as db:
        entry = db.get(PushLog, calls[0])
        entry.sent_at = entry.sent_at - timedelta(seconds=21)
        db.commit()
    assert client.post(ENDPOINT, json={"token": token}, headers=headers).status_code == 200
    assert len(calls) == 2
