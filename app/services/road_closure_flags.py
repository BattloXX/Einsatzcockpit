"""Feature-Flag-Logik für Straßensperren und Anfahrtsrouting."""

from sqlalchemy.orm import Session


def strassensperren_system_enabled(db: Session) -> bool:
    from app.models.master import SystemSettings

    row = db.query(SystemSettings).filter(SystemSettings.key == "strassensperren_module_enabled").first()
    return row is not None and row.value == "true"


def strassensperren_effective_enabled(org_id: int | None, db: Session) -> bool:
    if org_id is None or not strassensperren_system_enabled(db):
        return False
    from app.models.master import OrgSettings

    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(settings and settings.strassensperren_modul_aktiv)
