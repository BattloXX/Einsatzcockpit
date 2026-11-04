"""SMS-Faktor: eigene verschluesselte Nummer, Einmalcodes und Versandlimits.

Aufrufer committen auch Fehlversuche und versenden Benachrichtigungen nach Commit.
Die Login-Methode wird an den interaktiven Routen geprueft.
"""
import hashlib
import hmac
import logging
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.async_bridge import run_async
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import generate_numeric_pin
from app.core.telefon import telefon_e164, telefon_identitaet_at, telefon_maske
from app.models.admin_security import AdminRecoveryCode, UserMfaSms, utcnow
from app.models.login_pin import LoginPin
from app.models.master import OrgSettings
from app.models.user import User
from app.services import mfa_service
from app.services.sms_service import send_sms, sms_available


def sms_allowed(db: Session, user: User) -> bool:
    if user.is_system_admin or user.is_device or user.org_id is None:
        return False
    config = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    return (config is None or config.mfa_sms_erlaubt) and sms_available(user.org_id, db)


def _serialized_factor(db: Session, user_id: int) -> UserMfaSms | None:
    # Current Read statt altem Repeatable-Read-Snapshot: Limits und Sperren
    # muessen nach dem Warten auf einen anderen Request dessen Commit sehen.
    return db.query(UserMfaSms).filter(UserMfaSms.user_id == user_id).with_for_update().populate_existing().first()


def _pins(db: Session, user_id: int):
    return db.query(LoginPin).filter(LoginPin.user_id == user_id).with_for_update().populate_existing()


def send_code(db: Session, user: User, purpose: str, phone: str | None = None) -> None:
    if purpose not in {"mfa", "mfa_setup"}:
        raise ValueError("purpose")
    if not sms_allowed(db, user):
        raise ValueError("unavailable")
    factor = _serialized_factor(db, user.id)
    if factor is None or (purpose == "mfa" and factor.verified_at is None):
        raise ValueError("unconfigured")
    # Auch bei uebergebenem phone stammt das Versandziel immer aus dem Faktor.
    target = decrypt_secret(factor.phone_enc)
    if phone is not None and phone != target:
        raise ValueError("phone")
    now = utcnow()
    if factor.locked_until and factor.locked_until > now:
        raise ValueError("locked")
    recent = _pins(db, user.id).filter(
        LoginPin.purpose.in_(("mfa", "mfa_setup")),
        LoginPin.created_at >= now - timedelta(hours=24),
    ).all()
    if len(recent) >= 10 or sum(p.created_at >= now - timedelta(minutes=10) for p in recent) >= 3:
        raise ValueError("limit")
    for old in _pins(db, user.id).filter(
        LoginPin.purpose == purpose, LoginPin.used_at.is_(None),
    ).all():
        old.used_at = now
    code = generate_numeric_pin()
    pin = LoginPin(user_id=user.id, purpose=purpose, pin_hash=hashlib.sha256(code.encode()).hexdigest(),
                   created_at=now, expires_at=now + timedelta(minutes=5))
    db.add(pin)
    db.flush()
    assert user.org_id is not None
    try:
        result = run_async(send_sms, user.org_id, target,
                           f"{settings.APP_NAME}-Sicherheitscode: {code} (5 Minuten gueltig). Niemals weitergeben.")
        if not result.success:
            raise ValueError("unavailable")
    except Exception as exc:
        pin.used_at = now
        logging.getLogger(__name__).warning("MFA-SMS-Versand fehlgeschlagen (user_id=%s)", user.id)
        raise ValueError("unavailable") from exc
    mfa_service.audit(db, "sms_sent", user.id, org_id=user.org_id,
                      payload={"nummer": telefon_maske(target), "purpose": purpose})


