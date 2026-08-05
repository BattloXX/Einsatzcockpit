import json
from unittest.mock import MagicMock, patch

from app.config import settings
from app.models.user import PushSubscription
from app.services import push_service


def _subscription(endpoint="https://push.example.org/x"):
    return PushSubscription(endpoint=endpoint, p256dh="p", auth="a")


def _enable_web_push(monkeypatch):
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(settings, "VAPID_CLAIM_EMAIL", "test@example.org")


def test_send_push_default_payload_is_byte_identical(monkeypatch):
    _enable_web_push(monkeypatch)
    mock_webpush = MagicMock(return_value=None)

    with patch("pywebpush.webpush", mock_webpush):
        assert push_service.send_push(_subscription(), "T", "B", None, None) is True

    payload = mock_webpush.call_args.kwargs["data"]
    assert payload == '{"title": "T", "body": "B", "url": "/"}'
    assert json.loads(payload) == {"title": "T", "body": "B", "url": "/"}


def test_send_push_merges_live_payload_within_budget(monkeypatch):
    _enable_web_push(monkeypatch)
    mock_webpush = MagicMock(return_value=None)
    extra = {
        "kind": "einsatz_live",
        "tag": "ec-einsatz-1",
        "live": {"incident_id": 1, "alert": False},
    }

    with patch("pywebpush.webpush", mock_webpush):
        assert push_service.send_push(
            _subscription(), "T", "B", "/einsatz/1", None, extra=extra,
        ) is True

    serialized = mock_webpush.call_args.kwargs["data"]
    assert json.loads(serialized) == {
        "title": "T",
        "body": "B",
        "url": "/einsatz/1",
        **extra,
    }
    assert len(serialized.encode("utf-8")) < 3000


def test_notify_org_web_only_sends_web_pushes(monkeypatch):
    subscriptions = [_subscription("https://push.example.org/1"), _subscription("https://push.example.org/2")]
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = subscriptions
    monkeypatch.setattr(push_service, "_push_cfg", lambda _db: {"enabled": True})
    sent = []

    def fake_send_push(subscription, title, body, url, db=None, extra=None):
        sent.append((subscription, title, body, url, db, extra))
        return True

    monkeypatch.setattr(push_service, "send_push", fake_send_push)
    fcm = MagicMock()
    log_push = MagicMock()
    monkeypatch.setattr(push_service, "_notify_fcm_users", fcm)
    monkeypatch.setattr(push_service, "_log_push", log_push)
    extra = {"kind": "einsatz_live", "tag": "ec-einsatz-1"}

    count = push_service.notify_org_web(
        db, 7, "T", "B", "/einsatz/1", extra=extra,
    )

    assert count == 2
    assert sent == [
        (subscriptions[0], "T", "B", "/einsatz/1", db, extra),
        (subscriptions[1], "T", "B", "/einsatz/1", db, extra),
    ]
    fcm.assert_not_called()
    log_push.assert_not_called()
