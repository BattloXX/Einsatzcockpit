"""Live-Kontext fuer MCP-Aufrufe; Token enthalten bewusst keine Rechte."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.core.tenant import set_tenant_context
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User


@dataclass
class MCPContext:
    db: Session
    user: User
    org_id: int


class MCPPermissionError(PermissionError):
    pass


def mcp_effective_enabled(org_id: int, db: Session) -> bool:
    system = db.query(SystemSettings).filter(
        SystemSettings.key == "mcp_module_enabled", SystemSettings.value == "true"
    ).first()
    org = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(system and org and org.mcp_modul_aktiv)


def load_live_context(db: Session, user_id: int, token_org_id: int, required_roles: tuple[str, ...]) -> MCPContext:
    """Laedt den Benutzer erneut und setzt erst danach den Tenant-Kontext."""
    user = db.query(User).execution_options(include_all_tenants=True).filter(User.id == user_id).first()
    if not user or not user.active:
        raise MCPPermissionError("Der Benutzer ist nicht mehr aktiv.")
    if user.is_device:
        raise MCPPermissionError("Geräte-Benutzer dürfen MCP nicht verwenden.")
    if not user.org_id or user.org_id != token_org_id:
        raise MCPPermissionError("Die Organisation dieses Zugangs ist nicht mehr gültig.")
    if not mcp_effective_enabled(user.org_id, db):
        raise MCPPermissionError("Das MCP-Modul ist für diese Organisation nicht aktiviert.")
    if required_roles and not has_role(user, *required_roles):
        raise MCPPermissionError("Für dieses Werkzeug fehlen die erforderlichen Berechtigungen.")
    set_tenant_context(db, user.org_id)
    return MCPContext(db=db, user=user, org_id=user.org_id)
