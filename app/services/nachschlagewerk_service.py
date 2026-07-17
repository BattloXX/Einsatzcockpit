"""Nachschlagewerke-Service: Feature-Flag-Helfer.

Effektive Aktivierung: System-Flag (SystemSettings key
"nachschlagewerke_module_enabled" == "true") UND Org-Flag
(OrgSettings.nachschlagewerke_module_enabled == True) — Muster Objekt/UAS-Modul.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

_SYS_KEY = "nachschlagewerke_module_enabled"


def nachschlagewerke_system_enabled(db: Session) -> bool:
    """Systemweiter Nachschlagewerke-Flag aus SystemSettings. Fehlender Key -> False."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == _SYS_KEY).first()
    return row is not None and row.value == "true"


def nachschlagewerke_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Nachschlagewerke effektiv aktiv <=> System-Flag AN und Org-Flag AN.

    Gibt False wenn org_id None (system_admin ohne Impersonation).
    """
    if org_id is None:
        return False
    if not nachschlagewerke_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    org_s = (
        db.query(OrgSettings)
        .filter(OrgSettings.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return bool(org_s and org_s.nachschlagewerke_module_enabled)
