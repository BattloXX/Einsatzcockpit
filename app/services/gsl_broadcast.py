"""Gemeinsame Live-Broadcasts für GSL-Einsatzstellen."""

from app.models.major_incident import IncidentSite
from app.services.broadcast import broadcast_lage
from app.services.gsl_live_notify import notify_gsl_live


async def stelle_angelegt(db, lage, site_id: int) -> None:
    await broadcast_lage(lage.id, {"type": "site_created", "site_id": site_id})
    await notify_gsl_live(db, lage, org_id=lage.org_id, reason="counts")


async def stelle_geaendert(lage_id: int, site_id: int) -> None:
    await broadcast_lage(lage_id, {"type": "site_updated", "site_id": site_id})


async def prio_geaendert(lage_id: int, site_id: int) -> None:
    await broadcast_lage(lage_id, {"type": "site_prio_changed", "site_id": site_id})


async def abschnitt_geaendert(lage_id: int, site_id: int, sector_id: int | None) -> None:
    await broadcast_lage(lage_id, {"type": "site:sector_changed", "site_id": site_id, "sector_id": sector_id})


async def phase_geaendert(db, lage, site_id: int, phase) -> None:
    await broadcast_lage(lage.id, {"type": "site_phase_changed", "site_id": site_id, "phase": phase.value})
    await notify_gsl_live(db, lage, org_id=lage.org_id, reason="counts")


async def einheit_status_geaendert(db, lage, site_id: int, dispatch_id: int, einheit_id: int, result: dict) -> None:
    await broadcast_lage(lage.id, {"type": "site:card_changed", "site_id": site_id})
    await broadcast_lage(lage.id, {"type": "einheit:changed", "einheit_id": einheit_id,
                                   "dispatch_id": dispatch_id, "site_id": site_id})
    if result["phase_geaendert"]:
        await phase_geaendert(db, lage, site_id, db.get(IncidentSite, site_id).phase)
    if result["unterbrochen_dispatch_id"]:
        from app.services import resource_service
        interrupted = db.get(resource_service.EinheitSiteDispatch, result["unterbrochen_dispatch_id"])
        if interrupted:
            await broadcast_lage(lage.id, {"type": "site:card_changed", "site_id": interrupted.site_id})
            await broadcast_lage(lage.id, {"type": "einheit:changed", "einheit_id": interrupted.einheit_id,
                                           "site_id": interrupted.site_id})


async def site_log_geaendert(lage_id: int, site_id: int, funkjournal_geaendert: bool = False) -> None:
    """Informiert Board-Clients über einen neuen Einsatzstellen-Chronikeintrag."""
    await broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": site_id})
    if funkjournal_geaendert:
        await broadcast_lage(lage_id, {"type": "funkjournal:changed"})


async def journal_geaendert(lage_id: int) -> None:
    await broadcast_lage(lage_id, {"type": "journal_updated"})


async def funkjournal_geaendert(lage_id: int, site_id: int | None = None) -> None:
    await broadcast_lage(lage_id, {"type": "funkjournal:changed"})
    if site_id:
        await broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": site_id})


async def cross_marker_geaendert(lage_id: int, marker_id: int, deleted: bool = False) -> None:
    event = {"type": "cross_marker:changed", "marker_id": marker_id}
    if deleted:
        event["deleted"] = True
    await broadcast_lage(lage_id, event)
