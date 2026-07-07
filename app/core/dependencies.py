"""FastAPI-Dependencies für Tenant-Context-Auflösung.

CurrentOrgId: löst aus der Session (User.org_id) oder via ?org=N (nur system_admin)
den aktuellen Tenant auf und schreibt org_id in db.info["current_org_id"], damit der
TenantScoped-Listener automatisch filtert.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import get_db


def _set_uas_state(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt request.state.uas_module_enabled fail-safe (nie crashen)."""
    try:
        from app.services.uas_service import uas_effective_enabled
        request.state.uas_module_enabled = uas_effective_enabled(org_id, db)
    except Exception:
        request.state.uas_module_enabled = False


def _set_objekt_state(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt request.state.objekt_enabled fail-safe (nie crashen)."""
    try:
        from app.services.objekt_service import objekt_effective_enabled
        request.state.objekt_enabled = objekt_effective_enabled(org_id, db)
    except Exception:
        request.state.objekt_enabled = False


def _set_gateway_state(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt request.state.gateway_enabled fail-safe (nie crashen)."""
    try:
        from app.services.gateway_service import gateway_effective_enabled
        request.state.gateway_enabled = gateway_effective_enabled(org_id, db)
    except Exception:
        request.state.gateway_enabled = False


def _set_fahrtenbuch_state(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt request.state.fahrtenbuch_modul_aktiv fail-safe (nie crashen)."""
    try:
        from app.models.master import OrgSettings
        org_s = (
            db.query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .first()
            if org_id else None
        )
        request.state.fahrtenbuch_modul_aktiv = bool(org_s and org_s.fahrtenbuch_modul_aktiv)
    except Exception:
        request.state.fahrtenbuch_modul_aktiv = False


def _set_atemschutz_pruefung_state(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt request.state.atemschutz_pruefung_modul_aktiv fail-safe (nie crashen)."""
    try:
        from app.models.master import OrgSettings
        org_s = (
            db.query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .first()
            if org_id else None
        )
        request.state.atemschutz_pruefung_modul_aktiv = bool(org_s and org_s.atemschutz_pruefung_modul_aktiv)
    except Exception:
        request.state.atemschutz_pruefung_modul_aktiv = False


def _resolve_current_org(
    request: HTTPConnection,
    db: Session = Depends(get_db),
) -> int | None:
    """Bestimmt die aktive org_id für diesen Request und setzt den Tenant-Context.

    - Reguläre Nutzer: user.org_id
    - system_admin ohne ?org=: kein Filter (sieht alles)
    - system_admin mit ?org=N: impersoniert Org N (Audit-Eintrag)

    Setzt zusätzlich request.state.uas_module_enabled (True/False).
    """
    # Modul-Defaults: aus (wird unten ggf. überschrieben)
    request.state.uas_module_enabled = False
    request.state.objekt_enabled = False
    request.state.gateway_enabled = False
    request.state.fahrtenbuch_modul_aktiv = False
    request.state.atemschutz_pruefung_modul_aktiv = False

    user = getattr(request.state, "user", None)
    if user is None:
        # SEC-11: Anonyme Requests laufen UNGEFILTERT (org_id=None = kein
        # Tenant-Filter, identisch zum system_admin-Modus). Der Tenant-Listener
        # (app/core/tenant.py) bietet auf dieser Fläche KEINEN Schutz — jeder
        # unauthentifizierte Endpunkt (Public-Token-Routen, QR-Flows, API-Key-
        # Endpunkte ohne Session-Cookie) MUSS selbst scopen, z. B. über einen
        # expliziten .filter(...==token.org_id) oder eine andere Beweiskette
        # (Token/PIN/Signatur), bevor Daten zurückgegeben werden. Diese
        # Dependency selbst kann das nicht generisch erzwingen, da sie den
        # fachlichen Scoping-Mechanismus des jeweiligen Endpunkts nicht kennt.
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
            _set_uas_state(request, org_id, db)
            _set_objekt_state(request, org_id, db)
            _set_gateway_state(request, org_id, db)
            _set_fahrtenbuch_state(request, org_id, db)
            _set_atemschutz_pruefung_state(request, org_id, db)
            return org_id
        set_tenant_context(db, None)
        return None

    org_id = user.org_id
    set_tenant_context(db, org_id)
    _set_uas_state(request, org_id, db)
    _set_objekt_state(request, org_id, db)
    _set_gateway_state(request, org_id, db)
    _set_fahrtenbuch_state(request, org_id, db)
    _set_atemschutz_pruefung_state(request, org_id, db)
    return org_id


CurrentOrgId = Annotated[int | None, Depends(_resolve_current_org)]
