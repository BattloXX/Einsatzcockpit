"""Hinweise für die mobile Führung einer Großschadenslage."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models.major_incident import SITE_PRIORITY_LABEL, EinheitSiteDispatch, SitePhase, SitePriority
from app.services import lagemeldung_service, resource_service


def fuehrungs_hinweise(db: Session, lage: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Liefert tenant-sichere, nach Dringlichkeit sortierte Hinweise je Einsatzstelle."""
    current = now or datetime.now(UTC).replace(tzinfo=None)
    if current.tzinfo is not None:
        current = current.astimezone(UTC).replace(tzinfo=None)
    settings = lagemeldung_service.org_settings_for(db, lage.org_id)
    sites = [site for site in lage.sites if site.phase not in (SitePhase.erledigt, SitePhase.abgebrochen)]
    site_ids = [site.id for site in sites]
    dispatches = (
        db.query(EinheitSiteDispatch)
        .options(joinedload(EinheitSiteDispatch.einheit))
        .filter(EinheitSiteDispatch.site_id.in_(site_ids), EinheitSiteDispatch.withdrawn_at.is_(None))
        .all() if site_ids else []
    )
    by_site: dict[int, list[EinheitSiteDispatch]] = {site_id: [] for site_id in site_ids}
    for dispatch in dispatches:
        by_site.setdefault(dispatch.site_id, []).append(dispatch)

    result = []
    for site in sites:
        reasons: list[dict[str, str]] = []
        active = [dispatch for dispatch in by_site[site.id] if dispatch.beendet_at is None]
        if site.priority in (SitePriority.sofort, SitePriority.dringend):
            reasons.append({"code": "dringend", "text": "Priorität " + SITE_PRIORITY_LABEL[site.priority]})
        if site.naechste_lagemeldung_at and site.naechste_lagemeldung_at <= current:
            minutes = int((current - site.naechste_lagemeldung_at).total_seconds() // 60)
            reasons.append({"code": "lagemeldung_ueberfaellig", "text": f"Lagemeldung seit {minutes} Min. überfällig"})
        interval = lagemeldung_service.interval_minutes_for(site, settings) or 10
        for dispatch in active:
            if dispatch.einheit_status != "zugewiesen" or dispatch.bestaetigt_at is not None:
                continue
            if not dispatch.dispatched_at:
                continue
            minutes = int((current - dispatch.dispatched_at).total_seconds() // 60)
            if minutes > interval:
                label = dispatch.einheit.label if dispatch.einheit else "Einheit"
                reasons.append({"code": "unbestaetigt", "text": f"{label} seit {minutes} Min. nicht bestätigt"})
        no_report = resource_service.ohne_rueckmeldung_min_for_dispatches(
            site, active, org_settings=settings, jetzt=current
        )
        if no_report is not None:
            reasons.append({"code": "keine_rueckmeldung", "text": f"Keine Rückmeldung seit {no_report} Min."})
        if site.phase == SitePhase.fertig_gemeldet:
            reasons.append({"code": "wartet_auf_abschluss", "text": "Fertig gemeldet – Abschluss ausständig"})
        if reasons:
            result.append({"site_id": site.id, "bezeichnung": site.bezeichnung, "phase": site.phase.value,
                           "priority": site.priority.value if site.priority else None, "gruende": reasons,
                           "_oldest": min((d.dispatched_at for d in active if d.dispatched_at), default=datetime.max)})
    priority_order = {
        SitePriority.sofort.value: 0,
        SitePriority.dringend.value: 1,
        SitePriority.normal.value: 2,
        SitePriority.aufschiebbar.value: 3,
        None: 4,
    }
    result.sort(key=lambda item: (
        0 if any(reason["code"] == "dringend" for reason in item["gruende"]) else 1,
        priority_order.get(item["priority"], 4), item["_oldest"]
    ))
    for item in result:
        item.pop("_oldest")
    return result
