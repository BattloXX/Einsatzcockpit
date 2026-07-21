"""DIBOS→Einsatz-Anreicherung (Org-Opt-in, siehe OrgDibosConfig.enrich_incidents).

Ordnet GetCurrentEvents-Objekte über die stabile Einsatznummer (eventNumber ==
Incident.lis_operation_number) einem bereits bestehenden, AKTIVEN Einsatz zu und
ergänzt ihn um Felder, die der LIS/IPR-Sync (lis_sync.py) nicht liefert: den
vollständigen Einsatzort (Ortsteil/PLZ/Objekt), Einsatzcode/Diagnose, BMA-Nr.
und das sichtbare Meldungsprotokoll.

Rein additiv/anreichernd — legt NIE einen Einsatz an, setzt NIE eine
lis_operation_id/-number und beeinflusst das Dedup-Matching (lis_matching.py)
in keiner Weise. Läuft nur, wenn eine Org das explizit aktiviert hat
(enrich_incidents=True), siehe dibos_loop.py::_check_org(). Fahrzeug-Status
(S4/S5) und Zu-/Absagen werden bewusst NICHT hier gespiegelt — das liefert für
Orgs mit LIS/IPR-Anbindung bereits lis_sync._sync_vehicle_status() aus einer
autoritativen Quelle; ein zweiter, DIBOS-basierter Schreiber auf dieselben
Felder würde nur widersprüchliche Zeitstempel riskieren.
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.dibos.dibos_client import parse_events

logger = logging.getLogger("einsatzleiter.dibos.enrich")


def _find_active_incident_by_event_number(db: Session, org_id: int, event_number: str | None):
    if not event_number:
        return None
    from app.models.incident import Incident
    return (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org_id,
            Incident.lis_operation_number == event_number,
            Incident.status == "active",
        )
        .first()
    )


def _enrich_address(incident, location: dict) -> bool:
    """Ergänzt fehlende Adress-/Koordinatenfelder — überschreibt NIE bereits
    vorhandene Werte (z.B. vom LIS/IPR-Sync oder manueller Korrektur)."""
    changed = False
    if not incident.address_street and location.get("street"):
        incident.address_street = location["street"]
        changed = True
    if not incident.address_no and location.get("streetNo"):
        incident.address_no = location["streetNo"]
        changed = True
    if not incident.address_city and location.get("city"):
        incident.address_city = location["city"]
        changed = True
    if (
        incident.lat is None and incident.lng is None
        and location.get("latitude") is not None and location.get("longitude") is not None
    ):
        incident.lat = location["latitude"]
        incident.lng = location["longitude"]
        changed = True
    return changed


def _enrich_metadata(incident, event: dict) -> bool:
    """Aktualisiert die reinen DIBOS-Zusatzfelder (dibos_*) — diese kommen nur
    von hier, daher unbedenklich bei jedem Poll zu überschreiben (Idempotenz:
    gleicher Wert → kein Diff → changed bleibt False)."""
    changed = False
    if event.get("tycod") and incident.dibos_tycod != event["tycod"]:
        incident.dibos_tycod = event["tycod"]
        changed = True
    if event.get("diagnose") and incident.dibos_diagnose != event["diagnose"]:
        incident.dibos_diagnose = event["diagnose"]
        changed = True
    if event.get("bmaNo") and incident.dibos_bma_no != event["bmaNo"]:
        incident.dibos_bma_no = event["bmaNo"]
        changed = True
    if event.get("eventComment") and incident.dibos_event_comment != event["eventComment"]:
        incident.dibos_event_comment = event["eventComment"]
        changed = True
    return changed


def _sync_dibos_comments(db: Session, org_id: int, incident, comments: list[dict]) -> bool:
    """Importiert das sichtbare DIBOS-Meldungsprotokoll als Message-Cards.

    Dedupliziert über LisSyncedObject (obj_type="dibos_comment") — dieselbe
    generische Dedup-Tabelle, die der LIS/IPR-Sync auch für Dokumente nutzt
    (siehe lis_sync._sync_documents), nur mit einem eigenen obj_type.

    Nur NICHT-interne Kommentare (isInternal=False) werden übernommen: die
    "###"/"**"-Systemzeilen (Einheitenvorschlag-Details, LOI-Suche, Dispose-
    Meldungen) sind für das Einsatzjournal zu kleinteilig/technisch — isInternal
    trennt im Rohfeed bereits genau danach.
    """
    if not comments:
        return False
    from app.models.incident import IncidentColumn, Message
    from app.models.lis import LisSyncedObject

    messages_col = (
        db.query(IncidentColumn)
        .filter(IncidentColumn.incident_id == incident.id, IncidentColumn.code == "messages")
        .first()
    )

    changed = False
    for comment in comments:
        if comment.get("isInternal"):
            continue
        comment_id = comment.get("id")
        text = (comment.get("text") or "").strip()
        if not comment_id or not text:
            continue
        already = (
            db.query(LisSyncedObject.id)
            .filter(
                LisSyncedObject.org_id == org_id,
                LisSyncedObject.obj_type == "dibos_comment",
                LisSyncedObject.lis_id == str(comment_id),
            )
            .first()
        )
        if already:
            continue
        # Race-Schutz: zwischen der obigen Prüfung und diesem Insert kann ein
        # anderer Poll-Durchlauf (z.B. ein paralleler Trace/Thread) denselben
        # Kommentar bereits synchronisiert haben — uq_lis_synced_org_type_id
        # (models/lis.py) verhindert die Dublette auf DB-Ebene, ein Savepoint
        # sorgt dafür, dass NUR dieser eine Kommentar zurückgerollt wird, nicht
        # bereits in dieser Runde erfolgreich importierte andere Kommentare
        # (Muster: lis_sync._get_or_link_incident IntegrityError-Behandlung).
        try:
            with db.begin_nested():
                db.add(Message(
                    incident_id=incident.id,
                    column_id=messages_col.id if messages_col else None,
                    title="DIBOS: Meldung",
                    detail=text,
                    author_name=comment.get("creationPerson") or "DIBOS",
                ))
                db.add(LisSyncedObject(
                    org_id=org_id, obj_type="dibos_comment", lis_id=str(comment_id), incident_id=incident.id,
                ))
                db.flush()
        except IntegrityError:
            logger.info(
                "DIBOS-Kommentar %s (Einsatz %s) bereits synchronisiert (Race) — übersprungen",
                comment_id, incident.id,
            )
            continue
        changed = True
        logger.info("Meldung aus DIBOS-Kommentar %s auf Einsatz %s übernommen", comment_id, incident.id)
    return changed


def enrich_events_for_org(org_id: int, raw_events: list[dict]) -> list[int]:
    """Reichert aktive Einsätze der Org mit DIBOS-Zusatzinfos an.

    Läuft synchron in einer eigenen DB-Session (aus dibos_loop.py per
    asyncio.to_thread aufgerufen) — parallel zum bestehenden LIS/IPR-Sync, ohne
    dessen Matching/Dedup zu berühren. Ein Fehler bricht nur den eigenen
    Anreicherungs-Durchlauf ab (Rollback + Log), nie den DIBOS-Poll selbst.

    Gibt die IDs der tatsächlich geänderten Einsätze zurück (für den
    Board-Live-Broadcast im Aufrufer).
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    changed_ids: list[int] = []
    try:
        for event in parse_events(raw_events):
            incident = _find_active_incident_by_event_number(db, org_id, event.get("eventNumber"))
            if not incident:
                continue
            changed = False
            changed |= _enrich_address(incident, event.get("location") or {})
            changed |= _enrich_metadata(incident, event)
            changed |= _sync_dibos_comments(db, org_id, incident, event.get("comments") or [])
            if changed:
                db.flush()
                changed_ids.append(incident.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("DIBOS-Einsatzanreicherung für Org %s fehlgeschlagen", org_id)
    finally:
        db.close()
    return changed_ids
