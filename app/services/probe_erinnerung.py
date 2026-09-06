"""Tägliche, entdoppelte Erinnerungen für die Vorbereitung von Proben.

Die Regeln folgen dem Probenplan-Konzept: 14 Tage vor einer Probe ohne begonnene
Vorbereitung, sieben Tage bei höchstens 45 Prozent Checklistenfortschritt und zwei
Tage bei offenen Pflichtpunkten. Eine ProbeChange-Zeile ist zugleich Audit-Eintrag
und idempotenter Sendemarker je Regel.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.core.timezones import now_local, to_org_tz
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.probenplanung import ProbeChange, ProbeCheckliste, TerminStatus
from app.models.teilnahme import Termin
from app.services.probe_checklist_service import fortschritt
from app.services.probe_history import write_probe_change

logger = logging.getLogger("einsatzleiter.probe_erinnerung")

_ABGESCHLOSSENE_STATUS = {
    TerminStatus.durchgefuehrt,
    TerminStatus.abgeschlossen,
    TerminStatus.abgesagt,
}


def _regel_fuer(
    termin: Termin, checkliste: ProbeCheckliste | None, org: FireDept, heute
) -> tuple[str, str, str, dict] | None:
    lokal = to_org_tz(termin.beginn, org)
    if lokal is None:
        return None
    tage_bis = (lokal.date() - heute).days
    if tage_bis not in {14, 7, 2}:
        return None
    progress = fortschritt(checkliste, org) if checkliste else None
    prozent = progress.prozent if progress else 0
    offene_pflichtpunkte = progress.offene_pflichtpunkte if progress else 0
    if tage_bis == 14 and termin.status in {TerminStatus.entwurf, TerminStatus.geplant}:
        return (
            "erinnerung.14_tage",
            "Probe in 14 Tagen",
            f"{termin.titel}: Die Vorbereitung wurde noch nicht begonnen.",
            {"tage_bis": tage_bis, "prozent": prozent},
        )
    if tage_bis == 7 and prozent <= 45:
        return (
            "erinnerung.7_tage",
            "Probe in 7 Tagen",
            f"{termin.titel}: Die Checkliste ist erst zu {prozent} % erledigt.",
            {"tage_bis": tage_bis, "prozent": prozent},
        )
    if tage_bis == 2 and offene_pflichtpunkte:
        return (
            "erinnerung.2_tage",
            "Probe in 2 Tagen",
            f"{termin.titel}: {offene_pflichtpunkte} Pflichtpunkt(e) sind noch offen.",
            {"tage_bis": tage_bis, "offene_pflichtpunkte": offene_pflichtpunkte},
        )
    return None


def pruefe_probe_erinnerungen(db, *, jetzt: datetime | None = None) -> int:
    """Sendet fällige Organisations-Pushes; sicher erneut ausführbar."""
    jetzt = jetzt or datetime.now(UTC)
    # Ein etwas weiterer UTC-Korridor deckt alle Organisationszeitzonen ab.
    kandidaten = (
        db.query(Termin)
        .filter(
            Termin.archiviert_am.is_(None),
            Termin.status.notin_(_ABGESCHLOSSENE_STATUS),
            Termin.beginn >= jetzt - timedelta(days=1),
            Termin.beginn < jetzt + timedelta(days=15),
        )
        .all()
    )
    orgs = {org.id: org for org in db.query(FireDept).filter(FireDept.id.in_({t.org_id for t in kandidaten})).all()}
    sent = 0
    for termin in kandidaten:
        org = orgs.get(termin.org_id)
        if org is None:
            continue
        checkliste = (
            db.query(ProbeCheckliste)
            .filter(ProbeCheckliste.org_id == termin.org_id, ProbeCheckliste.termin_id == termin.id)
            .first()
        )
        regel = _regel_fuer(termin, checkliste, org, now_local(org).date())
        if regel is None:
            continue
        action, title, text, details = regel
        bereits_gesendet = (
            db.query(ProbeChange.id)
            .filter(
                ProbeChange.org_id == termin.org_id,
                ProbeChange.termin_id == termin.id,
                ProbeChange.action == action,
            )
            .first()
        )
        if bereits_gesendet:
            continue
        # Die Push-Log-Zeile und der Proben-Auditmarker werden gemeinsam committed.
        from app.services.push_service import notify_org
        notify_org(
            db,
            termin.org_id,
            title,
            text,
            url=f"/probenplanung/{termin.id}?tab=vorbereitung",
            source="probe_erinnerung",
        )
        write_probe_change(
            db, termin.id, action, "erinnerung", None, None, details, org_id=termin.org_id
        )
        sent += 1
    if sent:
        db.commit()
    return sent


async def probe_erinnerung_loop() -> None:
    """Prüft stündlich; der Auditmarker verhindert Mehrfachbenachrichtigungen."""
    from app.services.loop_utils import iteration_watch
    logger.info("probe_erinnerung_loop gestartet")
    while True:
        try:
            await asyncio.sleep(3600)
            with iteration_watch(logger, "probe_erinnerung_loop", 3600):
                def _pruefe() -> int:
                    with SessionLocal() as db:
                        set_tenant_context(db, None)
                        return pruefe_probe_erinnerungen(db)
                sent = await asyncio.to_thread(_pruefe)
                if sent:
                    logger.info("%s Probe-Erinnerung(en) gesendet", sent)
        except asyncio.CancelledError:
            logger.info("probe_erinnerung_loop beendet")
            break
        except Exception:
            logger.exception("probe_erinnerung_loop: Iteration fehlgeschlagen")
