"""RFC-6238 und persistenter Replay-/Brute-Force-Schutz fuer Admin-2FA.

Aufrufer committen auch fehlgeschlagene Pruefungen. Die Zeilensperre serialisiert
Versuche pro Benutzer in MariaDB; bedingte Updates schuetzen Einmalwerte zudem atomar.
"""
import base64
import hashlib
import hmac
import io
import logging
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote, urlencode

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.admin_security import AdminRecoveryCode, AdminTotp, utcnow
from app.models.master import SystemSettings
from app.models.user import Role, User, UserRole

MAX_FAILED = 5
LOCK_SECONDS = 300


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
    for recovery in db.query(AdminRecoveryCode).filter(AdminRecoveryCode.user_id == user.id).all():
        db.delete(recovery)
    user.session_epoch += 1
    audit(db, "reset", actor_id, entity_type="user", entity_id=user.id, payload={"local": local})


def notify_reset(db: Session, user: User) -> None:
    """Hinweis nach erfolgreichem Commit; keine Faktoren im Mailinhalt oder Log."""
    from app.core.async_bridge import run_async
    from app.services.mail_service import _build_message, _org_smtp_cfg, deliver, get_smtp_cfg, mail_available

    try:
        if user.email and mail_available(db, user.org_id):
            message = _build_message(
                to=user.email, subject="Einsatzcockpit: 2FA zurueckgesetzt",
                body_txt=("Ihre Zwei-Faktor-Authentifizierung wurde zurueckgesetzt. "
                          "Bei unbekannter Aktion kontaktieren Sie die Administration."),
                smtp_cfg=_org_smtp_cfg(db, user.org_id) or get_smtp_cfg(db),
            )
            run_async(deliver, db, user.org_id, message)
    except Exception:
        logging.getLogger(__name__).exception("2FA-Reset-Mail fehlgeschlagen (user_id=%s)", user.id)
