"""Reminder-Loop für überfällige GSL-Lagemeldungen (SKKM-Regelkreis).

Läuft periodisch und sucht laufende Einsatzstellen, deren Lagemeldungs-Timer
(`naechste_lagemeldung_at`) abgelaufen ist. Pro überfälliger Einsatzstelle wird –
sofern in der Org aktiviert und noch kein offener Auto-Auftrag existiert – ein
Auftrag „Lagemeldung anfordern“ im Funkjournal erzeugt (`is_request=True`,
`auto_kind="lagemeldung_faellig"`). Das Erfassen einer Lagemeldung schließt den
Auftrag und setzt den Timer zurück (siehe lagemeldung_service.register_lagemeldung).

Dedup: solange ein Auto-Auftrag offen ist, wird kein neuer erzeugt. Ist der
Auto-Auftrag in der Org deaktiviert, bleibt nur die Board-Markierung (roter Chip).
"""
import asyncio
import logging
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    AUTO_KIND_LAGEMELDUNG,
    CommLogEntry,
    IncidentSite,
    MajorIncident,
    MajorIncidentStatus,
    SitePhase,
)
from app.services import lagemeldung_service
from app.services.broadcast import broadcast_lage

logger = logging.getLogger("einsatzleiter.gsl_lagemeldung_reminder")

LOOP_INTERVAL_SECONDS = 45


def _scan_and_create(db) -> list[dict]:
    """Erzeugt fällige Auto-Aufträge und gibt die zu broadcastenden Events zurück."""
    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = (
        db.query(IncidentSite)
        .join(MajorIncident, MajorIncident.id == IncidentSite.major_incident_id)
        .filter(
            IncidentSite.naechste_lagemeldung_at.isnot(None),
            IncidentSite.naechste_lagemeldung_at <= now,
            IncidentSite.phase == SitePhase.in_arbeit,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .all()
    )

    events: list[dict] = []
    changed = False
    for site in candidates:
        # Ohne aktive Ressource keine Lagemeldungs-Pflicht
        if not lagemeldung_service.has_active_resource(site, db):
            continue

        org_settings = lagemeldung_service.org_settings_for(db, site.org_id)
        # Auto-Auftrag deaktiviert ⇒ nur Board-Markierung (roter Chip), kein Journaleintrag
        if org_settings is None or not org_settings.gsl_lagemeldung_auto_auftrag:
            continue

        # Dedup: bereits ein offener Auto-Auftrag für diese Einsatzstelle?
        existing = (
            db.query(CommLogEntry.id)
            .filter(
                CommLogEntry.related_site_id == site.id,
                CommLogEntry.auto_kind == AUTO_KIND_LAGEMELDUNG,
                CommLogEntry.handled == False,  # noqa: E712
            )
            .first()
        )
        if existing is not None:
            continue

        db.add(CommLogEntry(
            major_incident_id=site.major_incident_id,
            related_site_id=site.id,
            direction="out",
            is_request=True,
            auto_kind=AUTO_KIND_LAGEMELDUNG,
            message="Lagemeldung anfordern – nächste Lagemeldung überfällig",
            author_name="System",
        ))
        changed = True
        events.append({"lage_id": site.major_incident_id, "site_id": site.id})

    if changed:
        db.commit()
    return events


async def gsl_lagemeldung_reminder_loop() -> None:
    logger.info("gsl_lagemeldung_reminder_loop gestartet")
    while True:
        try:
            await asyncio.sleep(LOOP_INTERVAL_SECONDS)
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                events = _scan_and_create(db)
            finally:
                db.close()
            for ev in events:
                lage_id = ev["lage_id"]
                try:
                    await broadcast_lage(lage_id, {"type": "funkjournal:changed"})
                    await broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": ev["site_id"]})
                except Exception:
                    logger.exception(
                        "gsl_lagemeldung_reminder: WS-Broadcast für Lage %s fehlgeschlagen", lage_id
                    )
        except asyncio.CancelledError:
            logger.info("gsl_lagemeldung_reminder_loop beendet")
            break
        except Exception:
            logger.exception("gsl_lagemeldung_reminder_loop: Iteration fehlgeschlagen")
