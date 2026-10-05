"""DIBOS→Einsatz-Anreicherung (Org-Opt-in, siehe OrgDibosConfig.enrich_incidents)
und optionale Einsatzanlage (Org-Opt-in, siehe OrgDibosConfig.create_incidents).

Ordnet GetCurrentEvents-Objekte über die stabile Einsatznummer (eventNumber ==
Incident.lis_operation_number) einem bereits bestehenden, AKTIVEN Einsatz zu und
ergänzt ihn um Felder, die der LIS/IPR-Sync (lis_sync.py) nicht liefert: den
vollständigen Einsatzort (Ortsteil/PLZ/Objekt), Einsatzcode/Diagnose, BMA-Nr.
und das sichtbare Meldungsprotokoll.

Standardmäßig rein additiv/anreichernd — setzt NIE eine lis_operation_id/-number
und beeinflusst das Dedup-Matching (lis_matching.py) in keiner Weise, solange
eine Org nur enrich_incidents aktiviert hat. Mit create_incidents=True (siehe
_get_or_create_incident_for_event() unten, gedacht als Ersatz für die LIS/IPR-
Anbindung, sobald diese abgeschaltet wird) legt DIBOS für ein Event ohne
zuordenbaren Einsatz selbst einen neuen an — Matching über die Leitstellennummer
(eventNumber), analog zu lis_sync.py::_get_or_link_incident(). Beide Opt-ins
sind unabhängig voneinander aktivierbar. Läuft nur, wenn eine Org das explizit
aktiviert hat, und ausschließlich aus dem Poll-Loop (dibos_loop.py::_check_org())
— auch während einer laufenden Voll-Aufzeichnung (auto_trace_on_event), die
selbst rein lesend ist. Fahrzeug-Status
(S4/S5) wird bewusst NICHT hier gespiegelt — das liefert für Orgs mit LIS/IPR-
Anbindung bereits lis_sync._sync_vehicle_status() aus einer autoritativen
Quelle; ein zweiter, DIBOS-basierter Schreiber auf dieselben Felder würde nur
widersprüchliche Zeitstempel riskieren. Das gilt nur fuer Fahrzeuge, nicht fuer
die Wache selbst: Fuer sie gibt es kein LIS/IPR-Aequivalent und keinen anderen
Schreiber, daher wird ihr Status hier bewusst mitgepflegt.

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
_fallback_active: set[tuple[int, int]] = set()


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


def _is_exercise_event(event: dict) -> bool:
    """Erkennt einen Übungs-/Schulungseinsatz anhand von DIBOS-Freitextfeldern.

    DIBOS hat kein eigenes Übungs-Flag (anders als der Freitext in
    Operation.Type.Type bei LIS) — Keyword-Check auf tycodDescription/diagnose,
    dieselbe Wortliste wie lis_mapping.py::is_exercise_operation()."""
    from app.services.lis.lis_mapping import _EXERCISE_KEYWORDS
    text = f"{event.get('tycodDescription') or ''} {event.get('diagnose') or ''}".strip().lower()
    return any(keyword in text for keyword in _EXERCISE_KEYWORDS)


def _get_or_create_incident_for_event(db: Session, org, org_id: int, event: dict):
    """Analog zu lis_sync.py::_get_or_link_incident() für DIBOS-Events — Org-Opt-in
    OrgDibosConfig.create_incidents. Gibt (incident, created) zurück, oder None,
    wenn das Event keine Leitstellennummer hat (ohne die ist weder ein
    zuverlässiges Matching noch eine spätere erneute Zuordnung möglich).

    find_matching_incident() deckt dabei bereits zwei Fälle in einem Aufruf ab:
    1) ein Einsatz mit genau dieser lis_operation_number existiert bereits
       (unabhängig vom Status — auch ein bereits geschlossener zählt, siehe
       Modul-Docstring dort), 2) sonst die adress-/zeitfensterbasierte
       Heuristik über Alarmstichwort + Adresse (nur AKTIVE Einsätze). Erst wenn
       beides nichts findet, wird ein neuer Einsatz angelegt.
    """
    event_number = event.get("eventNumber")
    if not event_number:
        return None

    from app.models.incident import Incident
    from app.services.lis.lis_mapping import map_stichwort
    from app.services.lis.lis_matching import find_matching_incident

    location = event.get("location") or {}
    alarm_type_code = map_stichwort(event.get("tycod"))
    started_at = _parse_dibos_datetime(event.get("created"), org)
    report_text = event.get("eventComment") or event.get("diagnose")

    match = find_matching_incident(
        db, org_id,
        alarm_type_code=alarm_type_code,
        street=location.get("street"),
        city=location.get("city"),
        house_no=location.get("streetNo"),
        started_at=started_at,
        report_text=report_text,
        lis_operation_number=event_number,
    )
    if match:
        if match.lis_operation_number != event_number:
            match.lis_operation_number = event_number
            db.flush()
        return match, False

    from app.services.incident_service import create_incident
    # reject_near_duplicates bewusst NICHT gesetzt (Default False): find_matching_incident()
    # oben hat bereits sorgfaeltig (Leitstellennummer, dann Adresse+Zeitfenster) geprueft und
    # nichts gefunden - Muster identisch zu lis_sync._get_or_link_incident().
    incident, _ = create_incident(
        db,
        alarm_type_code=alarm_type_code,
        started_at=started_at,
        is_exercise=_is_exercise_event(event),
        address_street=location.get("street"),
        address_no=location.get("streetNo"),
        address_city=location.get("city"),
        lat=location.get("latitude"),
        lng=location.get("longitude"),
        report_text=report_text,
        reason=event.get("tycodDescription"),
        primary_org_id=org_id,
    )
    incident.lis_operation_number = event_number
    try:
        db.flush()
    except IntegrityError:
        # Race: ein zweiter, gleichzeitig laufender Poll (leichter Erkennungs-Loop und/oder
        # laufendes Voll-Tracing, siehe dibos_loop.py/dibos_capture.py) hat dieselbe
        # Leitstellennummer zwischen der obigen Prüfung und diesem Flush bereits committet
        # (uq_incident_org_lis_operation_number, siehe models/incident.py) — Muster 1:1 aus
        # lis_sync._get_or_link_incident().
        db.rollback()
        winner = (
            db.query(Incident)
            .filter(Incident.primary_org_id == org_id, Incident.lis_operation_number == event_number)
            .first()
        )
        if winner:
            logger.info(
                "Race beim Anlegen von Einsatz für DIBOS-Event %s (Org %s) — "
                "bereits von einem parallelen Poll angelegt, übernehme diesen (%s)",
                event_number, org_id, winner.id,
            )
            return winner, False
        raise
    logger.info("Einsatz %s aus DIBOS-Event %s neu angelegt (Org %s)", incident.id, event_number, org_id)
    return incident, True


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


def _sync_wache_status(
    db: Session,
    org,
    incident,
    event_number: str,
    raw_units: list[dict],
    wache_unid_filter: str | None,
) -> bool:
    """Uebernimmt passende Wachen aus den rohen GetCurrentUnits-Daten."""
    from app.services.dibos.dibos_mapping import map_wache_status
    from app.services.incident_service import set_wache_status

    changed = False
    for unit in raw_units:
        if unit.get("unitType") != "wache" or unit.get("eventNumber") != event_number:
            continue
        wache_unid = unit.get("unid")
        if not wache_unid or (wache_unid_filter and wache_unid != wache_unid_filter):
            continue
        raw_status = unit.get("currentStatusText")
        status = map_wache_status(raw_status)
        if status is None:
            logger.debug("Unbekannter DIBOS-Wachenstatus %r fuer %s uebersprungen", raw_status, wache_unid)
            continue
        status_at = _parse_dibos_datetime(unit.get("currentStatusTime"), org)
        entry = set_wache_status(
            db, incident, wache_unid, status,
            wache_name=unit.get("unidRfl"), status_text_raw=raw_status, status_at=status_at,
        )
        changed |= entry is not None
    return changed


def _lis_delivers(db: Session, org_id: int, incident) -> bool:
    from app.services.lis.lis_health import lis_delivers_for
    return lis_delivers_for(db, org_id, incident)


def _get_or_create_external_dibos_vehicle(db: Session, org, unit: dict):
    """Externer Platzhalter für eine fremde DIBOS-Einheit. Die DIBOS-unid ist
    dieselbe Kennung wie die LIS-ReferenceId — Schlüssel ist daher lis_reference_id
    + is_external, sodass LIS (lis_sync._get_or_create_external_vehicle) und DIBOS
    denselben Platzhalter teilen statt zwei anzulegen. lis_auto_created markiert ihn
    als automatisch erzeugte Leitstellen-Einheit (Admin-UI read-only)."""
    from app.models.master import VehicleMaster
    unid = unit.get("unid")
    if not unid:
        return None
    vehicle = db.query(VehicleMaster).filter(
        VehicleMaster.dept_id == org.id, VehicleMaster.lis_reference_id == unid,
        VehicleMaster.is_external.is_(True), VehicleMaster.deleted.is_(False),
    ).first()
    if vehicle:
        return vehicle
    name = str(unit.get("unidRfl") or unid)
    lev3 = str(unit.get("lev3") or "")
    prefix = name.split()[0] if name else lev3
    vehicle = VehicleMaster(
        dept_id=org.id, code=name[:30], name=name[:150], type="", is_external=True,
        lis_auto_created=True, lis_reference_id=str(unid)[:60],
        adhoc_org_name=(prefix or "Fremde Organisation (DIBOS)")[:150],
        adhoc_org_short=prefix[:3] or None, active=True,
        display_order=db.query(VehicleMaster).filter(VehicleMaster.dept_id == org.id).count(),
    )
    db.add(vehicle)
    db.flush()
    logger.info("Externes DIBOS-Fahrzeug %r als VehicleMaster %s angelegt", unid, vehicle.id)
    return vehicle


def _sync_dibos_vehicle_location(
    db: Session, org, incident, vehicle, unit: dict, station_coords: set[tuple[float, float]],
) -> None:
    from app.models.major_incident import VehiclePosition
    from app.services.major_incident_service import incident_major_incident_id
    raw_lat, raw_lon = unit.get("latitude"), unit.get("longitude")
    if raw_lat is None or raw_lon is None:
        return
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except (TypeError, ValueError):
        return
    if (round(lat, 5), round(lon, 5)) in station_coords:
        return
    lage_id = incident_major_incident_id(db, incident.id)
    letzte = db.query(VehiclePosition).filter(
        VehiclePosition.incident_id == lage_id, VehiclePosition.vehicle_id == vehicle.id,
    ).order_by(VehiclePosition.received_at.desc()).first()
    if letzte and letzte.source == "manual":
        return
    if letzte and round(letzte.lat, 5) == round(lat, 5) and round(letzte.lon, 5) == round(lon, 5):
        return
    now = datetime.now(UTC)
    db.add(VehiclePosition(incident_id=lage_id, org_id=org.id, vehicle_id=vehicle.id,
                           lat=lat, lon=lon, source="dibos", recorded_at=now, received_at=now))
    db.flush()


def _sync_dibos_vehicle_status(
    db: Session, org, incident, event: dict, raw_units: list[dict], sync_external_units: bool,
) -> bool:
    """Schreibt nur im LIS-Ausfall-Fallback; sonst bliebe LIS nicht autoritativ."""
    from app.models.incident import IncidentColumn, IncidentVehicle
    from app.models.master import VehicleMaster
    from app.services.dibos.dibos_mapping import map_dibos_unit_status
    from app.services.incident_service import _next_display_order, append_card, set_unit_status
    from app.services.lis.lis_health import lis_delivers_for

    key = (org.id, incident.id)
    fallback = not lis_delivers_for(db, org.id, incident)
    if fallback != (key in _fallback_active):
        logger.info("DIBOS-Fallback für Org %s, Einsatz %s %s", org.id, incident.id,
                    "aktiv" if fallback else "deaktiviert")
        if fallback:
            _fallback_active.add(key)
        else:
            _fallback_active.discard(key)
    if not fallback:
        return False
    own_lev3 = event.get("lev3") or next((u.get("lev3") for u in event.get("units") or [] if u.get("lev3")), None)
    event_units = {u.get("unid"): u for u in event.get("units") or [] if u.get("unid")}
    units = [
        {**event_units.get(u.get("unid"), {}), **u}
        for u in raw_units if u.get("eventNumber") == event.get("eventNumber")
    ] + (event.get("units") or [])
    station_coords = {
        (round(float(u["latitude"]), 5), round(float(u["longitude"]), 5)) for u in raw_units
        if u.get("unitType") == "wache" and u.get("latitude") is not None and u.get("longitude") is not None
    }
    changed = False
    seen: set[str] = set()
    for unit in units:
        unid = unit.get("unid")
        if not unid or unid in seen or unit.get("unitType") == "wache":
            continue
        seen.add(unid)
        external = bool(own_lev3 and unit.get("lev3") and unit.get("lev3") != own_lev3)
        vehicle = db.query(VehicleMaster).filter(
            # DIBOS-unid == LIS-ReferenceId (dasselbe Feld, in der Admin-UI gepflegt)
            VehicleMaster.dept_id == org.id, VehicleMaster.lis_reference_id == unid,
            VehicleMaster.deleted.is_(False),
        ).first()
        if not vehicle and external and sync_external_units:
            vehicle = _get_or_create_external_dibos_vehicle(db, org, unit)
        if not vehicle:
            continue
        status = map_dibos_unit_status(unit.get("currentStatusText"))
        iv = db.query(IncidentVehicle).filter(IncidentVehicle.incident_id == incident.id,
            IncidentVehicle.vehicle_master_id == vehicle.id, IncidentVehicle.removed_at.is_(None)).first()
        if not iv and status:
            col = db.query(IncidentColumn).filter_by(incident_id=incident.id, code="active").first()
            if col:
                iv = IncidentVehicle(incident_id=incident.id, column_id=col.id, vehicle_master_id=vehicle.id,
                    display_order=_next_display_order(db, incident.id, col.id), unit_status=status)
                db.add(iv)
                db.flush()
                append_card(db, col.id, "vehicle", iv.id)
                changed = True
        elif iv and status and iv.unit_status != status:
            set_unit_status(db, iv, status)
            changed = True
        _sync_dibos_vehicle_location(db, org, incident, vehicle, unit, station_coords)
    return changed


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


def _enrich_caller(incident, callers: list[dict]) -> bool:
    """Ergänzt fehlende Anrufer-/Melderfelder — überschreibt NIE bereits
    vorhandene Werte (z.B. vom Alarm-Webhook oder manueller Korrektur)."""
    caller = next((item for item in callers if item.get("number")), None)
    if caller is None:
        return False

    changed = False
    if not incident.caller_name and caller.get("name"):
        incident.caller_name = caller["name"]
        changed = True
    if not incident.caller_phone:
        incident.caller_phone = caller["number"]
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


async def _notify_new_dibos_incident(incident_id: int, org_id: int) -> None:
    """Broadcastet und alarmiert einen frisch committeten DIBOS-Einsatz."""
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.services.broadcast import broadcast_org
    from app.services.exercise_guard import darf_extern
    from app.services.incident_notify import notify_incident_created

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if incident is None:
            return
        try:
            await broadcast_org(org_id, {
                "type": "incident_created",
                "incident_id": incident.id,
                "alarm": incident.alarm_type_code,
                "alarm_erlaubt": darf_extern(
                    "ws_alarm", is_exercise=incident.is_exercise, org_id=org_id, db=db
                ),
                "alarm_type_code": incident.alarm_type_code,
                "is_exercise": incident.is_exercise,
                "url": f"/einsatz/{incident.id}/info",
                "title": f"Neuer Einsatz aus DIBOS: {incident.alarm_type_code}",
            })
        except Exception:
            logger.exception("DIBOS-Board-Broadcast für neuen Einsatz %s fehlgeschlagen", incident.id)
        try:
            await notify_incident_created(
                db, incident, org_id=org_id,
                base_url=settings.effective_public_base_url,
                background_tasks=None,
            )
        except Exception:
            logger.exception(
                "Einsatzinfo-Benachrichtigung für DIBOS-Einsatz %s fehlgeschlagen",
                incident.id,
            )
    finally:
        db.close()


async def _geocode_and_match_dibos_incident(incident_id: int, org_id: int) -> None:
    """Folgearbeit erst nach dem Anlage-Commit: BMA kann bereits einen Link
    erzeugt haben; match_incident_background dedupliziert diesen Objekt-Link."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if incident is None or incident.status == "closed":
            return
        if incident.lat is None and incident.lng is None and (incident.address_street or incident.address_city):
            from app.services.geocoding import geocode_address
            geo = await geocode_address(incident.address_street, incident.address_no, incident.address_city)
            if geo:
                incident.lat, incident.lng = geo.lat, geo.lng
                db.commit()
        from app.services.objekt_matching_service import match_incident_background
        await match_incident_background(incident_id)
    except Exception:
        db.rollback()
        logger.exception("Geocoding/Objekt-Matching nach DIBOS-Anlage fehlgeschlagen (Einsatz %s)", incident_id)
    finally:
        db.close()


