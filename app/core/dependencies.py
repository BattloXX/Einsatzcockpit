"""FastAPI-Dependencies für Tenant-Context-Auflösung.

CurrentOrgId: löst aus der Session (User.org_id) oder via ?org=N (nur system_admin)
den aktuellen Tenant auf und schreibt org_id in db.info["current_org_id"], damit der
TenantScoped-Listener automatisch filtert.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import get_db


def _resolve_current_org(
    request: Request,
    db: Session = Depends(get_db),
) -> int | None:
    """Bestimmt die aktive org_id für diesen Request und setzt den Tenant-Context.

    - Reguläre Nutzer: user.org_id
    - system_admin ohne ?org=: kein Filter (sieht alles)
    - system_admin mit ?org=N: impersoniert Org N (Audit-Eintrag)
    """
    user = getattr(request.state, "user", None)
    if user is None:
        set_tenant_context(db, None)
        return None

    if user.is_system_admin:
        org_param = request.query_params.get("org")
        if org_param:
            try:
                org_id = int(org_param)
            except ValueError:
                raise HTTPException(400, "Ungültiger org-Parameter")
            write_audit(
                db,
                "system_admin.impersonate_org",
                user_id=user.id,
                org_id=org_id,
                payload={"acting_as_org": org_id},
                ip=request.client.host if request.client else None,
            )
            set_tenant_context(db, org_id)
            return org_id
        set_tenant_context(db, None)
        return None

    org_id = user.org_id
    set_tenant_context(db, org_id)
    return org_id


CurrentOrgId = Annotated[int | None, Depends(_resolve_current_org)]
