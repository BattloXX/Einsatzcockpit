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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, PushSubscription

log = logging.getLogger(__name__)

# Gecachter FCM-App-Zustand – wird beim ersten Aufruf initialisiert
_fcm_app: Any = None
# firebase_admin ist eine optionale Abhaengigkeit (nicht in pyproject). Fehlt das
# Paket, ist das ein prozess-permanenter Zustand: einmal merken statt bei JEDEM
# Push erneut zu importieren und den vollen Traceback zu loggen (Log-Flut auf
# Prod beobachtet 2026-07-06). Web-Push/VAPID laeuft davon unberuehrt weiter.
_fcm_unavailable: bool = False
_fcm_unconfigured_warned: bool = False


def _get_fcm_app(cfg: dict | None = None):
    """Gibt eine initialisierte firebase_admin.App zurück oder None wenn FCM nicht konfiguriert."""
    global _fcm_app, _fcm_unavailable, _fcm_unconfigured_warned
    if _fcm_app is not None:
        return _fcm_app
    if _fcm_unavailable:
        return None
    fcm_enabled = cfg.get("fcm_enabled", settings.FCM_ENABLED) if cfg else settings.FCM_ENABLED
    fcm_project_id = cfg.get("fcm_project_id", settings.FCM_PROJECT_ID) if cfg else settings.FCM_PROJECT_ID
    fcm_creds = cfg.get("fcm_credentials_path", settings.FCM_CREDENTIALS_PATH) if cfg else settings.FCM_CREDENTIALS_PATH
    if not fcm_enabled or not fcm_project_id or not fcm_creds:
        if not _fcm_unconfigured_warned:
            _fcm_unconfigured_warned = True
            # Einzeln benennen statt nur "irgendwas fehlt" -- fcm_enabled wird von der DB
            # (Admin > System-Einstellungen) ueberschrieben, sobald dort JEMALS gespeichert
            # wurde (auch mit "Inaktiv"), selbst wenn FCM_ENABLED=true in der .env steht.
            # Das ist die haeufigste Ursache dieser Warnung trotz korrekt gesetzter .env.
            missing = []
            if not fcm_enabled:
                missing.append("fcm_enabled=false (Admin > System-Einstellungen hat Vorrang vor .env!)")
            if not fcm_project_id:
                missing.append("fcm_project_id ist leer")
            if not fcm_creds:
                missing.append("fcm_credentials_path ist leer")
            log.warning(
                "FCM ist nicht konfiguriert - native Android-Pushes werden nicht verschickt. "
                "Grund: %s",
                "; ".join(missing),
            )
        return None
    try:
        import firebase_admin  # type: ignore
        from firebase_admin import credentials  # type: ignore
    except ImportError:
        # Paket nicht installiert -> permanent (kein Runtime-pip). Einmal warnen,
        # danach still bleiben, damit die Logs nicht bei jedem Push volllaufen.
        _fcm_unavailable = True
        log.warning(
            "FCM ist aktiviert, aber das Paket 'firebase_admin' ist nicht installiert "
            "-> FCM-Push deaktiviert. Web-Push/VAPID bleibt aktiv. "
            "Zum Aktivieren: 'pip install firebase-admin' im Server-venv."
        )
        return None
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(fcm_creds)
            _fcm_app = firebase_admin.initialize_app(cred, {"projectId": fcm_project_id})
        else:
            _fcm_app = firebase_admin.get_app()
        return _fcm_app
    except Exception:
        log.exception("FCM-Initialisierung fehlgeschlagen")
        return None


