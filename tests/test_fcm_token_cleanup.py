"""Inaktivitaet richtet sich nach Registrierung oder Empfang, nicht nach Versand."""
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
from app.services import push_service
from app.services.background_runtime import BackgroundRuntime


def test_cleanup_preserves_active_tokens_and_delivery_history(caplog):
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=1)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = User(username=f"cleanup-{uuid4().hex}", display_name="Cleanup", org_id=1)
        db.add(user)
        db.flush()
        tokens = [
            FcmToken(user_id=user.id, token=uuid4().hex, created_at=old),
            FcmToken(user_id=user.id, token=uuid4().hex, created_at=old, last_used_at=old, last_ack_at=old),
            FcmToken(user_id=user.id, token=uuid4().hex, created_at=recent),
            FcmToken(user_id=user.id, token=uuid4().hex, created_at=old, last_used_at=recent),
            FcmToken(user_id=user.id, token=uuid4().hex, created_at=old, last_used_at=old, last_ack_at=recent),
        ]
        push = PushLog(title="Test", body="Test", source="cleanup", org_id=1)
        db.add_all([*tokens, push])
        db.flush()
        ids = [token.id for token in tokens]
        delivery = FcmDeliveryLog(push_log_id=push.id, fcm_token_id=ids[0], user_id=user.id, success=True)
        db.add(delivery)
        db.commit()
        with caplog.at_level(logging.INFO, logger=push_service.__name__):
            assert push_service.cleanup_inactive_fcm_tokens(db) >= 2
        db.commit()
        assert [db.get(FcmToken, token_id) is not None for token_id in ids] == [False, False, True, True, True]
        assert db.get(FcmDeliveryLog, delivery.id).fcm_token_id is None
        assert "inaktive Tokens entfernt" in caplog.text
        assert push_service.cleanup_inactive_fcm_tokens(db) == 0


def test_wake_skips_inactive_tokens_but_accepts_recent_ack(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    old = now - timedelta(days=31)
    sent = []
    monkeypatch.setattr(push_service, "_get_fcm_app", lambda cfg: object())

    def send(token, *args, delivery, **kwargs):
        sent.append(token.id)
        delivery.success = True
        return True, None

    monkeypatch.setattr(push_service, "send_fcm", send)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = User(username=f"wake-age-{uuid4().hex}", display_name="Alter", org_id=1)
        db.add(user)
        db.flush()
        stale = FcmToken(user_id=user.id, token=uuid4().hex, created_at=old, last_used_at=old)
        active = FcmToken(user_id=user.id, token=uuid4().hex, created_at=old, last_ack_at=now)
        db.add_all([stale, active])
        db.commit()
        assert push_service._notify_fcm_users(db, {user.id}, "Test", "Test", None, wake_only=True) == 1
        assert sent == [active.id]
        sent.clear()
        # Echte Alarme bleiben auch fuer alte Tokens erlaubt.
        assert push_service._notify_fcm_users(db, {user.id}, "Test", "Test", None) == 2
        assert set(sent) == {stale.id, active.id}


def test_leader_housekeeping_runs_token_cleanup(monkeypatch):
    called = []
    monkeypatch.setattr(push_service, "cleanup_inactive_fcm_tokens", lambda db: called.append(db))
    BackgroundRuntime.cleanup()
    assert len(called) == 1
