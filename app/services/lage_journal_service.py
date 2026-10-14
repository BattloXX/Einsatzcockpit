"""Geschäftslogik für Einträge im Lagejournal."""
from sqlalchemy.orm import Session

from app.core.html_utils import sanitize_html
from app.models.major_incident import LageEinheit, LageJournalEntry, MajorIncident


def journal_eintrag_anlegen(
    db: Session, lage: MajorIncident, *, text: str, category: str, body_html: str,
    partner_from: str, partner_to: str, measure: str, user_id: int | None,
    author_name: str | None, site_id: int | None = None, einheit_id: int | None = None,
) -> LageJournalEntry:
    """Erstellt einen Journal-Eintrag; Commit und Medien bleiben beim Aufrufer."""
    if site_id is not None:
        from app.models.major_incident import IncidentSite
        site = db.get(IncidentSite, site_id)
        if not site or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle nicht gefunden.")
    if einheit_id is not None:
        einheit = db.get(LageEinheit, einheit_id)
        if not einheit or einheit.lage_id != lage.id:
            raise ValueError("Einheit nicht gefunden.")
    entry = LageJournalEntry(
        major_incident_id=lage.id, category=category, text=text.strip()[:2000],
        body_html=sanitize_html(body_html), partner_from=partner_from.strip()[:120] or None,
        partner_to=partner_to.strip()[:120] or None, measure=measure.strip()[:500] or None,
        author_name=author_name, user_id=user_id, site_id=site_id, einheit_id=einheit_id,
    )
    db.add(entry)
    return entry
