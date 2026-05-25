from collections.abc import Callable

from fastapi import HTTPException, Request

# Hierarchy: system_admin > admin/org_admin > incident_leader > breathing_supervisor > recorder > readonly
ROLES = {
    "system_admin": 200,   # cross-org, full system access
    "admin": 100,          # backward-compat alias for org_admin
    "org_admin": 100,      # full access within their organisation
    "incident_leader": 70,
    "breathing_supervisor": 50,
    "recorder": 30,
    "readonly": 10,
}

# Roles that bypass all org-scoping checks
SUPERADMIN_ROLES = {"system_admin"}

# Roles that grant full access within an org
ORG_ADMIN_ROLES = {"system_admin", "admin", "org_admin"}


def require_role(*roles: str) -> Callable:
    """FastAPI dependency that enforces role membership.
    system_admin always passes regardless of requested roles.
    """
    def dependency(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        user_roles = {r.code for r in user.roles}
        # system_admin bypasses all role checks
        if "system_admin" in user_roles:
            return user
        if not user_roles.intersection(set(roles) | {"admin", "org_admin"}):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user
    return dependency


def require_system_admin(request: Request):
    """Dependency: only system_admin can access this endpoint."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    user_roles = {r.code for r in user.roles}
    if "system_admin" not in user_roles:
        raise HTTPException(status_code=403, detail="Nur Systemadministratoren haben Zugriff")
    return user


def has_role(user, *roles: str) -> bool:
    if user is None:
        return False
    user_roles = {r.code for r in user.roles}
    if "system_admin" in user_roles:
        return True
    return bool(user_roles.intersection(set(roles) | {"admin", "org_admin"}))


def can_access_incident(user, incident) -> bool:
    """Check if user can access an incident (own org or collaborating org)."""
    if user is None:
        return False
    user_roles = {r.code for r in user.roles}
    if "system_admin" in user_roles:
        return True
    if user.org_id is None:
        return False
    # Primary org
    if incident.primary_org_id == user.org_id:
        return True
    # Collaborating org
    collab_org_ids = {io.org_id for io in (incident.collaborating_orgs or [])}
    return user.org_id in collab_org_ids


def same_org_or_system_admin(user, target_org_id: int) -> bool:
    """True if user belongs to target_org_id OR is system_admin."""
    if user is None:
        return False
    if "system_admin" in {r.code for r in user.roles}:
        return True
    return user.org_id == target_org_id
