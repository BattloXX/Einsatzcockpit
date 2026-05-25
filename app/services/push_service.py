"""Web Push notifications via VAPID."""
import json
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import PushSubscription

log = logging.getLogger(__name__)


def send_push(subscription: PushSubscription, title: str, body: str, url: str | None = None) -> bool:
    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        return False
    try:
        from pywebpush import webpush
        data = json.dumps({"title": title, "body": body, "url": url or "/"})
        webpush(
            subscription_info={"endpoint": subscription.endpoint,
                                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=data,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIM_EMAIL}"},
        )
        return True
    except Exception as exc:
        log.warning("Push failed for sub %s: %s", subscription.id, exc)
        return False


def notify_all(db: Session, title: str, body: str, url: str | None = None) -> int:
    subs = db.query(PushSubscription).all()
    count = sum(1 for s in subs if send_push(s, title, body, url))
    return count


def notify_user(db: Session, user_id: int, title: str, body: str, url: str | None = None) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    count = sum(1 for s in subs if send_push(s, title, body, url))
    return count
