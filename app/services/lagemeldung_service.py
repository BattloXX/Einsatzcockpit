"""GSL-Lagemeldungs-Regelkreis (SKKM): Timer-Logik je Einsatzstelle.

Erzwingt sanft regelmäßige Lagemeldungen: jede laufende Einsatzstelle bekommt
einen Timer (`naechste_lagemeldung_at`). Läuft er ab, erzeugt der Reminder-Loop
einen Auftrag im Funkjournal. Das Erfassen einer Lagemeldung setzt den Timer
zurück und schließt offene Auto-Aufträge (= Kontrolle im Führungskreislauf).

Org-Konfiguration steuert das Verhalten:
- `gsl_lagemeldung_interval_minutes`  – Default-Intervall; NULL ⇒ Logik komplett aus.
- `gsl_lagemeldung_interval_sofort_minutes` – Override bei Priorität „Sofort“; NULL ⇒ Default.
- `gsl_lagemeldung_auto_auftrag` – ob ein Funkjournal-Auftrag erzeugt wird.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.major_incident import (
    AUTO_KIND_LAGEMELDUNG,
    CommLogEntry,
    IncidentSite,
    LageEinheit,
    SitePhase,
    SitePriority,
    SiteResourceAssignment,
)
from app.models.master import OrgSettings


def org_settings_for(db: Session, org_id: int | None) -> OrgSettings | None:
    if not org_id:
        return None
    return db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()


def interval_minutes_for(site: IncidentSite, org_settings: OrgSettings | None) -> int | None:
    """Effektives Lagemeldungs-Intervall für eine Einsatzstelle.

    None ⇒ keine Timer-Pflicht (Default-Intervall nicht konfiguriert).
    Bei Priorität „Sofort“ wird – falls gesetzt – das kürzere Sofort-Intervall verwendet.
    """
    if org_settings is None:
        return None
    base = org_settings.gsl_lagemeldung_interval_minutes
    if base is None:
        return None
    if site.priority == SitePriority.sofort and org_settings.gsl_lagemeldung_interval_sofort_minutes:
        return org_settings.gsl_lagemeldung_interval_sofort_minutes
    return base


def has_active_resource(site: IncidentSite, db: Session) -> bool:
    """True, wenn der Einsatzstelle mindestens eine nicht freigegebene Ressource zugeordnet ist.

    Per DB-Query (statt der Python-Collection), damit frisch hinzugefügte bzw. soeben
    freigegebene Ressourcen via SQLAlchemy-Autoflush bereits korrekt berücksichtigt werden.
    """
    ra = (
        db.query(SiteResourceAssignment.id)
        .filter(
            SiteResourceAssignment.incident_site_id == site.id,
            SiteResourceAssignment.released_at.is_(None),
        )
        .first()
    )
    if ra is not None:
        return True
    le = (
        db.query(LageEinheit.id)
        .filter(
            LageEinheit.incident_site_id == site.id,
            LageEinheit.status != "abgerueckt",
        )
        .first()
    )
    return le is not None


def _now() -> datetime:
    # naive UTC – konsistent mit den DateTime-Spalten (keine tz-Info)
    return datetime.now(UTC).replace(tzinfo=None)


def recompute_due(site: IncidentSite, org_settings: OrgSettings | None) -> None:
    """Setzt `naechste_lagemeldung_at` auf jetzt + Intervall (oder None, wenn aus)."""
    interval = interval_minutes_for(site, org_settings)
    if interval is None:
        site.naechste_lagemeldung_at = None
    else:
        site.naechste_lagemeldung_at = _now() + timedelta(minutes=interval)


def close_open_auto_auftraege(db: Session, site_id: int) -> int:
    """Markiert offene Auto-Aufträge „Lagemeldung anfordern“ der Einsatzstelle als erledigt.

    Gibt die Anzahl geschlossener Aufträge zurück.
    """
    open_auftraege = (
        db.query(CommLogEntry)
        .filter(
            CommLogEntry.related_site_id == site_id,
            CommLogEntry.auto_kind == AUTO_KIND_LAGEMELDUNG,
            CommLogEntry.handled == False,  # noqa: E712
        )
        .all()
    )
    for a in open_auftraege:
        a.handled = True
    return len(open_auftraege)


def ensure_timer(site: IncidentSite, db: Session) -> bool:
    """Startet den Timer, wenn er noch nicht läuft und die Voraussetzungen erfüllt sind.

    Voraussetzungen: Phase „in_arbeit“, ≥1 aktive Ressource, Intervall konfiguriert,
    Timer bisher None. Gibt True zurück, wenn ein Timer gesetzt wurde.
    """
    if site.naechste_lagemeldung_at is not None:
        return False
    if site.phase != SitePhase.in_arbeit:
        return False
    org_settings = org_settings_for(db, site.org_id)
    if interval_minutes_for(site, org_settings) is None:
        return False
    if not has_active_resource(site, db):
        return False
    recompute_due(site, org_settings)
    return True


def clear_timer(site: IncidentSite, db: Session) -> None:
    """Stoppt den Timer und schließt offene Auto-Aufträge der Einsatzstelle."""
    site.naechste_lagemeldung_at = None
    close_open_auto_auftraege(db, site.id)


def register_lagemeldung(site: IncidentSite, db: Session) -> None:
    """Erfasste Lagemeldung = Kontrolle: Timer zurücksetzen + Auto-Aufträge schließen."""
    org_settings = org_settings_for(db, site.org_id)
    recompute_due(site, org_settings)
    close_open_auto_auftraege(db, site.id)


def recompute_if_active(site: IncidentSite, db: Session) -> None:
    """Neuberechnung nur, wenn bereits ein Timer läuft (z.B. nach Prio-Wechsel)."""
    if site.naechste_lagemeldung_at is None:
        return
    org_settings = org_settings_for(db, site.org_id)
    recompute_due(site, org_settings)