def enrich_events_for_org(
    org_id: int,
    raw_events: list[dict],
    *,
    raw_public_events: list[dict] | None = None,
    raw_units: list[dict] | None = None,
    wache_unid: str | None = None,
    create_incidents: bool = False,
    loop: asyncio.AbstractEventLoop | None = None,
) -> dict:
    """Reichert aktive Einsätze der Org mit DIBOS-Zusatzinfos an — und legt,
    wenn create_incidents=True (Org-Opt-in OrgDibosConfig.create_incidents),
    für ein Event ohne passenden Einsatz einen neuen an (siehe
    _get_or_create_incident_for_event()).

    Läuft synchron in einer eigenen DB-Session (aus dibos_loop.py per
    asyncio.to_thread aufgerufen) — parallel zum bestehenden LIS/IPR-Sync, ohne
    dessen Matching/Dedup zu berühren. Ein Fehler bricht nur den eigenen
    Anreicherungs-Durchlauf ab (Rollback + Log), nie den DIBOS-Poll selbst.

    Neue Einsätze werden nach vollständiger Anreicherung sofort committet. Ihre
    Benachrichtigung wird per ``run_coroutine_threadsafe`` auf dem übergebenen
    Haupt-Event-Loop eingeplant; die Coroutine verwendet eine eigene DB-Session.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import FireDept

    db = SessionLocal()
    set_tenant_context(db, None)
    changed_ids: list[int] = []
    vehicle_changed_ids: list[int] = []
    rsvp_changed_ids: list[int] = []
    created_ids: list[int] = []
    ok = False
    closed_ids: list[int] = []
    objekt_match_ids: list[int] = []
    created_match_ids: list[int] = []
    try:
        org = db.get(FireDept, org_id)
        from app.models.dibos import OrgDibosConfig
        dibos_config = db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == org_id).first()
        for event in parse_events(raw_events):
            event_number = event.get("eventNumber")
            incident = _find_active_incident_by_event_number(db, org_id, event_number)
            if not incident and event_number:
                # Auch geschlossene Treffer laden: ein erneut aktives Event kann
                # genau den vorherigen DIBOS-Auto-Close wieder aufheben.
                from app.models.incident import Incident
                incident = db.query(Incident).filter(
                    Incident.primary_org_id == org_id,
                    Incident.lis_operation_number == event_number,
                ).first()
            just_created = False
            if not incident and create_incidents:
                result = _get_or_create_incident_for_event(db, org, org_id, event)
                if result is not None:
                    incident, just_created = result
            if not incident:
                continue
            if (
                incident.status == "closed" and incident.closed_via_lis_auto
                and not incident.lis_auto_close_locked and not event.get("closed")
                and not _lis_delivers(db, org_id, incident)
            ):
                # Nur im LIS-Ausfall: Liefert LIS, ist es für Abschluss/Wiedereröffnung
                # autoritativ. LIS schließt, sobald die eigene Beteiligung endet, während
                # das DIBOS-Event oft noch offen ist — ein Reopen hier würde den Einsatz
                # dann wieder öffnen und dauerhaft sperren (nur noch manueller Abschluss).
                from app.services.incident_service import reopen_incident
                reopen_incident(db, incident, user_id=None)
                incident.lis_auto_close_locked = True
                db.flush()
                logger.info("Einsatz %s durch wieder aktives DIBOS-Event wiedereröffnet und gesperrt", incident.id)
            if incident.status == "closed":
                continue
            changed = False
            changed |= _enrich_address(incident, event.get("location") or {})
            changed |= _enrich_caller(incident, event.get("callers") or [])
            changed |= _enrich_metadata(incident, event)
            if just_created and not event.get("closed"):
                # Sofort committen + alarmieren: SMS/Teams/Push brauchen nur die
                # Felder oben (Stichwort, Adresse, Meldung) und dürfen nicht auf
                # Kommentar-Import (eine Query je Kommentar), Wachenstatus,
                # BMA-Objekt-Matching und Zu-/Absagen warten. Diese laufen danach
                # auf dem bereits committeten Einsatz weiter.
                # ID vor dem Commit sichern: expire_on_commit invalidiert auch `incident`
                # und `org`; spätere Zugriffe werden bei Bedarf automatisch nachgeladen.
                incident_id = incident.id
                db.commit()
                created_ids.append(incident_id)
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        _notify_new_dibos_incident(incident_id, org_id), loop
                    )
            changed |= _sync_dibos_comments(db, org_id, incident, event.get("comments") or [])
            if raw_units is not None and isinstance(event_number, str):
                changed |= _sync_wache_status(
                    db, org, incident, event_number, raw_units, wache_unid
                )
                vehicle_changed = _sync_dibos_vehicle_status(
                    db, org, incident, event, raw_units,
                    bool(dibos_config and dibos_config.sync_external_units),
                )
                changed |= vehicle_changed
                if vehicle_changed:
                    vehicle_changed_ids.append(incident.id)
            if event.get("bmaNo"):
                objekt_match = _match_objekt_by_dibos_bma(db, incident)
                changed |= objekt_match
                if objekt_match:
                    objekt_match_ids.append(incident.id)
            rsvp_changed = _sync_person_responses(db, org_id, org, incident, event.get("personResponses") or [])
            changed |= rsvp_changed
            if rsvp_changed:
                rsvp_changed_ids.append(incident.id)
            if just_created and event.get("closed"):
                # Das Event war bei Anlage bereits abgeschlossen (z.B. Poll unmittelbar
                # vor Ende eingetroffen) — zur Dokumentation anlegen, aber KEINE
                # Alarmierung mehr auslösen, direkt schließen (Muster: lis_sync.py).
                from app.services.incident_service import close_incident
                close_incident(db, incident, user_id=None, auto_closed_by_lis=True)
                db.flush()
                closed_ids.append(incident.id)
                logger.info(
                    "Einsatz %s aus bereits beendetem DIBOS-Event %s angelegt (Org %s) — "
                    "keine Alarmierung, direkt geschlossen",
                    incident.id, event.get("eventNumber"), org_id,
                )
            if changed:
                db.flush()
                changed_ids.append(incident.id)
            if just_created and not event.get("closed"):
                # Folgearbeit (Kommentare, Objekt, Zu-/Absagen) des bereits oben
                # committeten neuen Einsatzes sichern.
                db.commit()
                created_match_ids.append(incident.id)
        for event in parse_events(raw_public_events or []):
            if not event.get("closed"):
                continue
            incident = _find_active_incident_by_event_number(
                db, org_id, event.get("eventNumber")
            )
            if incident is None:
                continue
            if incident.lis_auto_close_locked:
                continue
            from app.services.incident_service import close_incident
            close_incident(db, incident, user_id=None, auto_closed_by_lis=True)
            db.flush()
            closed_ids.append(incident.id)
            logger.info(
                "Einsatz %s durch abgeschlossenes DIBOS-Event %s automatisch geschlossen (Org %s)",
                incident.id, event.get("eventNumber"), org_id,
            )
        db.commit()
        ok = True
    except Exception:
        db.rollback()
        logger.exception("DIBOS-Einsatzanreicherung für Org %s fehlgeschlagen", org_id)
    finally:
        db.close()
    return {
        "changed_ids": changed_ids,
        "vehicle_changed_ids": vehicle_changed_ids,
        "rsvp_changed_ids": rsvp_changed_ids,
        "created_ids": created_ids,
        "closed_ids": closed_ids,
        "objekt_match_ids": objekt_match_ids,
        "created_match_ids": created_match_ids,
        # False nach Rollback: der Poll-Loop merkt sich den Datenstand dann NICHT
        # als verarbeitet und versucht es im nächsten Zyklus erneut.
        "ok": ok,
    }


async def enrich_and_broadcast(
    org_id: int,
    raw_events: list[dict],
    *,
    raw_public_events: list[dict] | None = None,
    raw_units: list[dict] | None = None,
    wache_unid: str | None = None,
    create_incidents: bool = False,
) -> bool:
    """Reichert an (in einem Thread, da synchron/DB-blockierend) und broadcastet
    pro tatsächlich geänderten Einsatz — Fehler dürfen den aufrufenden Poll nie
    abbrechen. Einziger Aufrufer ist dibos_loop.py::_check_org() (der Diagnose-
    Trace in dibos_capture.py schreibt bewusst nichts). Gibt True zurück, wenn der
    Durchlauf ohne Rollback durchlief.

    create_incidents=True (Org-Opt-in) legt zusätzlich neue Einsätze an. Sobald
    ein neuer Einsatz mit seinen Stammdaten committet ist (VOR Kommentaren,
    Objekt-Matching, Zu-/Absagen), plant der Worker-Thread seine Benachrichtigung
    auf diesem Event-Loop ein. Autodruck, Objekt-Einsatzinfo und WordPress-Bericht
    laufen als Hintergrund-Tasks, damit sie den nächsten Poll nicht verzögern.

    Drei Broadcast-/Benachrichtigungs-Typen: "dibos_sync" (voller Board-Reload)
    für jeden geänderten Einsatz, "rsvp:changed" (nur Zu-/Absage-Widget neu
    laden, siehe app.js) für Einsätze mit neuen Personenrückmeldungen, sowie
    die Einsatzinfo-Benachrichtigung für neu angelegte Einsätze.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            enrich_events_for_org, org_id, raw_events, raw_public_events=raw_public_events,
            raw_units=raw_units,
            wache_unid=wache_unid, create_incidents=create_incidents, loop=loop,
        )
    except Exception:
        logger.exception("DIBOS-Einsatzanreicherung fehlgeschlagen (Org %s)", org_id)
        return False
    changed_ids = result.get("changed_ids") or []
    vehicle_changed_ids = result.get("vehicle_changed_ids") or []
    rsvp_changed_ids = result.get("rsvp_changed_ids") or []
    closed_ids = result.get("closed_ids") or []
    objekt_match_ids = result.get("objekt_match_ids") or []
    created_ids = result.get("created_ids") or []
    created_match_ids = result.get("created_match_ids") or []
    from app.services.objekt_kontakt_notify import dispatch_objekt_einsatzinfo
    from app.services.print_dispatcher import autoprint_incident_background
    for incident_id in created_ids:
        _start_background(autoprint_incident_background(incident_id), "DIBOS-Auto-Druck", incident_id)
    for incident_id in created_match_ids:
        _start_background(
            _geocode_and_match_dibos_incident(incident_id, org_id),
            "DIBOS-Geocoding/Objekt-Matching", incident_id,
        )
    for incident_id in objekt_match_ids:
        _start_background(
            dispatch_objekt_einsatzinfo(incident_id), "DIBOS-Objekt-Einsatzinfo", incident_id
        )
    from app.services.broadcast import manager
    if closed_ids:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.incident import Incident

        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            for incident_id in closed_ids:
                incident = db.get(Incident, incident_id)
                if incident is None:
                    continue
                _start_background(
                    _post_incident_report(incident_id), "DIBOS-Auto-Close: WordPress-Bericht", incident_id
                )
                try:
                    await manager.broadcast(incident_id, {"type": "incident_closed"})
                except Exception:
                    logger.exception(
                        "DIBOS-Auto-Close: Broadcast fehlgeschlagen (Einsatz %s)",
                        incident.id,
                    )
                try:
                    from app.services.incident_live_notify import notify_incident_live
                    await notify_incident_live(
                        db, incident, org_id=org_id, reason="closed", background_tasks=None,
                    )
                except Exception:
                    logger.exception("DIBOS-Auto-Close: Live-Push fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            db.close()
    ok = bool(result.get("ok"))
    if not changed_ids and not rsvp_changed_ids:
        return ok
    for incident_id in changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "dibos_sync"})
        except Exception:
            logger.exception("DIBOS-Broadcast für Einsatz %s fehlgeschlagen", incident_id)
    for incident_id in vehicle_changed_ids:
        # Der Live-Push liest in eigener Session, daher erst nach dem Worker-Commit.
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.incident import Incident
        from app.services.incident_live_notify import notify_incident_live
        live_db = SessionLocal()
        set_tenant_context(live_db, None)
        try:
            incident = live_db.get(Incident, incident_id)
            if incident:
                await notify_incident_live(
                    live_db, incident, org_id=org_id, reason="unit_status", background_tasks=None,
                )
        except Exception:
            logger.exception("DIBOS-Live-Push (Fahrzeugstatus) fehlgeschlagen (Einsatz %s)", incident_id)
        finally:
            live_db.close()
    for incident_id in rsvp_changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "rsvp:changed"})
        except Exception:
            logger.exception("DIBOS-RSVP-Broadcast für Einsatz %s fehlgeschlagen", incident_id)
    return ok


# Starke Referenzen auf laufende Hintergrund-Tasks — asyncio hält Tasks nur
# schwach, ohne diese Menge könnte ein Task mitten im Lauf eingesammelt werden.
_background_tasks: set[asyncio.Task] = set()


def _start_background(coro, label: str, incident_id: int) -> None:
    """Startet langsame Folgearbeit (Druck, Mail/SMS an Objektkontakte, WordPress)
    entkoppelt vom Poll-Loop; Fehler werden nur geloggt."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        _background_tasks.discard(finished)
        if not finished.cancelled() and finished.exception() is not None:
            logger.error(
                "%s fehlgeschlagen (Einsatz %s)", label, incident_id, exc_info=finished.exception(),
            )

    task.add_done_callback(_done)


async def _post_incident_report(incident_id: int) -> None:
    """WordPress-Bericht als Hintergrund-Task — mit eigener Session, da die des
    Aufrufers beim Start des Tasks bereits geschlossen sein kann."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.services.wordpress_report_service import post_incident_report

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if incident is not None:
            await post_incident_report(db, incident)
    finally:
        db.close()
