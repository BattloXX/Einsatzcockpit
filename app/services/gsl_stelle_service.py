"""Geschäftslogik für Einsatzstellen der Großschadenslage."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.major_incident import IncidentSite, MajorIncident, Sector, SiteLogEntry, SitePhase, SitePriority
from app.services import lagemeldung_service, resource_service
from app.services.major_incident_service import create_site


def stelle_simuliert_anlegen(
    db: Session, lage: MajorIncident, *, bezeichnung: str, einsatzgrund: str | None,
    ort: str | None, strasse: str | None, hausnr: str | None, lat: float | None,
    lng: float | None, alarm_stufe: str | None, user_id: int | None, author_name: str | None,
) -> IncidentSite:
    """Legt eine Planspiel-Stelle ohne Geocoding, KI oder externe Wirkung an."""
    site = create_site(
        db, lage, bezeichnung=bezeichnung, einsatzgrund=(einsatzgrund or "")[:160], ort=ort,
        strasse=strasse, hausnr=hausnr, lat=lat, lng=lng, source="simulation", alarm_stufe=alarm_stufe,
        created_by=user_id,
    )
    db.add(SiteLogEntry(incident_site_id=site.id, kind="status", text="Einsatzstelle angelegt (Planspiel)",
                        user_id=user_id, author_name=author_name))
    write_audit(db, "major_incident.site.created", user_id=user_id,
                payload={"lage_id": lage.id, "site_id": site.id, "bezeichnung": site.bezeichnung,
                         "quelle": "planspiel"})
    from app.services.geo_service import auto_assign_section
    auto_assign_section(db, site)
    return site


async def _apply_ai_prio(site: IncidentSite, db: Session, org_id: int | None = None) -> None:
    """Schlägt eine Priorität per KI vor, ohne jemals einen Fehler weiterzugeben."""
    from app.services.ai_service import is_enabled

    text = site.einsatzgrund or site.bezeichnung
    if site.priority or not is_enabled() or not text:
        return
    try:
        from app.services.ai_service import analyze_site_reconnaissance
        result = await analyze_site_reconnaissance(
            text,
            {"bezeichnung": site.bezeichnung, "ort": site.ort or "", "strasse": site.strasse or ""},
            org_id=org_id,
        )
        if result and result.get("prio_vorschlag"):
            site.priority = SitePriority(result["prio_vorschlag"])
            site.danger_score = result.get("danger_score")
            site.urgency_score = result.get("urgency_score")
    except Exception:
        pass


async def _geocode_site(site: IncidentSite) -> None:
    """Ergänzt Koordinaten aus der Adresse und ignoriert Geocoding-Fehler."""
    if site.lat and site.lng:
        return
    if not (site.strasse or site.ort):
        return
    try:
        from app.services.geocoding import geocode_address
        geo = await geocode_address(site.strasse, site.hausnr, site.ort)
        if geo:
            site.lat, site.lng = geo.lat, geo.lng
    except Exception:
        pass


async def stelle_anlegen(
    db: Session, lage: MajorIncident, *, bezeichnung: str, einsatzgrund: str | None = None,
    ort: str | None = None, strasse: str | None = None, hausnr: str | None = None,
    lat: float | None = None, lng: float | None = None, user_id: int, author_name: str | None,
) -> IncidentSite:
    """Legt eine Einsatzstelle an, ohne zu committen oder zu senden."""
    site = create_site(db, lage, bezeichnung=bezeichnung, einsatzgrund=einsatzgrund, ort=ort,
                       strasse=strasse, hausnr=hausnr, lat=lat, lng=lng, created_by=user_id)
    await _geocode_site(site)
    await _apply_ai_prio(site, db, org_id=lage.org_id)
    db.add(SiteLogEntry(incident_site_id=site.id, kind="status", text="Einsatzstelle angelegt",
                        user_id=user_id, author_name=author_name))
    write_audit(db, "major_incident.site.created", user_id=user_id,
                payload={"lage_id": lage.id, "site_id": site.id, "bezeichnung": site.bezeichnung})
    from app.services.geo_service import auto_assign_section
    auto_assign_section(db, site)
    return site


def _adresse_normalisiert(wert: str | None) -> str:
    return "".join((wert or "").casefold().split())


def finde_adress_duplikate(
    db: Session, lage: MajorIncident, *, ort: str | None, strasse: str | None,
    hausnr: str | None, ausser_site_id: int | None = None,
) -> list[IncidentSite]:
    """Findet offene Stellen derselben Lage mit derselben normalisierten Adresse."""
    if not _adresse_normalisiert(strasse):
        return []
    query = db.query(IncidentSite).filter(
        IncidentSite.major_incident_id == lage.id,
        IncidentSite.phase.notin_((SitePhase.erledigt, SitePhase.abgebrochen)),
    )
    if ausser_site_id is not None:
        query = query.filter(IncidentSite.id != ausser_site_id)
    adresse = (_adresse_normalisiert(ort), _adresse_normalisiert(strasse), _adresse_normalisiert(hausnr))
    return [site for site in query.all() if (
        _adresse_normalisiert(site.ort), _adresse_normalisiert(site.strasse), _adresse_normalisiert(site.hausnr),
    ) == adresse]


async def stelle_bearbeiten(
    db: Session, site: IncidentSite, *, bezeichnung: str, einsatzgrund: str | None,
    ort: str | None, strasse: str | None, hausnr: str | None, user_id: int, author_name: str | None,
) -> None:
    """Übernimmt die bearbeitbaren Felder einer Einsatzstelle ohne Commit."""
    old_bezeichnung = site.bezeichnung
    site.bezeichnung = bezeichnung.strip()
    site.einsatzgrund = einsatzgrund.strip() if einsatzgrund and einsatzgrund.strip() else None
    site.ort = ort.strip() if ort and ort.strip() else None
    site.strasse = strasse.strip() if strasse and strasse.strip() else None
    site.hausnr = hausnr.strip() if hausnr and hausnr.strip() else None
    if site.bezeichnung != old_bezeichnung:
        db.add(SiteLogEntry(incident_site_id=site.id, kind="status",
                             text=f"Bezeichnung geändert: {old_bezeichnung} → {site.bezeichnung}",
                             user_id=user_id, author_name=author_name))
    await _geocode_site(site)


def stelle_prio_setzen(
    db: Session, site: IncidentSite, prio: SitePriority | None, *, user_id: int, author_name: str | None,
) -> bool:
    """Setzt die Priorität, protokolliert Änderungen und aktualisiert die Lagemeldung."""
    old_prio = site.priority
    site.priority = prio
    if old_prio != prio:
        from app.models.major_incident import SITE_PRIORITY_LABEL
        old_label = SITE_PRIORITY_LABEL.get(old_prio, "–") if old_prio else "–"
        new_label = SITE_PRIORITY_LABEL.get(prio, "–") if prio else "–"
        db.add(SiteLogEntry(incident_site_id=site.id, kind="prio", text=f"Priorität: {old_label} → {new_label}",
                            user_id=user_id, author_name=author_name))
    lagemeldung_service.recompute_if_active(site, db)
    return old_prio != prio


def stelle_abschnitt_setzen(db: Session, site: IncidentSite, sector_id: int | None) -> None:
    """Weist einen Abschnitt derselben Lage zu und synchronisiert Einheiten."""
    if sector_id is not None:
        sector = db.get(Sector, sector_id)
        if sector is None or sector.major_incident_id != site.major_incident_id:
            raise ValueError("Abschnitt nicht gefunden.")
    site.sector_id = sector_id
    site.section_assigned_mode = "manual"
    resource_service.sync_units_sector_to_site(db, site)
