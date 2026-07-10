"""Lageführung-Modul-Service: Feature-Flag-Logik.

Effektive Aktivierung: System-Flag (SystemSettings key "lagefuehrung_modul_aktiv" == "true")
UND Org-Flag (OrgSettings.lagefuehrung_modul_aktiv == True).
"""
from __future__ import annotations

from sqlalchemy.orm import Session


def lagefuehrung_system_enabled(db: Session) -> bool:
    """Systemweiter Lageführung-Flag aus SystemSettings. Fehlender Key → False."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "lagefuehrung_modul_aktiv").first()
    return row is not None and row.value == "true"


def lagefuehrung_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Lageführung effektiv aktiv ⟺ System-Flag AN und Org-Flag AN.

    Gibt False wenn org_id None (system_admin ohne Impersonation).
    """
    if org_id is None:
        return False
    if not lagefuehrung_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(org_s and org_s.lagefuehrung_modul_aktiv)