def send_fcm(
    fcm_token_row: FcmToken,
    title: str,
    body: str,
    url: str | None = None,
    channel_id: str | None = None,
    cfg: dict | None = None,
    db: Session | None = None,
    push_log_id: int | None = None,
    delivery: FcmDeliveryLog | None = None,
) -> tuple[bool, str | None]:
    """Sendet Wake- und Display-Nachricht an ein FCM-Geraet."""
    app = _get_fcm_app(cfg)
    if app is None:
        return False, "fcm_not_configured"
    if delivery is None and db is not None and push_log_id is not None:
        delivery = FcmDeliveryLog(
            push_log_id=push_log_id,
            fcm_token_id=fcm_token_row.id,
            user_id=fcm_token_row.user_id,
            sent_at=datetime.now(UTC).replace(tzinfo=None),
            success=False,
        )
        db.add(delivery)
        db.commit()

    from firebase_admin import messaging  # type: ignore

    data = {
        "url": url or "/",
        "title": title,
        "body": body,
        "channel_id": channel_id or "",
    }
    if delivery is not None:
        data["delivery_id"] = str(delivery.id)

    def _send(message, kind: str) -> tuple[bool, str | None, bool]:
        try:
            messaging.send(message)
            return True, None, False
        except (messaging.UnregisteredError, messaging.SenderIdMismatchError) as exc:
            log.info("FCM-Token %s ist ungueltig und wird entfernt: %s", fcm_token_row.id, exc)
            if db is not None:
                db.delete(fcm_token_row)
            if delivery is not None:
                delivery.fcm_token_id = None
            return False, "unregistered_pruned", True
        except messaging.QuotaExceededError as exc:
            log.warning("FCM-Quota ueberschritten fuer Token %s (%s): %s", fcm_token_row.id, kind, exc)
            return False, "quota_exceeded", False
        except messaging.ThirdPartyAuthError as exc:
            log.warning("FCM-Authentifizierung fehlgeschlagen fuer Token %s (%s): %s", fcm_token_row.id, kind, exc)
            return False, "sender_id_mismatch", False
        except Exception as exc:
            log.warning("FCM fehlgeschlagen fuer Token %s (%s): %s", fcm_token_row.id, kind, exc)
            return False, "unknown", False

    wake_data = dict(data, silent="1")
    wake_message = messaging.Message(
        data=wake_data,
        android=messaging.AndroidConfig(priority="high"),
        token=fcm_token_row.token,
    )
    wake_ok, wake_error, token_invalid = _send(wake_message, "wake")

    display_ok = False
    display_error: str | None = "unregistered_pruned" if token_invalid else None
    if not token_invalid:
        display_android = messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id=channel_id,
                sound="default",
                default_vibrate_timings=True,
            ),
        ) if channel_id else messaging.AndroidConfig(priority="high")
        display_message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data,
            android=display_android,
            token=fcm_token_row.token,
        )
        display_ok, display_error, _token_invalid = _send(display_message, "display")

    if delivery is not None:
        delivery.success = display_ok
        errors = []
        if not wake_ok:
            errors.append(f"Wake-Nachricht fehlgeschlagen ({wake_error or 'unknown'})")
        if not display_ok:
            errors.append(f"Display-Nachricht fehlgeschlagen ({display_error or 'unknown'})")
        delivery.error_code = display_error if not display_ok else wake_error
        delivery.error_detail = "; ".join(errors) or None
    return display_ok, display_error if not display_ok else wake_error


def upsert_fcm_token(
    db: Session,
    *,
    user_id: int,
    token: str,
    platform: str = "android",
    device_token_id: int | None = None,
) -> bool:
    """Legt einen FCM-Token an oder aktualisiert ihn, ohne selbst zu committen."""
    token = (token or "").strip()[:512]
    if not token:
        return False
    now = datetime.now(UTC)
    existing = db.query(FcmToken).filter(FcmToken.token == token).first()
    if existing:
        existing.user_id = user_id
        existing.device_token_id = device_token_id
        existing.last_used_at = now
    else:
        db.add(FcmToken(
            user_id=user_id,
            device_token_id=device_token_id,
            token=token,
            platform=platform[:20],
            created_at=now,
            last_used_at=now,
        ))
    return True


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


def _log_push(
    db: Session,
    title: str,
    body: str,
    url: str | None,
    source: str,
    target_user_id: int | None,
    sent_count: int = 0,
    total_count: int = 0,
    org_id: int | None = None,
) -> PushLog:
    entry = PushLog(
        title=title,
        body=body,
        url=url,
        source=source,
        org_id=org_id,
        target_user_id=target_user_id,
        sent_count=sent_count,
        total_count=total_count,
    )
    db.add(entry)
    db.flush()
    return entry


