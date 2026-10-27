"""Org-loser Kontext mit Live-Pruefung bei jedem Zugriff."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.admin_security import utcnow
from app.models.master import SystemSettings
from app.models.user import User
from app.services import totp_service

REFRESH_LIFETIME = timedelta(hours=8)
DENIED = "Anmeldung oder Zugriff nicht moeglich."


@dataclass
class AdminContext:
    db: Session
    user: User
    client_id: str


def allowed(db: Session, user: User | None) -> bool:
    switch = db.get(SystemSettings, "admin_mcp_enabled")
    return bool(user and user.active and not user.is_device and user.is_system_admin
                and user.admin_mcp_access and switch and switch.value == "true")


def load_live_context(db: Session, row) -> AdminContext:
    user = db.query(User).execution_options(include_all_tenants=True).filter(User.id == row.user_id)\
        .populate_existing().first()
    if (not allowed(db, user) or user is None or user.session_epoch != row.session_epoch
            or row.scopes.split() != ["mcp-admin"]
            or (totp_service.enabled(db) and (row.mfa_at is None
                or not timedelta(0) <= utcnow() - row.mfa_at <= REFRESH_LIFETIME))):
        raise PermissionError(DENIED)
    return AdminContext(db, user, row.client_id)


@contextmanager
def admin_session():
    """Explizit org-lose System-Session, auch fuer nachgeladene User-Beziehungen."""
    with SessionLocal() as db:
        set_tenant_context(db, None)
        yield db
