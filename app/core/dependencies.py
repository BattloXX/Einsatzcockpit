"""FastAPI-Dependencies für Tenant-Context-Auflösung.

CurrentOrgId: löst aus der Session (User.org_id) oder via ?org=N (nur system_admin)
den aktuellen Tenant auf und schreibt org_id in db.info["current_org_id"], damit der
TenantScoped-Listener automatisch filtert.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.core.audit import write_audit
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.user import ApiKey


def get_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Authentifiziert einen aktiven API-Key, ohne bestehende Routen einzuschränken."""
    key_hash = hash_api_key(x_api_key)
    api_key = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not api_key or not api_key.is_active:
        raise HTTPException(status_code=401, detail="Ungültiger oder gesperrter API-Key")
    api_key.last_used_at = datetime.now(UTC)
    return api_key


def require_scope(*scopes: str):
    """Dependency-Factory für API-Key-Scopes mit gesetztem Tenant-Kontext."""
    def dependency(
        api_key: ApiKey = Depends(get_api_key),
        db: Session = Depends(get_db),
    ) -> ApiKey:
        if not any(api_key.has_scope(scope) for scope in scopes):
            raise HTTPException(status_code=403, detail="API-Key hat nicht den nötigen Scope")
        set_tenant_context(db, api_key.org_id)
        return api_key

    return dependency

# System-Flag-Key in SystemSettings → request.state-Attribut. Die Semantik je
# Modul ist identisch zu den *_effective_enabled-Helfern der Services
# (effektiv = System-Flag "true" AND Org-Flag) — hier nur gebündelt, damit pro
# Request 2 Queries statt bis zu 10 anfallen (Audit B4).
_SYSTEM_FLAG_KEYS = ("uas_module_enabled", "objekt_module_enabled",
                     "gateway_module_enabled", "lagefuehrung_modul_aktiv",
                     "nachschlagewerke_module_enabled", "foerderstrecke_module_enabled",
                     "mailing_module_enabled", "probenplanung_module_enabled")


def _set_module_states(request: HTTPConnection, org_id: int | None, db: Session) -> None:
    """Setzt alle Modul-Flags auf request.state fail-safe (nie crashen).

    Bündelt die früheren sechs _set_*_state-Helfer: EIN OrgSettings-Load plus
    EIN SystemSettings-Load (key IN (...)) statt je Modul eigener Queries.
    Bei Fehlern bleiben die in _resolve_current_org gesetzten Defaults (False).
    """
    if org_id is None:
        return
    try:
        from app.models.master import OrgSettings, SystemSettings
        org_s = (
            db.query(OrgSettings)
            .filter(OrgSettings.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .first()
        )
        rows = db.query(SystemSettings).filter(SystemSettings.key.in_(_SYSTEM_FLAG_KEYS)).all()
        sys_on = {r.key for r in rows if r.value == "true"}

        request.state.uas_module_enabled = bool(
            "uas_module_enabled" in sys_on and org_s and org_s.uas_module_enabled)
        request.state.objekt_enabled = bool(
            "objekt_module_enabled" in sys_on and org_s and org_s.objekt_module_enabled)
        request.state.nachschlagewerke_enabled = bool(
            "nachschlagewerke_module_enabled" in sys_on
            and org_s and org_s.nachschlagewerke_module_enabled)
        request.state.gateway_enabled = bool(
            "gateway_module_enabled" in sys_on and org_s and org_s.gateway_module_enabled)
        request.state.lagefuehrung_modul_aktiv = bool(
            "lagefuehrung_modul_aktiv" in sys_on and org_s and org_s.lagefuehrung_modul_aktiv)
        request.state.foerderstrecke_enabled = bool(
            "foerderstrecke_module_enabled" in sys_on
            and org_s and org_s.foerderstrecke_module_enabled)
        request.state.mailing_module_enabled = bool(
            "mailing_module_enabled" in sys_on and org_s and org_s.mailing_module_enabled)
        request.state.probenplanung_enabled = bool(
            "probenplanung_module_enabled" in sys_on
            and org_s and org_s.probenplanung_modul_aktiv)
        # Atemschutz fehlt bewusst in der Bulk-Abfrage: fehlender System-Key
        # bedeutet hier im Gegensatz zu allen obigen Modulen "aktiv".
        from app.services.breathing_service import breathing_effective_enabled
        request.state.breathing_module_enabled = breathing_effective_enabled(org_id, db)
        # Rein org-gesteuerte Module (kein System-Flag):
        request.state.fahrtenbuch_modul_aktiv = bool(org_s and org_s.fahrtenbuch_modul_aktiv)
        request.state.atemschutz_pruefung_modul_aktiv = bool(
            org_s and org_s.atemschutz_pruefung_modul_aktiv)
    except Exception:
        pass


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
    request.state.nachschlagewerke_enabled = False
    request.state.gateway_enabled = False
    request.state.fahrtenbuch_modul_aktiv = False
    request.state.atemschutz_pruefung_modul_aktiv = False
    request.state.lagefuehrung_modul_aktiv = False
    request.state.foerderstrecke_enabled = False
    request.state.mailing_module_enabled = False
    request.state.probenplanung_enabled = False
    request.state.breathing_module_enabled = True

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
            _set_module_states(request, org_id, db)
            return org_id
        set_tenant_context(db, None)
        return None

    org_id = user.org_id
    set_tenant_context(db, org_id)
    _set_module_states(request, org_id, db)
    return org_id


CurrentOrgId = Annotated[int | None, Depends(_resolve_current_org)]
