"""Gemeinsame Benutzerverwaltung fuer Admin-UI und MCP."""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import manageable_role_codes, permitted_submitted_roles
from app.core.security import hash_password
from app.models.master import Member
from app.models.password_reset import PasswordResetToken
from app.models.user import Role, User, UserRole


def validate_member_link(
    db: Session, member_id: int | None, target_org_id: int | None, target_is_device: bool,
    user_id: int | None = None,
) -> str | None:
    """Prueft eine Benutzer-Mitglied-Verknuepfung wie die Admin-Oberflaeche."""
    if member_id is None:
        return None
    if target_is_device:
        return "member_invalid"
    member = db.query(Member).execution_options(include_all_tenants=True).filter(Member.id == member_id).first()
    if member is None or member.org_id != target_org_id:
        return "member_invalid"
    existing = db.query(User).execution_options(include_all_tenants=True).filter(User.member_id == member_id)
    if user_id is not None:
        existing = existing.filter(User.id != user_id)
    return "member_taken" if existing.first() is not None else None


def update_user_roles(db: Session, actor: User, target: User, submitted_codes: list[str], action: str) -> list[str]:
    """Ersetzt verwaltbare Rollen und behaelt nicht verwaltbare Rollen bei."""
    allowed_submitted = permitted_submitted_roles(db, actor, submitted_codes, action=action, target=target)
    existing_codes = {role.code for role in target.roles}
    all_roles = {role.code: role for role in db.query(Role).all()}
    manageable = manageable_role_codes(actor, set(all_roles))
    final_codes = (existing_codes - manageable) | allowed_submitted
    db.query(UserRole).filter(UserRole.user_id == target.id).delete()
    for code in final_codes:
        db.add(UserRole(user_id=target.id, role_id=all_roles[code].id))
    return sorted(final_codes)


def create_user(
    db: Session, *, org_id: int, username: str, display_name: str, email: str = "", full_name: str = "",
    phone: str = "", member_id: int | None = None, password: str | None = None,
) -> tuple[User, str]:
    """Legt einen lokalen Benutzer mit zufaelligem Passwort an."""
    username = username.strip()
    if not username:
        raise ValueError("Benutzername darf nicht leer sein.")
    if db.query(User).filter(User.username == username).first():
        raise ValueError("Benutzername ist bereits vergeben.")
    email_clean = email.strip().lower() or None
    if email_clean and db.query(User).filter(User.email == email_clean).first():
        raise ValueError("E-Mail-Adresse ist bereits vergeben.")
    member_error = validate_member_link(db, member_id, org_id, False)
    if member_error:
        raise ValueError("Mitglied ist ungueltig oder bereits verknuepft.")
    generated_password = password or secrets.token_urlsafe(16)
    user = User(
        username=username, display_name=display_name.strip() or username, full_name=full_name.strip() or None,
        email=email_clean, phone=phone.strip() or None, password_hash=hash_password(generated_password),
        org_id=org_id, member_id=member_id,
    )
    db.add(user)
    db.flush()
    return user, generated_password


async def send_welcome_mail(user: User, password: str, db: Session) -> None:
    """Versendet die Willkommensmail fuer einen neu angelegten lokalen Benutzer."""
    if not user.email:
        return
    from app.services.mail_service import send_welcome_mail as _send_welcome_mail

    await _send_welcome_mail(
        to=user.email, username=user.username, password=password,
        user_display_name=user.full_name or user.display_name or user.username,
        app_url=settings.effective_public_base_url.rstrip("/"), is_test=settings.TEST_SYSTEM,
        db=db, org_id=user.org_id,
    )


async def send_password_reset_mail(user: User, db: Session, requesting_ip: str | None = None) -> None:
    """Entwertet alte Reset-Tokens, erstellt einen neuen und versendet die Reset-Mail.

    Token (und ein vom Aufrufer vorbereitetes Audit) werden vor dem Versand committet,
    damit der Link auch bei einem spaeten SMTP-Fehler gueltig bleibt (Verhalten der Admin-UI).
    """
    if not user.email:
        raise ValueError("Benutzer hat keine E-Mail-Adresse.")
    now = datetime.now(UTC)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now})
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MIN), requesting_ip=requesting_ip,
    ))
    db.commit()
    from app.services.mail_service import send_password_reset

    reset_url = f"{settings.effective_public_base_url.rstrip('/')}/passwort-zuruecksetzen?token={raw_token}"
    await send_password_reset(
        to=user.email, reset_url=reset_url, user_display_name=user.full_name or user.display_name or user.username,
        db=db, org_id=user.org_id,
    )
