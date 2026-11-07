"""90 Tage Diagnosehistorie, ausschliesslich org-gefilterte Batches."""
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.infoscreen_agent import InfoscreenDeviceEvent


def purge_old_device_events(chunk_size: int = 500) -> int:
    if chunk_size <= 0:
        raise ValueError("chunk_size muss positiv sein")
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)
    total = 0
    with SessionLocal() as db:
        set_tenant_context(db, None)
        orgs = db.query(InfoscreenDeviceEvent.org_id).filter(
            InfoscreenDeviceEvent.ts < cutoff, InfoscreenDeviceEvent.org_id.is_not(None)).distinct().all()
        for (org_id,) in orgs:
            while True:
                rows = db.query(InfoscreenDeviceEvent).filter(
                    InfoscreenDeviceEvent.org_id == org_id, InfoscreenDeviceEvent.ts < cutoff,
                ).order_by(InfoscreenDeviceEvent.id).limit(chunk_size).all()
                if not rows:
                    break
                for row in rows:
                    db.delete(row)
                db.commit()
                total += len(rows)
    return total


def purge_old_overrides(chunk_size: int = 500) -> int:
    """Abgelaufene Uebersteuerungen nach 30 Tagen, getrennte Org-Batches."""
    from app.models.infoscreen_agent import InfoscreenOverride

    if chunk_size <= 0:
        raise ValueError("chunk_size muss positiv sein")
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    total = 0
    with SessionLocal() as db:
        set_tenant_context(db, None)
        orgs = db.query(InfoscreenOverride.org_id).filter(
            InfoscreenOverride.bis < cutoff, InfoscreenOverride.org_id.is_not(None)).distinct().all()
        for (org_id,) in orgs:
            while True:
                rows = db.query(InfoscreenOverride).filter(
                    InfoscreenOverride.org_id == org_id, InfoscreenOverride.bis < cutoff,
                ).order_by(InfoscreenOverride.id).limit(chunk_size).all()
                if not rows:
                    break
                for row in rows:
                    db.delete(row)
                db.commit()
                total += len(rows)
    return total
