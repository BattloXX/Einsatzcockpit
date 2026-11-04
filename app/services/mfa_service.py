"""RFC-6238 und persistenter Replay-/Brute-Force-Schutz fuer MFA.

Aufrufer committen auch fehlgeschlagene Pruefungen. Die Zeilensperre serialisiert
Versuche pro Benutzer in MariaDB; bedingte Updates schuetzen Einmalwerte zudem atomar.
"""
import base64
import hashlib
import hmac
import io
import logging
import math
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.permissions import ORG_ADMIN_ROLES, authorize_user_mutation
from app.models.admin_security import AdminRecoveryCode, AdminTotp, UserMfaSms, utcnow
from app.models.master import OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole

MAX_FAILED = 5
LOCK_SECONDS = 300
RESET_SETUP_HOURS = 24
MFA_POLICIES = ("aus", "optional", "admins", "alle")
_POLICY_CACHE_TTL = 30
_policy_cache: dict[int, tuple[float, str, bool]] = {}


def clear_policy_cache() -> None:
    """Org-Richtlinien verwerfen, etwa nach einer Aenderung oder in Tests."""
    _policy_cache.clear()


def required_for_cached(user: User) -> bool | None:
    """Ohne DB pruefen; None fordert einen Policy-Lookup an."""
    if user.is_device or user.username.startswith("alarmlink-gast@org"):
        return False
    if user.is_system_admin:
        return None
    if user.org_id is None:
        return False
    cached = _policy_cache.get(user.org_id)
    if cached is None or time.monotonic() - cached[0] >= _POLICY_CACHE_TTL:
        return None
    _, policy, sso_exempt = cached
    if user.entra_oid and sso_exempt:
        return False
    return policy == "alle" or (policy == "admins" and bool(user.role_codes & ORG_ADMIN_ROLES))


def required_for(db: Session, user: User) -> bool:
    """MFA-Pflicht aus globalem Schalter oder Org-Richtlinie ermitteln."""
    cached = required_for_cached(user)
    if cached is not None:
        return cached
    if user.is_system_admin:
        return enabled(db)
    row = db.query(OrgSettings.mfa_policy, OrgSettings.mfa_sso_ausgenommen).execution_options(
        include_all_tenants=True,
    ).filter(OrgSettings.org_id == user.org_id).first()
    policy, sso_exempt = (row.mfa_policy, row.mfa_sso_ausgenommen) if row else ("aus", True)
    assert user.org_id is not None
    _policy_cache[user.org_id] = (time.monotonic(), policy, sso_exempt)
    if user.entra_oid and sso_exempt:
        return False
    return policy == "alle" or (policy == "admins" and bool(user.role_codes & ORG_ADMIN_ROLES))


def enabled(db: Session) -> bool:
    row = db.get(SystemSettings, "admin_2fa_enabled")
    return row is not None and row.value == "true"


def admins(db: Session, *, active_only: bool = True) -> list[User]:
    return db.query(User).execution_options(include_all_tenants=True).join(UserRole).join(Role).filter(
        Role.code == "system_admin", *([User.active.is_(True)] if active_only else []),
    ).order_by(User.display_name).all()


def audit(db: Session, event: str, user_id: int | None, **kwargs) -> None:
    write_audit(db, "admin.mfa." + event, user_id=user_id, **kwargs)


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(time.time() if now is None else now) // 30
    matches = [step for step in range(max(0, current - 1), current + 2)
               if hmac.compare_digest(totp(secret, step), code)]
    return max(matches) if matches else None


def provisioning_uri(secret: str, username: str) -> str:
    return "otpauth://totp/" + quote("Einsatzcockpit:" + username, safe="") + "?" + urlencode({
        "secret": secret, "issuer": "Einsatzcockpit", "algorithm": "SHA1", "digits": 6, "period": 30,
    })


def qr_data_uri(uri: str) -> str | None:
    try:
        import qrcode

        image = qrcode.make(uri)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    except (ImportError, OSError):
        logging.getLogger(__name__).warning("MFA-QR nicht verfuegbar: qrcode/Pillow fehlt oder ist defekt")
        return None


