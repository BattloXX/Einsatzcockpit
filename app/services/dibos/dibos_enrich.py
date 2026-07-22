"""DIBOS→Einsatz-Anreicherung (Org-Opt-in, siehe OrgDibosConfig.enrich_incidents).

Ordnet GetCurrentEvents-Objekte über die stabile Einsatznummer (eventNumber ==
Incident.lis_operation_number) einem bereits bestehenden, AKTIVEN Einsatz zu und
ergänzt ihn um Felder, die der LIS/IPR-Sync (lis_sync.py) nicht liefert: den
vollständigen Einsatzort (Ortsteil/PLZ/Objekt), Einsatzcode/Diagnose, BMA-Nr.
und das sichtbare Meldungsprotokoll.

Rein additiv/anreichernd — legt NIE einen Einsatz an, setzt NIE eine
lis_operation_id/-number und beeinflusst das Dedup-Matching (lis_matching.py)
in keiner Weise. Läuft nur, wenn eine Org das explizit aktiviert hat
(enrich_incidents=True) — UNABHÄNGIG von einer laufenden Voll-Aufzeichnung
(auto_trace_on_event): der leichte Erkennungs-Loop (dibos_loop.py::_check_org())
ruft enrich_and_broadcast() direkt auf einem einfachen GetCurrentEvents-Poll
auf, ohne Rohdaten aufzuzeichnen — spart Speicherlast, wenn nur die Anreicherung
gewünscht ist. Läuft zusätzlich eine Voll-Aufzeichnung, übernimmt deren eigener
Poll-Zyklus (dibos_capture.py::_capture_once()) die Anreicherung, damit
GetCurrentEvents nicht doppelt abgefragt wird. Fahrzeug-Status
(S4/S5) wird bewusst NICHT hier gespiegelt — das liefert für Orgs mit LIS/IPR-
Anbindung bereits lis_sync._sync_vehicle_status() aus einer autoritativen
Quelle; ein zweiter, DIBOS-basierter Schreiber auf dieselben Felder würde nur
widersprüchliche Zeitstempel riskieren.

Personen-Zu-/Absagen (personResponseList, siehe
LWZEventHub_Personenrueckmeldung.md) sind davon ausgenommen: die LIS-Pipeline
schreibt sie nur als Freitext-Log (IncidentLog, siehe
lis_sync._sync_person_responses) und speist damit NICHT das Zu-/Absage-Widget
im Board (das liest strukturiert aus Teilnahme.rsvp_status, siehe
ui_incident.py::incident_rsvp_summary — bisher ohne echten Schreiber). DIBOS
liefert mit "id" (stabiler Datensatz-Schlüssel je Person+Einsatz) und
"changeDate" (Versionsanker) genau das, was für ein echtes Upsert in
Teilnahme nötig ist — siehe _sync_person_responses() unten.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.dibos.dibos_client import parse_events

logger = logging.getLogger("einsatzleiter.dibos.enrich")

_RSVP_DELAYED_RE = re.compile(r"^\d+\s*min\.?$", re.IGNORECASE)


def _map_dibos_rsvp_status(status: str | None) -> str | None:
    """Bildet den DIBOS-Rückmeldestatus auf Teilnahme.rsvp_status ab.

    "Zugesagt"/"Abgesagt" direkt. Zeitversetzte Zusagen ("10 Min", "5 Min", ...)
    zählen als Zusage — die Person kommt, nur später (siehe
    LWZEventHub_Personenrueckmeldung.md Abschnitt 3; die genaue ETA geht dabei
    verloren, Teilnahme kennt keinen ETA-Wert — für die Zu-/Absage-Zählung im
    Board-Widget reicht "kommt" vs. "kommt nicht"). Unbekannte/neue Statuswerte
    werden NICHT geraten, sondern übersprungen (None), um keine falsche
    Zu-/Absage zu erzeugen.
    """
    if not status:
        return None
    normalized = status.strip().lower()
    if normalized == "zugesagt":
        return "zugesagt"
    if normalized == "abgesagt":
        return "abgesagt"
    if _RSVP_DELAYED_RE.match(normalized):
        return "zugesagt"
    return None


def _parse_dibos_datetime(value: str | None, org) -> datetime | None:
    """Parst einen DIBOS-Zeitstempel-String (variable Bruchteilssekunden-Länge,
    z.B. "2026-07-21T17:47:22.9698014") zu naive UTC.

    Naive Werte (kein Offset) werden als Org-Lokalzeit interpretiert (siehe
    app.core.timezones.org_tz) — dieselbe Behandlung wie LIS-Zeitstempel
    (lis_sync._parse_operation_datetime), da DIBOS ebenfalls ein
    österreichisches Regionalsystem ist.
    """
    if not value:
        return None
    raw = value.strip()
    if "." in raw:
        head, _, frac = raw.partition(".")
        digits = "".join(ch for ch in frac if ch.isdigit())[:6]
        tail = "".join(ch for ch in frac if not ch.isdigit())
        raw = f"{head}.{digits}{tail}" if digits else head + tail
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    from app.core.timezones import org_tz
    return dt.replace(tzinfo=org_tz(org)).astimezone(UTC).replace(tzinfo=None)


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


def _has_confirmed_objekt_link(db: Session, incident_id: int) -> bool:
    """True, wenn der Einsatz bereits ein BESTÄTIGTES Objekt hat (egal welche
    Quelle: manuell, Adresse, LIS-BMA-Text, ...). Verhindert, dass ein neu über
    DIBOS bekannt gewordener BMA-Treffer einen bereits von einem Menschen (oder
    einer anderen Quelle) bestätigten Objekt-Link durch ein zweites Objekt
    ergänzt — match_incident() selbst dedupliziert nur pro Objekt-ID, nicht
    "schon irgendein bestätigter Link vorhanden" (siehe Stufe 1/2 in
    objekt_matching_service.py)."""
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    return (
        db.query(ObjektEinsatz.id)
        .filter(ObjektEinsatz.incident_id == incident_id, ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT)
        .execution_options(include_all_tenants=True)
        .first()
        is not None
    )


def _match_objekt_by_dibos_bma(db: Session, incident) -> bool:
    """Sucht über die DIBOS-BMA-Nummer (Stufe 1 in objekt_matching_service.py,
    seit dort um incident.dibos_bma_no erweitert) ein passendes Objekt und
    verknüpft es mit dem Einsatz — nur wenn noch KEIN bestätigter Objekt-Link
    existiert und die Objektverwaltung für die Org aktiv ist. Gibt True zurück,
    wenn ein neuer Link entstanden ist (Board-Reload nötig).

    Läuft synchron in der bestehenden Session von enrich_events_for_org() —
    match_incident() selbst committet nicht, das übernimmt der Aufrufer.
    """
    if _has_confirmed_objekt_link(db, incident.id):
        return False
    from app.services.objekt_service import objekt_effective_enabled
    if not objekt_effective_enabled(incident.primary_org_id, db):
        return False
    from app.services.objekt_matching_service import match_incident
    try:
        neu = match_incident(db, incident)
    except Exception:
        logger.exception("Objekt-Matching über DIBOS-BMA-Nr. fehlgeschlagen (Einsatz %s)", incident.id)
        return False
    if neu:
        logger.info(
            "Einsatz %s über DIBOS-BMA-Nr. %s mit Objekt verknüpft (%s)",
            incident.id, incident.dibos_bma_no, [e.objekt_id for e in neu],
        )
    return bool(neu)


def _match_member_by_sybos(db: Session, org_id: int, sybos_id: str | None):
    """Löst eine DIBOS-personResponseList[].idSybos auf ein Mitglied auf, sofern
    dessen syBOS-ID über den Mitglieder-Excel-Import hinterlegt wurde (siehe
    ui_admin.py::import_members_excel). idSybos ist nur für sybos-angebundene
    Dienststellen befüllt — ohne Treffer bleibt die Rückmeldung ein
    Freitext-Eintrag (Teilnahme.freitext_name)."""
    if not sybos_id:
        return None
    from app.models.master import Member
    return (
        db.query(Member)
        .filter(Member.org_id == org_id, Member.sybos_id == sybos_id)
        .execution_options(include_all_tenants=True)
        .first()
    )


def _find_teilnahme(db: Session, org_id: int, incident_id: int, dibos_response_id, mitglied_id: int | None):
    from app.models.teilnahme import Teilnahme

    by_response = (
        db.query(Teilnahme)
        .filter(Teilnahme.org_id == org_id, Teilnahme.dibos_response_id == dibos_response_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if by_response is not None:
        return by_response
    if mitglied_id is None:
        return None
    # Fängt eine bereits per anderer Quelle (z.B. Teams-Bot) angelegte Zeile für
    # dasselbe Mitglied/denselben Einsatz ab, bevor sie erstmals über DIBOS
    # aktualisiert wird — sonst würde uq_teilnahme_mitglied beim Insert greifen.
    return (
        db.query(Teilnahme)
        .filter(
            Teilnahme.org_id == org_id, Teilnahme.bezug_typ == "einsatz",
            Teilnahme.bezug_id == incident_id, Teilnahme.mitglied_id == mitglied_id,
        )
        .execution_options(include_all_tenants=True)
        .first()
    )


def _sync_person_responses(db: Session, org_id: int, org, incident, person_responses: list[dict]) -> bool:
    """Upsert der DIBOS-Personenrückmeldungen (Zu-/Absagen) in Teilnahme —
    speist damit direkt das Zu-/Absage-Widget im Board (ui_incident.py::
    incident_rsvp_summary), das bisher keinen echten Schreiber hatte.

    changeDate ist der Versionsanker: eine eingehende Rückmeldung wird nur
    übernommen, wenn sie neuer ist als der bereits gespeicherte Stand (schützt
    auch gegen ein Zurückfallen auf einen älteren Stand, falls eine andere
    Quelle – z.B. Teams-Bot – zwischenzeitlich einen neueren geschrieben hat).
    """
    if not person_responses:
        return False
    from app.models.teilnahme import Teilnahme

    changed = False
    for resp in person_responses:
        response_id = resp.get("id")
        if not response_id:
            continue
        status = _map_dibos_rsvp_status(resp.get("status"))
        if not status:
            continue
        change_date = _parse_dibos_datetime(resp.get("changeDate"), org)
        if change_date is None:
            continue

        member = _match_member_by_sybos(db, org_id, resp.get("idSybos"))
        mitglied_id = member.id if member else None
        existing = _find_teilnahme(db, org_id, incident.id, response_id, mitglied_id)

        if existing is not None and existing.rsvp_at is not None and existing.rsvp_at >= change_date:
            continue  # kein neuerer Stand als bereits gespeichert (Versionsanker)

        person_name = resp.get("person") or "Unbekannt"

        try:
            with db.begin_nested():
                if existing is not None:
                    existing.rsvp_status = status
                    existing.rsvp_at = change_date
                    existing.rsvp_source = "dibos"
                    existing.dibos_response_id = response_id
                    if member is not None:
                        existing.mitglied_id = member.id
                        existing.freitext_name = None
                    elif not existing.mitglied_id:
                        existing.freitext_name = person_name
                else:
                    db.add(Teilnahme(
                        org_id=org_id, bezug_typ="einsatz", bezug_id=incident.id,
                        mitglied_id=mitglied_id, freitext_name=None if member else person_name,
                        rsvp_status=status, rsvp_at=change_date, rsvp_source="dibos",
                        dibos_response_id=response_id,
                    ))
                db.flush()
        except IntegrityError:
            logger.info(
                "DIBOS-Personenrückmeldung %s (Einsatz %s) bereits synchronisiert (Race) — übersprungen",
                response_id, incident.id,
            )
            continue
        changed = True
        logger.info(
            "Zu-/Absage aus DIBOS übernommen: %s -> %s (Einsatz %s, Mitglied %s)",
            person_name, status, incident.id, mitglied_id or "kein Treffer",
        )
    return changed


def enrich_events_for_org(org_id: int, raw_events: list[dict]) -> dict:
    """Reichert aktive Einsätze der Org mit DIBOS-Zusatzinfos an.

    Läuft synchron in einer eigenen DB-Session (aus dibos_loop.py per
    asyncio.to_thread aufgerufen) — parallel zum bestehenden LIS/IPR-Sync, ohne
    dessen Matching/Dedup zu berühren. Ein Fehler bricht nur den eigenen
    Anreicherungs-Durchlauf ab (Rollback + Log), nie den DIBOS-Poll selbst.

    Gibt {"changed_ids": [...], "rsvp_changed_ids": [...]} zurück — Erstere für
    den generellen Board-Reload-Broadcast, Letztere zusätzlich für den
    gezielten "rsvp:changed"-Broadcast (Zu-/Absage-Widget), siehe
    dibos_capture.py::_enrich_and_broadcast().
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import FireDept

    db = SessionLocal()
    set_tenant_context(db, None)
    changed_ids: list[int] = []
    rsvp_changed_ids: list[int] = []
    try:
        org = db.get(FireDept, org_id)
        for event in parse_events(raw_events):
            incident = _find_active_incident_by_event_number(db, org_id, event.get("eventNumber"))
            if not incident:
                continue
            changed = False
            changed |= _enrich_address(incident, event.get("location") or {})
            changed |= _enrich_metadata(incident, event)
            changed |= _sync_dibos_comments(db, org_id, incident, event.get("comments") or [])
            if event.get("bmaNo"):
                changed |= _match_objekt_by_dibos_bma(db, incident)
            rsvp_changed = _sync_person_responses(db, org_id, org, incident, event.get("personResponses") or [])
            changed |= rsvp_changed
            if rsvp_changed:
                rsvp_changed_ids.append(incident.id)
            if changed:
                db.flush()
                changed_ids.append(incident.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("DIBOS-Einsatzanreicherung für Org %s fehlgeschlagen", org_id)
    finally:
        db.close()
    return {"changed_ids": changed_ids, "rsvp_changed_ids": rsvp_changed_ids}


async def enrich_and_broadcast(org_id: int, raw_events: list[dict]) -> None:
    """Reichert an (in einem Thread, da synchron/DB-blockierend) und broadcastet
    pro tatsächlich geänderten Einsatz — Fehler dürfen den aufrufenden Poll nie
    abbrechen. Gemeinsamer Einstiegspunkt für BEIDE DIBOS-Aufrufer:
    dibos_capture.py::_capture_once() (während einer laufenden Voll-Aufzeichnung)
    UND dibos_loop.py::_check_org() (leichter Poll, KEINE Voll-Aufzeichnung nötig
    — reduziert die Speicherlast, da keine Rohdaten auf Platte geschrieben werden).

    Zwei Broadcast-Typen: "dibos_sync" (voller Board-Reload) für jeden geänderten
    Einsatz, zusätzlich das gezielte "rsvp:changed" (nur Zu-/Absage-Widget neu
    laden, siehe app.js) für Einsätze mit neuen Personenrückmeldungen.
    """
    try:
        result = await asyncio.to_thread(enrich_events_for_org, org_id, raw_events)
    except Exception:
        logger.exception("DIBOS-Einsatzanreicherung fehlgeschlagen (Org %s)", org_id)
        return
    changed_ids = result.get("changed_ids") or []
    rsvp_changed_ids = result.get("rsvp_changed_ids") or []
    if not changed_ids and not rsvp_changed_ids:
        return
    from app.services.broadcast import manager
    for incident_id in changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "dibos_sync", "reload_board": True})
        except Exception:
            logger.exception("DIBOS-Broadcast für Einsatz %s fehlgeschlagen", incident_id)
    for incident_id in rsvp_changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "rsvp:changed"})
        except Exception:
            logger.exception("DIBOS-RSVP-Broadcast für Einsatz %s fehlgeschlagen", incident_id)
