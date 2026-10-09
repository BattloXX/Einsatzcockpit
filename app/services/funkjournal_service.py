"""Geschäftslogik für das Funkjournal der Großschadenslage."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.major_incident import CommLogEntry, IncidentSite, MajorIncident


def add_comm_entry(
    db: Session,
    lage: MajorIncident,
    *,
    direction: str,
    message: str,
    user_id: int | None,
    author_name: str | None,
    channel: str | None = None,
    partner: str | None = None,
    is_request: bool = False,
    related_site_id: int | None = None,
) -> CommLogEntry:
    """Erfasst einen Funkjournaleintrag und spiegelt ihn bei Bedarf in die Chronik."""
    if direction not in ("in", "out", "int"):
        raise ValueError("Ungültige Richtung")

    site = None
    if related_site_id is not None:
        site = db.get(IncidentSite, related_site_id)
        if not site or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle gehört nicht zur Lage")

    entry = CommLogEntry(
        major_incident_id=lage.id,
        direction=direction,
        channel=(channel or "").strip() or None,
        partner=(partner or "").strip() or None,
        message=message.strip(),
        is_request=is_request,
        related_site_id=related_site_id,
        user_id=user_id,
        author_name=author_name,
    )
    db.add(entry)
    if site:
        dir_label = {"in": "↓ Eingehend", "out": "↑ Ausgehend", "int": "↔ Intern"}[direction]
        teile = [f"Funkjournal ({dir_label})"]
        if entry.channel:
            teile.append(f"Kanal: {entry.channel}")
        if entry.partner:
            teile.append(f"Von/An: {entry.partner}")
        teile.append(entry.message)
        from app.services.site_log_service import add_site_log
        add_site_log(db, site, "note", " – ".join(teile), user_id=user_id, author_name=author_name)
    return entry


def toggle_handled(db: Session, lage: MajorIncident, entry_id: int) -> CommLogEntry:
    """Schaltet den Erledigt-Status eines Eintrags derselben Lage um."""
    entry = db.get(CommLogEntry, entry_id)
    if not entry or entry.major_incident_id != lage.id:
        raise LookupError("Funkjournaleintrag nicht gefunden")
    entry.handled = not entry.handled
    return entry