def start_setup(db: Session, user: User) -> AdminTotp:
    row = db.get(AdminTotp, user.id)
    if row is None:
        row = AdminTotp(user_id=user.id, secret_enc=encrypt_secret(generate_secret()))
        db.add(row)
        audit(db, "setup_started", user.id)
        db.flush()
    return row


def recovery_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().replace("-", "").encode()).hexdigest()


def create_recovery_codes(db: Session, user_id: int) -> list[str]:
    for row in db.query(AdminRecoveryCode).filter(AdminRecoveryCode.user_id == user_id).all():
        db.delete(row)
    codes = [secrets.token_hex(10).upper() for _ in range(10)]
    formatted = ["-".join(code[i:i + 5] for i in range(0, 20, 5)) for code in codes]
    for code in formatted:
        db.add(AdminRecoveryCode(user_id=user_id, code_hash=recovery_hash(code)))
    return formatted


def verify(db: Session, user_id: int, code: str, *, confirming: bool = False) -> bool:
    row = db.query(AdminTotp).filter(AdminTotp.user_id == user_id).with_for_update().populate_existing().first()
    now = utcnow()
    if (not confirming and (row is None or row.confirmed_at is None)
            and (sms := db.get(UserMfaSms, user_id)) and sms.verified_at):
        return verify_recovery(db, user_id, code)
    if row is None or (not confirming and row.confirmed_at is None) or (confirming and row.confirmed_at is not None):
        audit(db, "failed", user_id)
        return False
    if row.locked_until and row.locked_until > now:
        audit(db, "failed", user_id)
        return False
    step = matching_step(decrypt_secret(row.secret_enc), code.strip())
    success = False
    recovery = False
    if step is not None:
        result = db.execute(update(AdminTotp).where(
            AdminTotp.user_id == user_id,
            or_(AdminTotp.last_used_step.is_(None), AdminTotp.last_used_step < step),
        ).values(last_used_step=step), execution_options={"synchronize_session": False})
        success = result.rowcount == 1  # type: ignore[attr-defined]
    elif not confirming:
        candidate = db.query(AdminRecoveryCode).filter(
            AdminRecoveryCode.user_id == user_id, AdminRecoveryCode.code_hash == recovery_hash(code),
            AdminRecoveryCode.used_at.is_(None),
        ).first()
        if candidate:
            result = db.execute(update(AdminRecoveryCode).where(
                AdminRecoveryCode.id == candidate.id, AdminRecoveryCode.used_at.is_(None),
            ).values(used_at=now), execution_options={"synchronize_session": False})
            success = result.rowcount == 1  # type: ignore[attr-defined]
            recovery = success
    if success:
        row.failed_count = 0
        row.locked_until = None
        if confirming:
            row.confirmed_at = now
            user = db.get(User, user_id)
            if user is not None:
                user.mfa_reset_at = None
        audit(db, "confirmed" if confirming else "recovery_used" if recovery else "verified", user_id)
    else:
        if row.locked_until:
            row.failed_count = 0
            row.locked_until = None
        row.failed_count += 1
        if row.failed_count >= MAX_FAILED:
            row.locked_until = now + timedelta(seconds=LOCK_SECONDS)
        audit(db, "failed", user_id)
    db.flush()
    return success



def verify_recovery(db: Session, user_id: int, code: str) -> bool:
    """Recovery fuer SMS-Konten ohne bestaetigten TOTP-Faktor, mit gleicher Sperre."""
    from app.services.mfa_sms_service import _serialized_factor

    factor = _serialized_factor(db, user_id)
    now = utcnow()
    if factor is None or factor.verified_at is None or (factor.locked_until and factor.locked_until > now):
        audit(db, "failed", user_id)
        return False
    candidate = db.query(AdminRecoveryCode).filter(
        AdminRecoveryCode.user_id == user_id, AdminRecoveryCode.code_hash == recovery_hash(code),
        AdminRecoveryCode.used_at.is_(None),
    ).first()
    success = False
    if candidate:
        result = db.execute(update(AdminRecoveryCode).where(
            AdminRecoveryCode.id == candidate.id, AdminRecoveryCode.used_at.is_(None),
        ).values(used_at=now), execution_options={"synchronize_session": False})
        success = result.rowcount == 1  # type: ignore[attr-defined]
    if success:
        factor.failed_count = 0
        factor.locked_until = None
    else:
        if factor.locked_until:
            factor.failed_count = 0
            factor.locked_until = None
        db.flush()
        db.execute(update(UserMfaSms).where(UserMfaSms.user_id == user_id).values(
            failed_count=UserMfaSms.failed_count + 1), execution_options={"synchronize_session": False})
        db.execute(update(UserMfaSms).where(
            UserMfaSms.user_id == user_id, UserMfaSms.failed_count >= MAX_FAILED,
        ).values(locked_until=now + timedelta(seconds=LOCK_SECONDS)),
            execution_options={"synchronize_session": False})
        db.refresh(factor)
    audit(db, "recovery_used" if success else "failed", user_id)
    db.flush()
    return success

