"""Push an das Fahrzeug-Tablet einer GSL-Einheit bei Auftragsereignissen."""
from __future__ import annotations

import logging

logger = logging.getLogger("einsatzleiter.gsl_einheit_push")

_TITEL = {
    "neu": "Neuer Einsatzauftrag",
    "geaendert": "Auftrag geändert",
    "zurueckgezogen": "Auftrag zurückgezogen",
}


def push_einheit_auftrag(einheit_id: int, dispatch_id: int, ereignis: str) -> int:
    """Best-effort-Push nach dem Commit; eine Zustellung ist keine Lesebestätigung (Funk bleibt verbindlich)."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.major_incident import EinheitSiteDispatch, LageEinheit, MajorIncident
    from app.models.user import DeviceToken
    from app.services.exercise_guard import darf_extern
    from app.services.push_service import notify_vehicle

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = db.get(LageEinheit, einheit_id)
        dispatch = db.get(EinheitSiteDispatch, dispatch_id)
        if not einheit or not dispatch or dispatch.einheit_id != einheit.id or not einheit.vehicle_id:
            return 0
        lage = db.get(MajorIncident, einheit.lage_id)
        if not lage or lage.org_id is None or lage.status != "active":
            return 0
        set_tenant_context(db, lage.org_id)
        if not darf_extern("push", is_exercise=bool(lage.is_exercise), org_id=lage.org_id, db=db):
            return 0
        hat_tablet = db.query(DeviceToken.id).filter(
            DeviceToken.vehicle_master_id == einheit.vehicle_id,
            DeviceToken.revoked_at.is_(None),
            DeviceToken.gsl_profil == "einheit",
        ).first()
        if not hat_tablet:
            return 0
        site = dispatch.site
        body = (site.bezeichnung if site else "") or einheit.label
        if ereignis != "zurueckgezogen" and dispatch.auftrag:
            body = f"{body}: {dispatch.auftrag}"[:160]
        anzahl = notify_vehicle(db, einheit.vehicle_id, _TITEL.get(ereignis, "Auftrag"), body,
                                f"/einheit/auftrag/{dispatch.id}")
        db.commit()
        return anzahl
    except Exception:
        db.rollback()
        logger.warning("Einheiten-Push fehlgeschlagen (einheit=%s, dispatch=%s)", einheit_id, dispatch_id)
        return 0
    finally:
        db.close()