def send_push(subscription: PushSubscription, title: str, body: str,
              url: str | None = None, db: Session | None = None,
              extra: dict | None = None) -> bool:
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return False
    if not cfg["private_key"] or not cfg["public_key"]:
        return False
    try:
        from pywebpush import webpush
        payload = {"title": title, "body": body, "url": url or "/"}
        if extra:
            payload.update(extra)
        data = json.dumps(payload)
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
                      url: str | None, cfg: dict | None = None,
                      channel_id: str | None = None,
                      push_log_id: int | None = None,
                      commit_delivery_log: bool = True) -> int:
    """Sendet FCM an alle registrierten Tokens der angegebenen User-IDs.

    ``commit_delivery_log`` committet die Delivery-Zeilen VOR dem Versand, damit die
    ``delivery_id`` sichtbar ist, bevor das Geraet den Ack schickt (sonst laeuft der
    Ack ins Leere und "Zugestellt" bleibt bei 0). Ein Commit haengt hier zwingend an
    der Session des Aufrufers: ``fcm_delivery_log.push_log_id`` ist ein Fremdschluessel
    auf die noch nicht committete ``push_log``-Zeile, eine eigene Session koennte die
    Zeile also gar nicht anlegen. Aufrufer, die mitten in einer groesseren Transaktion
    stehen (``notify_vehicle`` aus ``incident_service``), setzen daher False und
    verzichten auf die Ack-Garantie.
    """
    tokens = db.query(FcmToken).filter(FcmToken.user_id.in_(user_ids)).all() if user_ids else []
    if push_log_id is None:
        fallback_log = _log_push(db, title, body, url, "system", None)
        push_log_id = fallback_log.id

    if _get_fcm_app(cfg) is None:
        if tokens:
            for token in tokens:
                db.add(FcmDeliveryLog(
                    push_log_id=push_log_id,
                    fcm_token_id=token.id,
                    user_id=token.user_id,
                    sent_at=datetime.now(UTC).replace(tzinfo=None),
                    success=False,
                    error_code="fcm_not_configured",
                    error_detail="FCM ist nicht konfiguriert oder konnte nicht initialisiert werden",
                ))
        else:
            # Pro Alarm/Push-Aufruf bleibt die Fehlkonfiguration auch ohne Token sichtbar.
            db.add(FcmDeliveryLog(
                push_log_id=push_log_id,
                fcm_token_id=None,
                user_id=None,
                sent_at=datetime.now(UTC).replace(tzinfo=None),
                success=False,
                error_code="fcm_not_configured",
                error_detail="FCM ist nicht konfiguriert oder konnte nicht initialisiert werden",
            ))
        if commit_delivery_log:
            db.commit()
        else:
            db.flush()
        return 0

    deliveries = []
    for token in tokens:
        delivery = FcmDeliveryLog(
            push_log_id=push_log_id,
            fcm_token_id=token.id,
            user_id=token.user_id,
            sent_at=datetime.now(UTC).replace(tzinfo=None),
            success=False,
        )
        db.add(delivery)
        deliveries.append((token, delivery))
    if commit_delivery_log:
        db.commit()
    else:
        db.flush()

    success_count = 0
    for token, delivery in deliveries:
        ok, _error_code = send_fcm(
            token,
            title,
            body,
            url,
            channel_id=channel_id,
            cfg=cfg,
            db=db,
            push_log_id=push_log_id,
            delivery=delivery,
        )
        success_count += int(ok)
    db.flush()
    return success_count


def _notify_fcm_logged(
    db: Session,
    user_ids: set[int],
    title: str,
    body: str,
    url: str | None,
    cfg: dict | None,
    channel_id: str | None,
    push_log_id: int,
    commit_delivery_log: bool = True,
) -> int:
    """Ruft den FCM-Fan-out mit PushLog-Verknuepfung auf."""
    try:
        return _notify_fcm_users(
            db,
            user_ids,
            title,
            body,
            url,
            cfg,
            channel_id,
            push_log_id=push_log_id,
            commit_delivery_log=commit_delivery_log,
        )
    except TypeError as exc:
        # Bestehende Erweiterungs-/Test-Doubles ohne das neue optionale Argument.
        if "push_log_id" not in str(exc):
            raise
        return _notify_fcm_users(db, user_ids, title, body, url, cfg, channel_id)


