"""Geschäftslogik für Chronik-Einträge an Einsatzstellen."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.major_incident import SITE_LOG_RESET_KINDS, SITE_LOG_USER_KINDS, IncidentSite, SiteLogEntry

# Inhalte bestehender Lagemeldungen; keine zusätzlichen Chronik-Typen oder Status.
SCHNELLMELDUNGEN = {
    "erkundung": (
        ("massnahmen_erforderlich", "Maßnahmen erforderlich", "meldung"),
        ("keine_massnahmen", "Keine Maßnahmen erforderlich", "meldung"),
        ("akute_gefahr", "Akute Gefahr festgestellt", "meldung"),
        ("weitere_erkundung", "Weitere Erkundung erforderlich", "meldung"),
    ),
    "abarbeitung": (
        ("arbeit_laeuft", "Arbeit läuft", "meldung"),
        ("auftrag_erledigt", "Auftrag erledigt", "abgeschlossen"),
        ("unterstuetzung", "Unterstützung benötigt", "meldung"),
        ("nicht_durchfuehrbar", "Auftrag nicht durchführbar", "nicht_durchfuehrbar"),
    ),
}


def schnellmeldungen(auftragsart: str) -> list[dict[str, str]]:
    return [{"code": code, "label": label, "aktion": aktion, "hervorhebung": meldung_hervorhebung(label)}
            for code, label, aktion in SCHNELLMELDUNGEN.get(auftragsart, ())]


def format_schnellmeldung(auftragsart: str, code: str, text: str, *, ergebnis: bool = False) -> str:
    auswahl = next((m for m in schnellmeldungen(auftragsart) if m["code"] == code), None)
    if not auswahl or auswahl["aktion"] != "meldung":
        raise ValueError("schnellmeldung_ungueltig")
    if ergebnis and (auftragsart != "erkundung" or (
        code != "keine_massnahmen" and sum(c.isalnum() for c in text) < 3
    )):
        raise ValueError("erkundungsergebnis_erforderlich")
    return auswahl["label"] + ("\n" + text.strip() if text.strip() else "")


def meldung_hervorhebung(text: str) -> str:
    """Nur kanonische Schnellmeldungsüberschriften, keine Stichwortklassifikation."""
    kopf = (text or "").split("\n", 1)[0]
    return {"Akute Gefahr festgestellt": "gefahr", "Unterstützung benötigt": "unterstuetzung"}.get(kopf, "")


def aktuelle_dringende_meldungen(entries: list[SiteLogEntry]) -> list[SiteLogEntry]:
    """Je Einheit die letzte Lagemeldung; spätere Meldungen lösen den Kartenhinweis ab."""
    gesehen = set()
    result = []
    for entry in sorted(entries, key=lambda e: (e.ts, e.id), reverse=True):
        key = (entry.incident_site_id, entry.einheit_id)
        if entry.kind != "lagemeldung" or key in gesehen:
            continue
        gesehen.add(key)
        if meldung_hervorhebung(entry.text):
            result.append(entry)
    return result


def dringende_meldungen_fuer_stellen(db: Session, site_ids: list[int]) -> dict[int, list[SiteLogEntry]]:
    """Lädt für Boardkarten nur die letzte Lagemeldung pro Stelle/Einheit, keine gesamte Chronik."""
    result: dict[int, list[SiteLogEntry]] = {site_id: [] for site_id in site_ids}
    if not site_ids:
        return result
    letzte_ids = db.query(func.max(SiteLogEntry.id)).filter(
        SiteLogEntry.incident_site_id.in_(site_ids), SiteLogEntry.kind == "lagemeldung",
    ).group_by(SiteLogEntry.incident_site_id, SiteLogEntry.einheit_id)
    for entry in aktuelle_dringende_meldungen(db.query(SiteLogEntry).filter(SiteLogEntry.id.in_(letzte_ids)).all()):
        result[entry.incident_site_id].append(entry)
    return result


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