def verify_sms(db: Session, user: User, code: str, purpose: str) -> bool:
    if purpose not in {"mfa", "mfa_setup"}:
        return False
    factor = _serialized_factor(db, user.id)
    now = utcnow()
    if (not sms_allowed(db, user) or factor is None
            or (purpose == "mfa" and factor.verified_at is None)
            or (factor.locked_until and factor.locked_until > now)):
        mfa_service.audit(db, "sms_failed", user.id)
        return False
    pin = _pins(db, user.id).filter(
        LoginPin.purpose == purpose, LoginPin.used_at.is_(None),
    ).order_by(LoginPin.created_at.desc(), LoginPin.id.desc()).first()
    success = False
    if (pin and pin.expires_at > now and pin.attempt_count < 5
            and hmac.compare_digest(pin.pin_hash, hashlib.sha256(code.strip().encode()).hexdigest())):
        result = db.execute(update(LoginPin).where(
            LoginPin.id == pin.id, LoginPin.user_id == user.id, LoginPin.purpose == purpose,
            LoginPin.used_at.is_(None), LoginPin.attempt_count < 5, LoginPin.expires_at > now,
        ).values(used_at=now), execution_options={"synchronize_session": False})
        success = result.rowcount == 1  # type: ignore[attr-defined]
    if success:
        factor.failed_count = 0
        factor.locked_until = None
    else:
        if pin:
            db.execute(update(LoginPin).where(LoginPin.id == pin.id, LoginPin.user_id == user.id).values(
                attempt_count=LoginPin.attempt_count + 1), execution_options={"synchronize_session": False})
        if factor.locked_until:
            factor.failed_count = 0
            factor.locked_until = None
        # Atomarer Zaehler verhindert verlorene Fehlversuche bei parallelen Requests.
        db.flush()
        db.execute(update(UserMfaSms).where(UserMfaSms.user_id == user.id).values(
            failed_count=UserMfaSms.failed_count + 1), execution_options={"synchronize_session": False})
        db.refresh(factor)
        db.execute(update(UserMfaSms).where(
            UserMfaSms.user_id == user.id, UserMfaSms.failed_count >= 5,
        ).values(locked_until=now + timedelta(minutes=5)), execution_options={"synchronize_session": False})
        db.refresh(factor)
    mfa_service.audit(db, "sms_verified" if success else "sms_failed", user.id)
    db.flush()
    return success


def start_sms_setup(db: Session, user: User, phone: str) -> UserMfaSms:
    if not sms_allowed(db, user):
        raise ValueError("unavailable")
    normalized = telefon_identitaet_at(phone)
    if not telefon_e164(normalized):
        raise ValueError("phone")
    row = _serialized_factor(db, user.id)
    if row is None:
        row = UserMfaSms(user_id=user.id, phone_enc=encrypt_secret(normalized))
        db.add(row)
    else:
        row.phone_enc = encrypt_secret(normalized)
        row.verified_at = None
    # Codes an die alte Nummer duerfen nach einem Nummernwechsel nicht gelten.
    for pin in _pins(db, user.id).filter(LoginPin.purpose == "mfa", LoginPin.used_at.is_(None)).all():
        pin.used_at = utcnow()
    db.flush()
    send_code(db, user, "mfa_setup", normalized)
    return row


def confirm_sms_setup(db: Session, user: User, code: str) -> list[str] | None:
    row = db.get(UserMfaSms, user.id)
    if row is None or row.verified_at is not None or not verify_sms(db, user, code, "mfa_setup"):
        return None
    row.verified_at = utcnow()
    user.mfa_reset_at = None
    codes = [] if db.query(AdminRecoveryCode.id).filter(
        AdminRecoveryCode.user_id == user.id, AdminRecoveryCode.used_at.is_(None),
    ).first() else mfa_service.create_recovery_codes(db, user.id)
    mfa_service.audit(db, "sms_confirmed", user.id, org_id=user.org_id,
                      payload={"nummer": telefon_maske(decrypt_secret(row.phone_enc))})
    return codes


def notify_setup(db: Session, user: User) -> None:
    from app.services.mail_service import _build_message, _org_smtp_cfg, deliver, get_smtp_cfg, mail_available

    row = db.get(UserMfaSms, user.id)
    try:
        if row and user.email and mail_available(db, user.org_id):
            message = _build_message(
                to=user.email, subject="SMS als zweiter Faktor eingerichtet",
                body_txt="SMS als zweiter Faktor eingerichtet: " + telefon_maske(decrypt_secret(row.phone_enc)),
                smtp_cfg=_org_smtp_cfg(db, user.org_id) or get_smtp_cfg(db),
            )
            run_async(deliver, db, user.org_id, message)
    except Exception:
        logging.getLogger(__name__).exception("MFA-SMS-Einrichtungs-Mail fehlgeschlagen (user_id=%s)", user.id)


def notify_reset_sms(user: User, phone: str | None) -> None:
    if not phone or user.org_id is None:
        return
    try:
        result = run_async(send_sms, user.org_id, phone,
                           "Ihre Zwei-Faktor-Authentifizierung wurde zurueckgesetzt.")
        if not result.success:
            logging.getLogger(__name__).warning("MFA-Reset-SMS fehlgeschlagen (user_id=%s)", user.id)
    except Exception:
        logging.getLogger(__name__).exception("MFA-Reset-SMS fehlgeschlagen (user_id=%s)", user.id)
