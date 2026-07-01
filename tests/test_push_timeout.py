"""Regressionstest PR4 (STAB-5): webpush()-Aufrufe muessen einen Timeout setzen,
sonst kann ein toter Push-Endpoint die sequentielle notify_all/notify_org-Schleife
unbegrenzt blockieren."""
from unittest.mock import MagicMock, patch

from app.config import settings
from app.models.user import PushSubscription
from app.services.push_service import send_push


def test_send_push_passes_timeout(monkeypatch):
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr(settings, "VAPID_CLAIM_EMAIL", "test@example.org")

    sub = PushSubscription(endpoint="https://push.example.org/x", p256dh="p", auth="a")

    mock_webpush = MagicMock(return_value=None)
    with patch("pywebpush.webpush", mock_webpush):
        ok = send_push(sub, "Titel", "Text", db=None)

    assert ok is True
    assert mock_webpush.called
    _, kwargs = mock_webpush.call_args
    assert kwargs.get("timeout") is not None, "webpush() ohne Timeout aufgerufen (STAB-5-Regression)"