def diagnose_failure(db: Session, user_id: int, code: str) -> tuple[str, int | None]:
    """Nach verify()-Fehlschlag nur lesen; keine Zaehler, Faktoren oder Audits aendern."""
    with db.no_autoflush:
        factor = db.query(AdminTotp.secret_enc, AdminTotp.confirmed_at,
                          AdminTotp.locked_until, AdminTotp.last_used_step).filter(
                              AdminTotp.user_id == user_id).first()
        if factor is None or factor.confirmed_at is None:
            return "nicht_eingerichtet", None
        now = utcnow()
        if factor.locked_until and factor.locked_until > now:
            return "gesperrt", max(1, math.ceil((factor.locked_until - now).total_seconds() / 60))
        step = matching_step(decrypt_secret(factor.secret_enc), code.strip())
        if step is not None and factor.last_used_step is not None and step <= factor.last_used_step:
            return "verbraucht", None
        if step is None and db.query(AdminRecoveryCode.id).filter(
            AdminRecoveryCode.user_id == user_id, AdminRecoveryCode.code_hash == recovery_hash(code),
            AdminRecoveryCode.used_at.is_not(None),
        ).first():
            return "verbraucht", None
    return "ungueltig", None


def fresh(mfa_at: int | None) -> bool:
    return mfa_at is not None and 0 <= time.time() - mfa_at <= 300


def set_enabled(db: Session, value: bool, actor_id: int | None, mfa_at: int | None, *, local: bool = False) -> None:
    if value:
        missing = [u.display_name for u in admins(db)
                   if not (factor := db.get(AdminTotp, u.id)) or factor.confirmed_at is None]
        if missing:
            raise ValueError("2FA noch nicht eingerichtet: " + ", ".join(missing))
    elif not local and not fresh(mfa_at):
        raise ValueError("Zum Deaktivieren zuerst 2FA erneut bestaetigen (maximal 5 Minuten).")
    row = db.get(SystemSettings, "admin_2fa_enabled")
    if row is None:
        row = SystemSettings(key="admin_2fa_enabled")
        db.add(row)
    row.value = "true" if value else "false"
    row.updated_by_user_id = actor_id
    if not value:
        for user in admins(db, active_only=False):
            user.session_epoch += 1
    audit(db, "global_enabled" if value else "global_disabled", actor_id, payload={"local": local})


def reset(db: Session, user: User, actor_id: int | None, *, local: bool = False) -> None:
    row = db.get(AdminTotp, user.id)
    if row:
        db.delete(row)
    sms = db.get(UserMfaSms, user.id)
    if sms:
        db.delete(sms)
    from app.models.login_pin import LoginPin

    for pin in db.query(LoginPin).filter(
        LoginPin.user_id == user.id, LoginPin.purpose.in_(("mfa", "mfa_setup")), LoginPin.used_at.is_(None),
    ).all():
        db.delete(pin)
    for recovery in db.query(AdminRecoveryCode).filter(AdminRecoveryCode.user_id == user.id).all():
        db.delete(recovery)
    user.session_epoch += 1
    audit(db, "reset", actor_id, entity_type="user", entity_id=user.id, payload={"local": local})


