"""Web Push notifications via VAPID.

VAPID-Schlüssel und der enable_push-Schalter werden aus den System-Einstellungen
(Datenbank) geladen. Env-Variablen dienen als Fallback. Der enable_push-Schalter
kann über Admin > System-Einstellungen umgeschaltet werden, ohne einen Neustart.
"""
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import PushSubscription

log = logging.getLogger(__name__)


def _push_cfg(db: Session | None) -> dict[str, Any]:
    """Lädt Push-Konfiguration – DB-Werte haben Vorrang vor Umgebungsvariablen."""
    cfg = {
        "enabled": True,
        "private_key": settings.VAPID_PRIVATE_KEY,
        "public_key": settings.VAPID_PUBLIC_KEY,
        "claim_email": settings.VAPID_CLAIM_EMAIL,
    }
    if db is not None:
        try:
            from app.models.master import SystemSettings

            def _get(key: str) -> str | None:
                row = db.query(SystemSettings).filter_by(key=key).first()
                return row.value if row and row.value else None

            if (v := _get("enable_push")) is not None:
                cfg["enabled"] = v.lower() == "true"
            if (v := _get("vapid_private_key")) is not None:
                cfg["private_key"] = v
            if (v := _get("vapid_public_key")) is not None:
                cfg["public_key"] = v
            if (v := _get("vapid_email")) is not None:
                email = v.removeprefix("mailto:")
                cfg["claim_email"] = email
        except Exception:
            log.exception("Fehler beim Laden der Push-Einstellungen aus der Datenbank")
    return cfg


def _log_push(db: Session, title: str, body: str, url: str | None,
              source: str, target_user_id: int | None,
              sent_count: int, total_count: int) -> None:
    try:
        from app.models.user import PushLog
        db.add(PushLog(
            title=title, body=body, url=url, source=source,
            target_user_id=target_user_id,
            sent_count=sent_count, total_count=total_count,
        ))
    except Exception:
        log.exception("Push-Log Eintrag fehlgeschlagen")


def send_push(subscription: PushSubscription, title: str, body: str,
              url: str | None = None, db: Session | None = None) -> bool:
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return False
    if not cfg["private_key"] or not cfg["public_key"]:
        return False
    try:
        from pywebpush import webpush
        data = json.dumps({"title": title, "body": body, "url": url or "/"})
        webpush(
            subscription_info={"endpoint": subscription.endpoint,
                                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=data,
            vapid_private_key=cfg["private_key"],
            vapid_claims={"sub": f"mailto:{cfg['claim_email']}"},
        )
        return True
    except Exception as exc:
        log.warning("Push fehlgeschlagen für Subscription %s: %s", subscription.id, exc)
        return False


def notify_all(db: Session, title: str, body: str, url: str | None = None,
               source: str = "system") -> int:
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return 0
    subs = db.query(PushSubscription).all()
    count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
    _log_push(db, title, body, url, source, None, count, len(subs))
    return count


def notify_user(db: Session, user_id: int, title: str, body: str,
                url: str | None = None, source: str = "system") -> int:
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return 0
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
    _log_push(db, title, body, url, source, user_id, count, len(subs))
    return count


def notify_vehicle(db: Session, vehicle_master_id: int, title: str, body: str,
                   url: str | None = None) -> int:
    """Push an alle Geräte, die mit diesem VehicleMaster verknüpft sind."""
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return 0
    from app.models.user import DeviceToken
    device_tokens = (
        db.query(DeviceToken)
        .filter(
            DeviceToken.vehicle_master_id == vehicle_master_id,
            DeviceToken.revoked_at.is_(None),
        )
        .all()
    )
    if not device_tokens:
        return 0
    user_ids = {dt.user_id for dt in device_tokens}
    subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
    count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
    if subs:
        _log_push(db, title, body, url, "vehicle_assigned", None, count, len(subs))
    return count
