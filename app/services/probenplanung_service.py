"""Feature-Flag-Logik der Probenplanung."""
from sqlalchemy.orm import Session


def probenplanung_system_enabled(db: Session) -> bool:
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == "probenplanung_module_enabled").first()
    return row is not None and row.value == "true"


def probenplanung_effective_enabled(org_id: int | None, db: Session) -> bool:
    if org_id is None or not probenplanung_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(settings and settings.probenplanung_modul_aktiv)
