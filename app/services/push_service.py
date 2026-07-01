"""Web Push notifications via VAPID + native Android Push via FCM.

VAPID-Schlüssel und der enable_push-Schalter werden aus den System-Einstellungen
(Datenbank) geladen. Env-Variablen dienen als Fallback. Der enable_push-Schalter
kann über Admin > System-Einstellungen umgeschaltet werden, ohne einen Neustart.

FCM (Firebase Cloud Messaging) ist ein zweiter Sendepfad für native Android-Apps.
Er wird nur ausgelöst, wenn FCM_ENABLED=true und ein Service-Account-Credentials-
Pfad konfiguriert ist. PWA-Nutzer erhalten weiterhin Web-Push über VAPID.
"""
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import FcmToken, PushSubscription

log = logging.getLogger(__name__)

# Gecachter FCM-App-Zustand – wird beim ersten Aufruf initialisiert
_fcm_app: Any = None


def _get_fcm_app(cfg: dict | None = None):
    """Gibt eine initialisierte firebase_admin.App zurück oder None wenn FCM nicht konfiguriert."""
    global _fcm_app
    if _fcm_app is not None:
        return _fcm_app
    fcm_enabled = cfg.get("fcm_enabled", settings.FCM_ENABLED) if cfg else settings.FCM_ENABLED
    fcm_project_id = cfg.get("fcm_project_id", settings.FCM_PROJECT_ID) if cfg else settings.FCM_PROJECT_ID
    fcm_creds = cfg.get("fcm_credentials_path", settings.FCM_CREDENTIALS_PATH) if cfg else settings.FCM_CREDENTIALS_PATH
    if not fcm_enabled or not fcm_project_id or not fcm_creds:
        return None
    try:
        import firebase_admin  # type: ignore
        from firebase_admin import credentials  # type: ignore
        if not firebase_admin._apps:
            cred = credentials.Certificate(fcm_creds)
            _fcm_app = firebase_admin.initialize_app(cred, {"projectId": fcm_project_id})
        else:
            _fcm_app = firebase_admin.get_app()
        return _fcm_app
    except Exception:
        log.exception("FCM-Initialisierung fehlgeschlagen")
        return None


def send_fcm(fcm_token_row: FcmToken, title: str, body: str, url: str | None = None) -> bool:
    """Sendet eine FCM-Nachricht an ein einzelnes Gerät."""
    app = _get_fcm_app()
    if app is None:
        return False
    try:
        from firebase_admin import messaging  # type: ignore
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={"url": url or "/", "title": title, "body": body},
            android=messaging.AndroidConfig(priority="high"),
            token=fcm_token_row.token,
        )
        messaging.send(message)
        return True
    except Exception as exc:
        log.warning("FCM fehlgeschlagen für Token %s: %s", fcm_token_row.id, exc)
        return False


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
                # Whitespace entfernen – Copy-Paste aus Key-Generatoren fügt oft \n ein
                cfg["private_key"] = v.strip()
            if (v := _get("vapid_public_key")) is not None:
                cfg["public_key"] = v.strip()
            if (v := _get("vapid_email")) is not None:
                email = v.strip().removeprefix("mailto:")
                cfg["claim_email"] = email
            # FCM – DB hat Vorrang vor .env
            cfg["fcm_enabled"] = (
                _get("fcm_enabled") or ("true" if settings.FCM_ENABLED else "false")
            ).lower() == "true"
            cfg["fcm_project_id"] = _get("fcm_project_id") or settings.FCM_PROJECT_ID
            cfg["fcm_credentials_path"] = _get("fcm_credentials_path") or settings.FCM_CREDENTIALS_PATH
        except Exception:
            log.exception("Fehler beim Laden der Push-Einstellungen aus der Datenbank")
    else:
        cfg["fcm_enabled"] = settings.FCM_ENABLED
        cfg["fcm_project_id"] = settings.FCM_PROJECT_ID
        cfg["fcm_credentials_path"] = settings.FCM_CREDENTIALS_PATH
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
            timeout=10,
        )
        return True
    except Exception as exc:
        # 410 Gone or 404 = subscription no longer valid → remove from DB
        status = None
        try:
            from pywebpush import WebPushException as _WPE  # noqa: F811
            if isinstance(exc, _WPE) and exc.response is not None:
                status = exc.response.status_code
        except Exception:
            pass
        if status in (403, 404, 410):
            log.info("Push-Subscription %s ist abgelaufen/ungültig (HTTP %s) – wird gelöscht", subscription.id, status)
            if db is not None:
                try:
                    db.delete(subscription)
                    db.commit()
                except Exception:
                    db.rollback()
        else:
            ep = subscription.endpoint[:60] if subscription.endpoint else "?"
            log.exception("Push fehlgeschlagen für Subscription %s (endpoint: %s…): %r",
                          subscription.id, ep, exc)
        return False


