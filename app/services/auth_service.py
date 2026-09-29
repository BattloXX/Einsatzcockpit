"""Gemeinsame Passwort-Authentifizierung fuer Web- und MCP-Login."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.security import hash_password, verify_password
from app.models.user import User

_dummy_password_hash: str | None = None


def _dummy_hash() -> str:
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = hash_password(secrets.token_urlsafe(32))
    return _dummy_password_hash


def authenticate_user(db: Session, username: str, password: str, ip: str | None) -> tuple[User | None, str | None]:
    """Prueft lokale Zugangsdaten inklusive Lockout und schreibt das bisherige Audit."""
    now = datetime.now(UTC)
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active:
        verify_password(password, _dummy_hash())
        return None, "Benutzername oder Passwort falsch"
    if user.org_id and getattr(user, "auth_provider", "local") == "entra":
        from app.models.sso import OrgSsoConfig

        if (
            db.query(OrgSsoConfig)
            .filter(
                OrgSsoConfig.org_id == user.org_id,
                OrgSsoConfig.enabled.is_(True),
                OrgSsoConfig.enforce_sso.is_(True),
            )
            .first()
        ):
            return None, "enforce_sso"
    if user.locked_until:
        until = user.locked_until.replace(tzinfo=UTC) if user.locked_until.tzinfo is None else user.locked_until
        if until > now:
            write_audit(db, "auth.login.locked", user_id=user.id, ip=ip)
            db.commit()
            return None, "Account ist aktuell gesperrt. Bitte später erneut versuchen."
        user.locked_until, user.failed_login_count = None, 0
    if not user.password_hash or not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        action = "auth.login.failed"
        if user.failed_login_count >= settings.LOGIN_MAX_FAILED:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            action = "auth.login.lockout_triggered"
        write_audit(db, action, user_id=user.id, ip=ip, payload={"failed_count": user.failed_login_count})
        db.commit()
        return None, "Benutzername oder Passwort falsch"
    user.last_login_at, user.failed_login_count, user.locked_until = now, 0, None
    write_audit(db, "auth.login", user_id=user.id, ip=ip)
    db.commit()
    return user, None
