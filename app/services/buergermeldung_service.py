"""Übernahme und Bearbeitung von Bürgermeldungen."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.major_incident import CitizenReport, MajorIncident, SiteLogEntry
from app.services import cross_marker_service, gsl_stelle_service
from app.services.major_incident_service import create_site


def report(db: Session, lage: MajorIncident, report_id: int) -> CitizenReport:
    value = db.get(CitizenReport, report_id)
    if not value or value.major_incident_id != lage.id:
        raise LookupError("Bürgermeldung nicht gefunden.")
    return value


async def annehmen(
    db: Session, lage: MajorIncident, report_id: int, *, user_id: int,
    author_name: str | None, bezeichnung: str | None = None, site_id: int | None = None,
    nur_neu: bool = True,
):
    value = report(db, lage, report_id)
    if nur_neu and value.status != "new":
        raise ValueError("Bürgermeldung wurde bereits bearbeitet.")
    if site_id is not None:
        from app.models.major_incident import IncidentSite
        site = db.get(IncidentSite, site_id)
        if not site or site.major_incident_id != lage.id:
            raise LookupError("Einsatzstelle nicht gefunden.")
    else:
        title = bezeichnung or f"Bürgermeldung – {value.ort or value.strasse or 'unbekannt'}"
        if bezeichnung is None and value.description:
            try:
                from app.services.ai_service import generate_site_bezeichnung, is_enabled
                if is_enabled():
                    title = await generate_site_bezeichnung(value.description, org_id=lage.org_id) or title
            except Exception:
                pass
        site = create_site(
            db, lage, bezeichnung=title, einsatzgrund=(value.description or "")[:160] or None,
            ort=value.ort, strasse=value.strasse, lat=value.lat, lng=value.lng,
            created_by=user_id, source="buerger",
        )
        await gsl_stelle_service._geocode_site(site)
        await gsl_stelle_service._apply_ai_prio(site, db, org_id=lage.org_id)
        from app.services.geo_service import auto_assign_section
        auto_assign_section(db, site)
    status_text = (f"Bürgermeldung #{value.id} zugeordnet" if site_id is not None
                   else f"Aus Bürgermeldung #{value.id} erstellt")
    db.add(SiteLogEntry(incident_site_id=site.id, kind="status", text=status_text,
                         user_id=user_id, author_name=author_name))
    # Name/Telefon aus der Bürgermeldung als Notiz übernehmen (MCP blendet sie aus)
    contact_parts = []
    if value.reporter_name:
        contact_parts.append(f"Anrufer: {value.reporter_name.strip()}")
    if value.reporter_contact:
        contact_parts.append(f"Tel.: {value.reporter_contact.strip()}")
    if contact_parts:
        db.add(SiteLogEntry(incident_site_id=site.id, kind="note", text=" · ".join(contact_parts),
                             user_id=user_id, author_name=author_name))
    if value.photo_filename:
        from app.services.lage_media_service import copy_citizen_photo_to_site
        media = copy_citizen_photo_to_site(Path("app_storage/citizen_media") / value.photo_filename, site.id,
            org_id=lage.org_id, user_id=user_id, author_name=author_name, db=db)
        if media:
            db.add(media)
    value.status, value.site_id = "accepted", site.id
    return value, site


def ablehnen(db: Session, lage: MajorIncident, report_id: int, *, nur_neu: bool = False) -> CitizenReport:
    value = report(db, lage, report_id)
    if nur_neu and value.status != "new":
        raise ValueError("Bürgermeldung wurde bereits bearbeitet.")
    value.status = "rejected"
    return value


def als_lageinfo(db: Session, lage: MajorIncident, report_id: int, *, user_id: int | None,
                 author_name: str | None, nur_neu: bool = False):
    value = report(db, lage, report_id)
    if nur_neu and value.status != "new":
        raise ValueError("Bürgermeldung wurde bereits bearbeitet.")
    marker = cross_marker_service.anlegen(
        db, lage, title=f"Bürgermeldung – {value.ort or value.strasse or 'unbekannt'}",
        marker_type="sonstiges", status="unbestaetigt", description=value.description or "",
        strasse=value.strasse or "", ort=value.ort or "", lat=value.lat, lng=value.lng,
        user_id=user_id, author_name=author_name, source="buerger",
    )
    value.status = "accepted"
    return value, marker