def notify_all(db: Session, title: str, body: str, url: str | None = None,
               source: str = "system", channel_id: str | None = None, *,
               extra: dict | None = None) -> int:
    cfg = _push_cfg(db)
    push_log = _log_push(db, title, body, url, source, None)
    # Web-Push (VAPID)
    if cfg["enabled"]:
        subs = db.query(PushSubscription).all()
        wp_count = sum(
            1 for s in subs if send_push(s, title, body, url, db=db, extra=extra)
        )
        push_log.sent_count = wp_count
        push_log.total_count = len(subs)
    else:
        wp_count = 0
    # FCM
    from app.models.user import User as _User
    all_user_ids = {row[0] for row in db.query(_User.id).all()}
    fcm_extra = _notify_fcm_logged(
        db, all_user_ids, title, body, url, cfg, channel_id, push_log.id
    )
    return wp_count + fcm_extra


def notify_org(db: Session, org_id: int, title: str, body: str,
               url: str | None = None, source: str = "system",
               channel_id: str | None = None, *, extra: dict | None = None) -> int:
    """Push nur an User der angegebenen Org (statt an alle)."""
    from app.models.user import User as _User
    cfg = _push_cfg(db)
    push_log = _log_push(db, title, body, url, source, None, org_id=org_id)
    org_user_ids_subq = db.query(_User.id).filter(_User.org_id == org_id)
    if cfg["enabled"]:
        subs = (
            db.query(PushSubscription)
            .filter(PushSubscription.user_id.in_(org_user_ids_subq))
            .all()
        )
        wp_count = sum(
            1 for s in subs if send_push(s, title, body, url, db=db, extra=extra)
        )
        push_log.sent_count = wp_count
        push_log.total_count = len(subs)
    else:
        wp_count = 0
    org_user_ids = {r[0] for r in db.query(_User.id).filter(_User.org_id == org_id).all()}
    fcm_extra = _notify_fcm_logged(
        db, org_user_ids, title, body, url, cfg, channel_id, push_log.id
    )
    return wp_count + fcm_extra


def notify_org_web(db: Session, org_id: int, title: str, body: str,
                   url: str | None = None, *, extra: dict | None = None,
                   source: str = "einsatz_live", log: bool = False) -> int:
    """Web-Push nur an User der angegebenen Org, ohne FCM-Fan-out."""
    from app.models.user import User as _User
    cfg = _push_cfg(db)
    if not cfg["enabled"]:
        return 0
    org_user_ids_subq = db.query(_User.id).filter(_User.org_id == org_id)
    subs = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_id.in_(org_user_ids_subq))
        .all()
    )
    wp_count = sum(
        1 for sub in subs
        if send_push(sub, title, body, url, db=db, extra=extra)
    )
    if log:
        _log_push(db, title, body, url, source, None, wp_count, len(subs), org_id=org_id)
    return wp_count


def notify_user(db: Session, user_id: int, title: str, body: str,
                url: str | None = None, source: str = "system") -> int:
    from app.models.user import User as _User

    cfg = _push_cfg(db)
    target_org_id = db.query(_User.org_id).filter(_User.id == user_id).scalar()
    push_log = _log_push(db, title, body, url, source, user_id, org_id=target_org_id)
    # Web-Push
    if cfg["enabled"]:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        push_log.sent_count = wp_count
        push_log.total_count = len(subs)
    else:
        wp_count = 0
    # FCM
    fcm_extra = _notify_fcm_logged(db, {user_id}, title, body, url, cfg, None, push_log.id)
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
    push_log = _log_push(db, title, body, url, "vehicle_assigned", None)
    user_ids = {dt.user_id for dt in device_tokens}
    # Web-Push
    if cfg["enabled"]:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
        wp_count = sum(1 for s in subs if send_push(s, title, body, url, db=db))
        push_log.sent_count = wp_count
        push_log.total_count = len(subs)
    else:
        wp_count = 0
    # FCM. Kein Commit der Delivery-Zeilen: notify_vehicle laeuft mitten in der
    # Transaktion des Aufrufers (incident_service: Auftrag/Meldung zuweisen), ein
    # Zwischen-Commit wuerde dort halbfertige Zuweisungen festschreiben.
    fcm_extra = _notify_fcm_logged(
        db, user_ids, title, body, url, cfg, None, push_log.id,
        commit_delivery_log=False,
    )
    return wp_count + fcm_extra
