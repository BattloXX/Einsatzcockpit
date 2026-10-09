"""Geschäftslogik für Chronik-Einträge an Einsatzstellen."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.major_incident import SITE_LOG_RESET_KINDS, SITE_LOG_USER_KINDS, IncidentSite, SiteLogEntry


def normalisiere_user_kind(art: str) -> str:
    """Gibt einen vom Benutzer auswählbaren Chronik-Typ zurück."""
    return art if art in SITE_LOG_USER_KINDS else "note"


def format_lagemeldung(felder: dict[str, str | None]) -> str:
    """Formatiert die optionalen Bestandteile einer strukturierten Lagemeldung."""
    abschnitte = (
        ("lage", "Lage vor Ort"),
        ("gefahren", "Festgestellte Gefahren"),
        ("massnahmen", "Durchgeführte Maßnahmen"),
        ("fortschritt", "Aktueller Fortschritt"),
    )
    zeilen = [f"{ueberschrift}: {inhalt.strip()}" for feld, ueberschrift in abschnitte
              if (inhalt := felder.get(feld)) and inhalt.strip()]
    if freitext := felder.get("freitext"):
        if freitext.strip():
            zeilen.append(freitext.strip())
    return "\n".join(zeilen)


def add_site_log(
    db: Session,
    site: IncidentSite,
    kind: str,
    text: str,
    *,
    user_id: int | None,
    author_name: str | None,
    einheit_id: int | None = None,
    erfasst_at: datetime | None = None,
) -> SiteLogEntry:
    """Legt einen Chronik-Eintrag an; der Aufrufer verantwortet den Commit."""
    entry = SiteLogEntry(
        incident_site_id=site.id,
        kind=kind,
        text=text.strip(),
        user_id=user_id,
        author_name=author_name,
        einheit_id=einheit_id,
        erfasst_at=erfasst_at,
    )
    db.add(entry)
    if kind in SITE_LOG_RESET_KINDS:
        # Lokaler Import verhindert einen Import-Zyklus über resource_service.
        from app.services import lagemeldung_service
        lagemeldung_service.register_lagemeldung(site, db)
    return entry