def _notify_fcm_users(db: Session, user_ids: set[int], title: str, body: str,
                      url: str | None, cfg: dict | None = None) -> int:
    """Sendet FCM an alle registrierten Tokens der angegebenen User-IDs."""
    if _get_fcm_app(cfg) is None:
        return 0
    tokens = db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).all() if user_ids else []
    return sum(1 for t in tokens if send_fcm(t, title, body, url))


def notify_all(db: Session, title: str, body: str, url: str | None = None,
               source: str = "system") -> int:
    cfg = _push_cfg(db)
    # Web-Push (VAPID)
    if cfg["enabled"]:
        subs = db.query(PushSubscription).all()
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        _log_push(db, title, body, url, source, None, wp_count, len(subs))
    else:
        wp_count = 0
    # FCM
    all_user_ids = {s.user_id for s in db.query(PushSubscription.user_id).distinct()}
    fcm_extra = _notify_fcm_users(db, all_user_ids, title, body, url, cfg)
    return wp_count + fcm_extra


def notify_org(db: Session, org_id: int, title: str, body: str,
               url: str | None = None, source: str = "system") -> int:
    """Push nur an User der angegebenen Org (statt an alle)."""
    from app.models.user import User as _User
    cfg = _push_cfg(db)
    org_user_ids_subq = db.query(_User.id).filter(_User.org_id == org_id)
    if cfg["enabled"]:
        subs = (
            db.query(PushSubscription)
            .filter(PushSubscription.user_id.in_(org_user_ids_subq))
            .all()
        )
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        _log_push(db, title, body, url, source, None, wp_count, len(subs))
    else:
        wp_count = 0
    org_user_ids = {r[0] for r in db.query(_User.id).filter(_User.org_id == org_id).all()}
    fcm_extra = _notify_fcm_users(db, org_user_ids, title, body, url, cfg)
    return wp_count + fcm_extra


def notify_user(db: Session, user_id: int, title: str, body: str,
                url: str | None = None, source: str = "system") -> int:
    cfg = _push_cfg(db)
    # Web-Push
    if cfg["enabled"]:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        _log_push(db, title, body, url, source, user_id, wp_count, len(subs))
    else:
        wp_count = 0
    # FCM
    fcm_extra = _notify_fcm_users(db, {user_id}, title, body, url, cfg)
    return wp_count + fcm_extra


def notify_vehicle(db: Session, vehicle_master_id: int, title: str, body: str,
                   url: str | None = None) -> int:
    """Push an alle Geräte, die mit diesem VehicleMaster verknüpft sind."""
    cfg = _push_cfg(db)
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
    # Web-Push
    if cfg["enabled"]:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        if subs:
            _log_push(db, title, body, url, "vehicle_assigned", None, wp_count, len(subs))
    else:
        wp_count = 0
    # FCM
    fcm_extra = _notify_fcm_users(db, user_ids, title, body, url, cfg)
    return wp_count + fcm_extra