def status_from_totp(user: User, factor: AdminTotp | None, sms: UserMfaSms | None = None) -> dict:
    """Status aus gebuendelt vorgeladenen Faktoren."""
    has_totp = factor is not None and factor.confirmed_at is not None
    has_sms = sms is not None and sms.verified_at is not None
    dates = [d for d in (factor.confirmed_at if has_totp and factor else None,
                         sms.verified_at if has_sms and sms else None) if d]
    locks = [f.locked_until for f in (factor, sms) if f and f.locked_until and f.locked_until > utcnow()]
    return {
        "aktiv": has_totp or has_sms,
        "methode": "totp+sms" if has_totp and has_sms else "totp" if has_totp else "sms" if has_sms else None,
        "eingerichtet_at": min(dates) if dates else None,
        "gesperrt_bis": max(locks) if locks else None,
        "reset_offen": user.mfa_reset_at is not None and not (has_totp or has_sms),
    }


def status(db: Session, user: User) -> dict:
    return status_from_totp(user, db.get(AdminTotp, user.id), db.get(UserMfaSms, user.id))


def setup_expired(db: Session, user: User) -> bool:
    return bool(user.mfa_reset_at and user.mfa_reset_at < utcnow() - timedelta(hours=RESET_SETUP_HOURS)
                and not status(db, user)["aktiv"] and required_for(db, user))


def _authorize_admin_action(db: Session, actor: User, target: User, actor_mfa_at: int | None) -> None:
    authorize_user_mutation(db, actor, target, "mfa_reset")
    if target.is_device:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if actor.id == target.id:
        raise HTTPException(403, "Eigene 2FA nur im Profil zuruecksetzen")
    needs_step_up = status(db, actor)["aktiv"] or required_for(db, actor)
    if needs_step_up and not fresh(actor_mfa_at):
        raise HTTPException(403, "Zuerst 2FA erneut bestaetigen")


def admin_reset(db: Session, actor: User, target: User, grund: str, actor_mfa_at: int | None) -> str | None:
    """Reset vorbereiten; Aufrufer committet und benachrichtigt danach das Ziel."""
    _authorize_admin_action(db, actor, target, actor_mfa_at)
    grund = grund.strip()
    if not 3 <= len(grund) <= 500:
        raise HTTPException(400, "Grund muss 3 bis 500 Zeichen enthalten")
    sms = db.get(UserMfaSms, target.id)
    phone = decrypt_secret(sms.phone_enc) if sms and sms.verified_at else None
    reset(db, target, actor.id)
    target.mfa_reset_at = utcnow()
    audit(db, "reset", actor.id, org_id=target.org_id, entity_type="user", entity_id=target.id,
          payload={"grund": grund, "akteur_rolle": "system_admin" if actor.is_system_admin else "org_admin"})

    return phone


def admin_unlock(db: Session, actor: User, target: User, actor_mfa_at: int | None) -> None:
    _authorize_admin_action(db, actor, target, actor_mfa_at)
    factor = db.get(AdminTotp, target.id)
    sms = db.get(UserMfaSms, target.id)
    if factor is None and sms is None:
        raise HTTPException(400, "Keine 2FA eingerichtet")
    for row in (factor, sms):
        if row is not None:
            row.failed_count = 0
            row.locked_until = None
    audit(db, "unlock", actor.id, org_id=target.org_id, entity_type="user", entity_id=target.id,
          payload={"akteur_rolle": "system_admin" if actor.is_system_admin else "org_admin"})


def notify_reset(db: Session, user: User) -> None:
    """Hinweis nach erfolgreichem Commit; keine Faktoren im Mailinhalt oder Log."""
    from app.core.async_bridge import run_async
    from app.services.mail_service import _build_message, _org_smtp_cfg, deliver, get_smtp_cfg, mail_available

    try:
        if user.email and mail_available(db, user.org_id):
            message = _build_message(
                to=user.email, subject="Einsatzcockpit: 2FA zurueckgesetzt",
                body_txt=("Ihre Zwei-Faktor-Authentifizierung wurde zurueckgesetzt. "
                          "Bitte richten Sie die Zwei-Faktor-Authentifizierung innerhalb von 24 Stunden neu ein. "
                          "Bei unbekannter Aktion kontaktieren Sie die Administration."),
                smtp_cfg=_org_smtp_cfg(db, user.org_id) or get_smtp_cfg(db),
            )
            run_async(deliver, db, user.org_id, message)
    except Exception:
        logging.getLogger(__name__).exception("2FA-Reset-Mail fehlgeschlagen (user_id=%s)", user.id)
