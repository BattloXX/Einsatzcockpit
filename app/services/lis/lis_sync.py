"""Sync-Orchestrierung je Organisation: Einsätze verbinden/anlegen, Fahrzeugstatus,
Meldungen, Zu-/Absagen (Protokoll), Dokumente, Backfill.

Aufgerufen aus lis_loop.py (Hintergrund-Poll) — kann aber auch direkt (z.B. im
"Verbindung testen"-Button oder in Tests) für eine einzelne Org aufgerufen werden.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.timezones import org_tz
from app.models.incident import IncidentColumn, IncidentLog, IncidentVehicle, Message, Task
from app.models.lis import LisSyncedObject, OrgLisConfig
from app.models.major_incident import IncidentSite, VehiclePosition
from app.models.master import FireDept, VehicleMaster
from app.services.incident_service import _next_display_order, append_card, create_incident, set_unit_status
from app.services.lis.lis_client import LisClient, LisClientError
from app.services.lis.lis_geo import lis_unit_coords_to_wgs84
from app.services.lis.lis_mapping import (
    is_exercise_operation,
    is_lis_auftrag,
    map_stichwort,
    map_unit_status,
    parse_person_response,
    unit_status_to_lis_prefix,
)
from app.services.lis.lis_matching import find_matching_incident

logger = logging.getLogger("einsatzleiter.lis.sync")

_BACKFILL_INTERVAL = timedelta(hours=24)


# ── Kleine Hilfsfunktionen ──────────────────────────────────────────────────
def _op_field(op: dict, *path: str):
    cur = op
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_operation_datetime(value: str | None, org: FireDept | None) -> datetime | None:
    """Parst einen LIS-Zeitstempel-String zu naive UTC. .NET-Default (0001-01-01) → None.

    Enthält der String eine Zeitzone/Offset, wird diese respektiert. Ohne Offset wird
    der Wert als Org-Lokalzeit interpretiert (siehe app.core.timezones.org_tz).
    """
    if not value or value.startswith("0001-01-01"):
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
    return dt.replace(tzinfo=org_tz(org)).astimezone(UTC).replace(tzinfo=None)


def _already_synced(db: Session, org_id: int, obj_type: str, lis_id: str) -> bool:
    return (
        db.query(LisSyncedObject.id)
        .filter(
            LisSyncedObject.org_id == org_id,
            LisSyncedObject.obj_type == obj_type,
            LisSyncedObject.lis_id == lis_id,
        )
        .first()
        is not None
    )


def _incident_major_incident_id(db: Session, incident_id: int) -> int | None:
    """Liefert die Lage-ID (Großschadenslage), falls dieser Einsatz dort als
    Einsatzstelle geführt wird — sonst None."""
    site = (
        db.query(IncidentSite.major_incident_id)
        .filter(IncidentSite.incident_id == incident_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    return site[0] if site else None


def _incident_belongs_to_major_incident(db: Session, incident_id: int) -> bool:
    """True wenn der Einsatz bereits als Einsatzstelle in eine Großschadenslage
    übernommen wurde — dort werden Einsätze nur zusammengefasst, keine Meldungs-Cards."""
    return _incident_major_incident_id(db, incident_id) is not None


def _parse_operation_coords(op: dict, address: dict) -> tuple[float | None, float | None]:
    """Einsatz-Koordinaten aus der LIS-Operation (projiziertes GMSC-System) → WGS84.

    LIS liefert die Einsatzposition als Operation.LocationX/LocationY (siehe echten
    Mitschnitt: `<a:LocationX>105669</a:LocationX><a:LocationY>257241</a:LocationY>`) im
    selben ganzzahligen System wie die Fahrzeugpositionen — daher dieselbe Umrechnung
    (lis_unit_coords_to_wgs84). Sind die Operation-Werte leer, wird ersatzweise die
    Adress-Ebene (Address.LocationX/Y) probiert. Ohne belastbare Werte → (None, None),
    dann greift der Adress-Geocoding-Fallback in sync_operation().
    """
    for src in (op, address):
        loc_x, loc_y = src.get("LocationX"), src.get("LocationY")
        if loc_x is None or loc_y is None:
            continue
        try:
            coords = lis_unit_coords_to_wgs84(float(loc_x), float(loc_y))
        except (TypeError, ValueError):
            coords = None
        if coords:
            return coords
    return None, None


def _parse_operation(op: dict, org: FireDept | None) -> dict:
    address = op.get("Address") or {}
    type_obj = op.get("Type") or {}
    reason = op.get("Description") or op.get("Name")
    started_raw = op.get("BeginTime") or op.get("CreationTime")
    lat, lng = _parse_operation_coords(op, address)
    ended_at = _parse_operation_datetime(op.get("EndTime"), org)
    return {
        "lis_operation_id": op.get("Id"),
        "lis_operation_number": op.get("Number"),
        "reason": reason,
        "street": address.get("Street"),
        "house_no": address.get("Housenumber"),
        "city": address.get("Community"),
        "lat": lat,
        "lng": lng,
        "report_text": reason,
        "alarm_type_code": map_stichwort(type_obj.get("Code") or type_obj.get("Type")),
        "started_at": _parse_operation_datetime(started_raw, org),
        "is_exercise": is_exercise_operation(type_obj),
        # EndTime gesetzt → Operation in LIS bereits beendet (z.B. Backfill historischer
        # Einsätze oder zwischen zwei Polls geschlossen). Steuert, ob noch alarmiert wird.
        "ended_at": ended_at,
        "is_closed": ended_at is not None,
    }


# ── Einsatz verbinden/anlegen ────────────────────────────────────────────────
def _get_or_link_incident(db: Session, org: FireDept, parsed: dict):
    """Gibt (incident, created) zurück. created=True, wenn der Einsatz zuerst über
    LIS geliefert wurde (kein passender API-Einsatz vorhanden war)."""
    from app.models.incident import Incident

    existing = (
        db.query(Incident)
        .filter(Incident.primary_org_id == org.id, Incident.lis_operation_id == parsed["lis_operation_id"])
        .first()
    )
    if existing:
        # Die Operation ist wieder aktiv (sonst würde sync_operation() für sie nicht laufen) —
        # war der Einsatz nur durch unseren eigenen Auto-Close geschlossen (nicht manuell),
        # wiedereröffnen. lis_auto_close_locked verhindert danach jeden weiteren automatischen
        # Abschluss dieses Einsatzes — nur noch ein manueller Abschluss im Einsatzcockpit zählt.
        # NICHT wiedereröffnen, wenn die Operation selbst bereits beendet ist (Backfill
        # historischer/geschlossener Operationen liefert diese Operation mit gesetzter EndTime).
        if (
            existing.status == "closed"
            and existing.closed_via_lis_auto
            and not existing.lis_auto_close_locked
            and not parsed.get("is_closed")
        ):
            from app.services.incident_service import reopen_incident
            reopen_incident(db, existing, user_id=None)
            existing.lis_auto_close_locked = True
            db.flush()
            logger.info(
                "Einsatz %s automatisch wiedereröffnet (LIS-Operation %s wieder aktiv, Org %s) — "
                "künftig nur noch manueller Abschluss möglich",
                existing.id, parsed["lis_operation_id"], org.id,
            )
        return existing, False

    match = find_matching_incident(
        db, org.id,
        alarm_type_code=parsed["alarm_type_code"],
        reason=parsed["reason"],
        street=parsed["street"],
        city=parsed["city"],
        started_at=parsed["started_at"],
    )
    if match:
        match.lis_operation_id = parsed["lis_operation_id"]
        match.lis_operation_number = parsed["lis_operation_number"]
        db.flush()
        logger.info(
            "LIS-Operation %s mit vorhandenem Einsatz %s verknüpft (Org %s)",
            parsed["lis_operation_id"], match.id, org.id,
        )
        return match, False

    # Koordinaten direkt aus der LIS-Operation übernehmen, falls mitgeliefert — dann
    # KEINE nachgelagerte Adressvalidierung/Geocoding nötig (siehe sync_operation()).
    incident = create_incident(
        db,
        alarm_type_code=parsed["alarm_type_code"],
        started_at=parsed["started_at"],
        is_exercise=parsed["is_exercise"],
        address_street=parsed["street"],
        address_no=parsed["house_no"],
        address_city=parsed["city"],
        lat=parsed.get("lat"),
        lng=parsed.get("lng"),
        report_text=parsed["report_text"],
        reason=parsed["reason"],
        primary_org_id=org.id,
    )
    incident.lis_operation_id = parsed["lis_operation_id"]
    incident.lis_operation_number = parsed["lis_operation_number"]
    db.flush()
    if parsed.get("lat") is not None and parsed.get("lng") is not None:
        logger.info(
            "Einsatz %s aus LIS-Operation %s neu angelegt (Org %s) — Koordinaten aus LIS "
            "übernommen (lat=%.5f, lon=%.5f), kein Geocoding nötig",
            incident.id, parsed["lis_operation_id"], org.id, parsed["lat"], parsed["lng"],
        )
    else:
        logger.info(
            "Einsatz %s aus LIS-Operation %s neu angelegt (Org %s, zuerst über LIS geliefert) — "
            "keine Koordinaten in der LIS-Operation, Adress-Geocoding folgt",
            incident.id, parsed["lis_operation_id"], org.id,
        )
    return incident, True


# ── Fahrzeugstatus (S4/S5) ───────────────────────────────────────────────────
def _sync_vehicle_status(db: Session, org: FireDept, incident, units: list[dict]) -> bool:
    lage_id = _incident_major_incident_id(db, incident.id)
    changed = False

    for unit in units:
        ref_id = unit.get("ReferenceId")
        if not ref_id:
            continue

        vehicle_master = (
            db.query(VehicleMaster)
            .filter(VehicleMaster.dept_id == org.id, VehicleMaster.lis_reference_id == ref_id)
            .first()
        )
        if not vehicle_master:
            logger.warning(
                "LIS-Einheit %r (Org %s) hat keine lis_reference_id-Zuordnung — Status/Position wird ignoriert",
                ref_id, org.id,
            )
            continue

        operation_unit_id = unit.get("Id")
        status_label = _op_field(unit, "OperationUnitStatusType", "Label")
        mapped_status = map_unit_status(status_label) if isinstance(status_label, str) else None

        incident_vehicle = (
            db.query(IncidentVehicle)
            .filter(
                IncidentVehicle.incident_id == incident.id,
                IncidentVehicle.vehicle_master_id == vehicle_master.id,
                IncidentVehicle.removed_at.is_(None),
            )
            .first()
        )
        if not incident_vehicle:
            # Ohne "Disponiert"-Spalte legt die Ausrückordnung mit aktiver LIS-Anbindung
            # keine Fahrzeuge mehr vor (siehe _populate_vehicles()) — das Fahrzeug erscheint
            # im Board erst hier, sobald LIS erstmals S4/S5 meldet (sonst bleibt es unsichtbar,
            # bis es manuell hinzugefügt wird).
            if not mapped_status:
                continue
            active_col = (
                db.query(IncidentColumn)
                .filter_by(incident_id=incident.id, code="active")
                .first()
            )
            if not active_col:
                continue
            incident_vehicle = IncidentVehicle(
                incident_id=incident.id,
                column_id=active_col.id,
                vehicle_master_id=vehicle_master.id,
                display_order=_next_display_order(db, incident.id, active_col.id),
                unit_status=mapped_status,
                lis_operation_unit_id=operation_unit_id,
            )
            db.add(incident_vehicle)
            db.flush()
            append_card(db, active_col.id, "vehicle", incident_vehicle.id)
            changed = True
            logger.info(
                "Fahrzeug %s durch LIS-Status %s neu auf Einsatz %s aufgenommen",
                vehicle_master.id, mapped_status, incident.id,
            )
        else:
            if operation_unit_id and incident_vehicle.lis_operation_unit_id != operation_unit_id:
                incident_vehicle.lis_operation_unit_id = operation_unit_id
            if mapped_status and incident_vehicle.unit_status != mapped_status:
                try:
                    set_unit_status(db, incident_vehicle, mapped_status)
                    changed = True
                except ValueError:
                    logger.warning("Ungültiger gemappter Status %r für Fahrzeug %s", mapped_status, vehicle_master.id)

        _sync_vehicle_location(db, org, incident, vehicle_master, unit, lage_id)

    return changed


# ── Fahrzeugposition (nur wenn LIS Koordinaten liefert, i.d.R. Status S5) ────
def _sync_vehicle_location(
    db: Session, org: FireDept, incident, vehicle_master: VehicleMaster, unit: dict, lage_id: int | None,
) -> None:
    """Schreibt eine LIS-Position in dieselbe Positionshistorie (VehiclePosition),
    in die auch die App per GPS schreibt — nicht in ein separates Feld. Auf der
    Lagekarte gewinnt je Fahrzeug automatisch die zuletzt empfangene Position,
    unabhängig davon ob sie von der App oder vom LIS stammt."""
    loc_x, loc_y = unit.get("LocationX"), unit.get("LocationY")
    if loc_x is None or loc_y is None:
        return
    try:
        coords = lis_unit_coords_to_wgs84(float(loc_x), float(loc_y))
    except (TypeError, ValueError):
        coords = None
    if not coords:
        return

    lat, lon = coords
    now = datetime.now(UTC)
    db.add(VehiclePosition(
        incident_id=lage_id,
        org_id=org.id,
        vehicle_id=vehicle_master.id,
        lat=lat,
        lon=lon,
        source="lis",
        recorded_at=now,
        received_at=now,
    ))
    db.flush()
    logger.info(
        "LIS-Position für Fahrzeug %s übernommen (Einsatz %s, Lage %s)", vehicle_master.id, incident.id, lage_id,
    )


# ── Meldungen (Type.Type=="JOURNAL" → Message-Cards, nur normaler Incident) ──
def _sync_messages(db: Session, org: FireDept, incident, tasks: list[dict]) -> bool:
    if _incident_belongs_to_major_incident(db, incident.id):
        return False

    messages_col = (
        db.query(IncidentColumn)
        .filter(IncidentColumn.incident_id == incident.id, IncidentColumn.code == "messages")
        .first()
    )

    changed = False
    for task in tasks:
        task_type = _op_field(task, "Type", "Type")
        if task_type == "UNITSTATUSHISTORY":
            continue  # Fahrzeug-Statusverlauf, siehe _sync_vehicle_status() — keine Meldung
        if is_lis_auftrag(task_type):
            continue  # separat behandelt (siehe _sync_tasks) — echter LIS-Auftrag, keine Meldung
        lis_task_id = task.get("Id")
        if not lis_task_id:
            continue
        description = task.get("Description") or ""

        existing = (
            db.query(Message)
            .filter(Message.incident_id == incident.id, Message.lis_task_id == lis_task_id)
            .first()
        )
        if existing:
            if description and existing.detail != description:
                existing.detail = description
                db.flush()
                changed = True
            continue

        number = task.get("Number")
        db.add(Message(
            incident_id=incident.id,
            column_id=messages_col.id if messages_col else None,
            title=f"LIS: {number}" if number else "LIS: Meldung",
            detail=description or None,
            author_name=task.get("CreatedBy") or "LIS",
            lis_task_id=lis_task_id,
        ))
        db.flush()
        changed = True
        logger.info("Meldung aus LIS-Task %s auf Einsatz %s übernommen", lis_task_id, incident.id)

    return changed


# ── Aufträge (Type.Type=="TASK" → Task-Cards, nur normaler Incident) ────────
def _sync_tasks(db: Session, org: FireDept, incident, tasks: list[dict]) -> bool:
    """Echte LIS-Aufträge ("an eine Stabsfunktion zuteilen") landen im eigenen
    Aufträge-Board (Task-Modell), NICHT als Message — analog zur manuellen
    Trennung Meldungen/Aufträge im UI (IncidentColumn "messages" vs. "tasks").
    """
    if _incident_belongs_to_major_incident(db, incident.id):
        return False

    tasks_col = (
        db.query(IncidentColumn)
        .filter(IncidentColumn.incident_id == incident.id, IncidentColumn.code == "tasks")
        .first()
    )

    changed = False
    for task in tasks:
        if not is_lis_auftrag(_op_field(task, "Type", "Type")):
            continue
        lis_task_id = task.get("Id")
        if not lis_task_id:
            continue
        description = task.get("Description") or ""
        due_at = _parse_operation_datetime(task.get("DeadlineTime"), org)

        existing = (
            db.query(Task)
            .filter(Task.incident_id == incident.id, Task.lis_task_id == lis_task_id)
            .first()
        )
        if existing:
            row_changed = False
            if description and existing.detail != description:
                existing.detail = description
                row_changed = True
            if due_at != existing.due_at:
                existing.due_at = due_at
                row_changed = True
            if row_changed:
                db.flush()
                changed = True
            continue

        number = task.get("Number")
        db.add(Task(
            incident_id=incident.id,
            column_id=tasks_col.id if tasks_col else None,
            title=f"LIS: {number}" if number else "LIS: Auftrag",
            detail=description or None,
            due_at=due_at,
            source="lis",
            lis_task_id=lis_task_id,
        ))
        db.flush()
        changed = True
        logger.info("Auftrag aus LIS-Task %s auf Einsatz %s übernommen", lis_task_id, incident.id)

    return changed


# ── Zu-/Absagen (Mannschaft) → Einsatz-Verlauf ───────────────────────────────
def _sync_person_responses(db: Session, org: FireDept, incident, tasks: list[dict]) -> bool:
    changed = False
    for task in tasks:
        task_type = _op_field(task, "Type", "Type")
        lis_task_id = task.get("Id")
        if not lis_task_id:
            continue
        parsed = parse_person_response(task.get("Description"), task_type)
        if not parsed:
            continue
        if _already_synced(db, org.id, "task_response", lis_task_id):
            continue

        person = task.get("CreatedBy") or parsed["person"]
        bits = [f"{person}: {parsed['status']}"]
        if parsed["role"]:
            bits.append(f"({parsed['role']})")
        if parsed["arrival_time"]:
            bits.append(f"– Ankunft {parsed['arrival_time']} Uhr")

        db.add(IncidentLog(
            incident_id=incident.id,
            text="LIS: " + " ".join(bits),
            author_name="LIS",
            level="info",
            entity_type="lis_task",
        ))
        db.add(LisSyncedObject(
            org_id=org.id, obj_type="task_response", lis_id=lis_task_id, incident_id=incident.id,
        ))
        db.flush()
        changed = True
        logger.info(
            "Zu-/Absage aus LIS-Task %s auf Einsatz %s protokolliert (%s: %s)",
            lis_task_id, incident.id, person, parsed["status"],
        )

    return changed


# ── Dokumente (MTOM-Download) ────────────────────────────────────────────────
async def _store_lis_document(
    db: Session, incident, raw: bytes, filename: str, org_id: int, title: str,
) -> None:
    import io
    from types import SimpleNamespace

    from fastapi import UploadFile

    from app.services import media_service

    msgs_col = (
        db.query(IncidentColumn)
        .filter(IncidentColumn.incident_id == incident.id, IncidentColumn.code == "messages")
        .first()
    )
    doc_message = Message(
        incident_id=incident.id,
        column_id=msgs_col.id if msgs_col else None,
        title=title,
        author_name="LIS",
    )
    db.add(doc_message)
    db.flush()

    upload = UploadFile(filename=filename, file=io.BytesIO(raw))
    system_user = SimpleNamespace(id=None)  # kein handelnder Benutzer bei Hintergrund-Sync
    await media_service.store_upload_for_message(upload, doc_message, system_user, db, org_id=org_id)  # type: ignore[arg-type]


async def _sync_documents(db: Session, org: FireDept, incident, client: LisClient, operation_id: str) -> bool:
    try:
        documents = await client.get_documents_by_operation_id(operation_id)
    except LisClientError:
        logger.exception(
            "LIS-Dokumente für Operation %s (Org %s) konnten nicht geladen werden", operation_id, org.id,
        )
        return False

    changed = False
    for doc in documents:
        doc_id = doc.get("Id")
        if not doc_id or _already_synced(db, org.id, "document", doc_id):
            continue
        try:
            raw = await client.download_document(doc_id, entity=doc.get("Entity") or "PR_OPERATION")
        except LisClientError:
            logger.exception("LIS-Dokument %s (Einsatz %s) konnte nicht geladen werden", doc_id, incident.id)
            continue

        # Anzeigename: NIEMALS die rohe Dokument-GUID (die landete früher als
        # "Dokument: <guid>.pdf" im Kartentitel). Fallback-Kette: Name → DokTyp →
        # generisch. Die GUID wird nur intern (Dedup/Ablage) verwendet.
        name = (doc.get("Name") or "").strip()
        doc_type = (doc.get("DocumentType") or "").strip()
        ext = (doc.get("FileExtension") or "").lstrip(".")
        display = name or doc_type or "LIS-Dokument"
        # Dateiname für die Ablage – lesbar, ohne GUID; nur wenn gar nichts da ist,
        # ein kurzer Kürzel-Suffix zur Eindeutigkeit (nicht im sichtbaren Titel).
        basis = name or doc_type or f"LIS-Dokument-{str(doc_id)[:8]}"
        filename = f"{basis}.{ext}" if ext else basis
        title = f"Dokument: {display}" + (f" ({ext.upper()})" if ext and not name else "")

        try:
            await _store_lis_document(db, incident, raw, filename, org.id, title)
        except Exception:
            logger.exception("LIS-Dokument %s (Einsatz %s) konnte nicht gespeichert werden", doc_id, incident.id)
            continue

        db.add(LisSyncedObject(org_id=org.id, obj_type="document", lis_id=doc_id, incident_id=incident.id))
        db.flush()
        changed = True
        logger.info("Dokument %s aus LIS auf Einsatz %s übernommen", filename, incident.id)

    return changed


# ── Einsätze schließen, deren Operation in LIS nicht mehr aktiv ist ─────────
async def _close_incidents_missing_from_lis(
    db: Session, org: FireDept, active_operation_ids: set[str],
) -> None:
    """GetOperationsInRange liefert keinen eigenen Status-/Closed-Flag pro
    Operation — das einzige verfügbare Signal für "in LIS abgeschlossen" ist,
    dass die Operation nicht mehr im ActiveParticipation-Ergebnis auftaucht.
    Schließt daher alle noch aktiven, über LIS angelegten/verknüpften Einsätze
    dieser Org, deren lis_operation_id nicht mehr in der aktuellen aktiven Menge
    ist (siehe sync_organization())."""
    from app.models.incident import Incident
    from app.services.broadcast import manager
    from app.services.incident_service import close_incident

    stale = (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org.id,
            Incident.status == "active",
            Incident.lis_operation_id.isnot(None),
            Incident.lis_operation_id.notin_(active_operation_ids),
            Incident.lis_auto_close_locked.is_(False),
        )
        .all()
    )
    for incident in stale:
        close_incident(db, incident, user_id=None, auto_closed_by_lis=True)
        db.commit()
        logger.info(
            "Einsatz %s automatisch geschlossen (LIS-Operation %s nicht mehr aktiv, Org %s)",
            incident.id, incident.lis_operation_id, org.id,
        )
        try:
            await manager.broadcast(incident.id, {"type": "incident_closed"})
        except Exception:
            logger.exception("LIS-Auto-Close: Broadcast für Einsatz %s fehlgeschlagen", incident.id)


# ── Ein Operation-Objekt vollständig verarbeiten ─────────────────────────────
async def sync_operation(db: Session, org: FireDept, config: OrgLisConfig, client: LisClient, op: dict) -> None:
    parsed = _parse_operation(op, org)
    if not parsed["lis_operation_id"]:
        return

    incident, created = _get_or_link_incident(db, org, parsed)

    try:
        # Experiment 2 (2026-07-05, nach Fehlschlag von Experiment 1 im Live-Test):
        # SelectOperation zusätzlich mit der KONKRETEN operationId unmittelbar vor
        # GetTasks aufrufen (bisher nur einmal pro Sync-Zyklus mit operationId=nil in
        # sync_organization()). Siehe select_operation()-Docstring in lis_client.py.
        await client.select_operation(config.organization_id, operation_id=parsed["lis_operation_id"])
        tasks = await client.get_tasks(parsed["lis_operation_id"])
    except LisClientError:
        logger.exception(
            "LIS-Tasks für Operation %s (Org %s) fehlgeschlagen", parsed["lis_operation_id"], org.id,
        )
        tasks = []

    messages_changed = _sync_messages(db, org, incident, tasks)
    tasks_changed = _sync_tasks(db, org, incident, tasks)
    responses_changed = _sync_person_responses(db, org, incident, tasks)

    try:
        units = await client.get_operation_units(config.organization_id, parsed["lis_operation_id"])
    except LisClientError:
        logger.exception(
            "LIS-Einheiten für Operation %s (Org %s) fehlgeschlagen", parsed["lis_operation_id"], org.id,
        )
        units = []
    vehicles_changed = _sync_vehicle_status(db, org, incident, units)

    documents_changed = await _sync_documents(db, org, incident, client, parsed["lis_operation_id"])

    # Board live aktualisieren, sobald LIS neue Meldungen/Aufträge/Fahrzeugstatus/Zu-Absagen
    # liefert — sonst sieht man neue LIS-Daten erst nach manuellem F5 (Board hat sonst keinen
    # Trigger, weil der Sync außerhalb jedes Requests im Hintergrund-Poll läuft).
    if not created and (
        messages_changed or tasks_changed or responses_changed or vehicles_changed or documents_changed
    ):
        from app.services.broadcast import manager
        await manager.broadcast(incident.id, {"type": "lis_sync", "reload_board": True})

    if created:
        # Adress-Geocoding-Fallback: nur wenn die LIS-Operation KEINE eigenen Koordinaten
        # mitgeliefert hat (sonst wurden diese bereits direkt übernommen, siehe
        # _get_or_link_incident). Läuft in der bestehenden Session (kein Request-Kontext);
        # die normalisierte Adress-Schreibweise (Vollversalien → Titel) steckt in
        # geocode_address(). Fehler dürfen die LIS-Anlage nie abbrechen.
        if incident.lat is None and incident.lng is None and (parsed["street"] or parsed["city"]):
            try:
                from app.services.geocoding import geocode_address
                geo = await geocode_address(parsed["street"], parsed["house_no"], parsed["city"])
            except Exception:
                geo = None
                logger.exception("Adress-Geocoding nach LIS-Anlage fehlgeschlagen (Einsatz %s)", incident.id)
            if geo:
                incident.lat, incident.lng = geo.lat, geo.lng
                db.flush()
                logger.info(
                    "Einsatz %s per Adress-Geocoding verortet (lat=%.5f, lon=%.5f)",
                    incident.id, geo.lat, geo.lng,
                )

        # Objekt-Matching fuer den frisch aus LIS angelegten Einsatz
        try:
            from app.services.objekt_matching_service import match_incident_background
            await match_incident_background(incident.id)
        except Exception:
            logger.exception("Objekt-Matching nach LIS-Anlage fehlgeschlagen (Einsatz %s)", incident.id)

        if parsed.get("is_closed"):
            # Der Einsatz war bei Anlage in LIS bereits abgeschlossen (Backfill historischer
            # Einsätze oder Operation zwischen zwei Polls beendet). Zur Dokumentation anlegen,
            # aber KEINE Alarmierung (Push/SMS/Teams/Board-Toast) mehr auslösen und den Einsatz
            # direkt schließen, damit er nicht fälschlich als aktiver Alarm erscheint.
            from app.services.incident_service import close_incident
            close_incident(db, incident, user_id=None, auto_closed_by_lis=True)
            db.flush()
            logger.info(
                "Einsatz %s aus bereits beendeter LIS-Operation %s angelegt (Org %s) — "
                "keine Alarmierung, direkt geschlossen",
                incident.id, parsed["lis_operation_id"], org.id,
            )
            return

        from app.services.broadcast import broadcast_org
        await broadcast_org(org.id, {
            "type": "incident_created",
            "incident_id": incident.id,
            "url": f"/einsatz/{incident.id}",
            "title": f"Neuer Einsatz aus LIS: {parsed['alarm_type_code']}",
        })

        # SMS-Einsatzinfo + Web-Push (+ Teams) – bisher loeste der LIS-Sync ueberhaupt
        # keine Benachrichtigung aus (kein Request-Kontext -> kein BackgroundTasks).
        # Gleiche zentrale Funktion wie API/manuelle Anlage (incident_notify.py),
        # hier ohne background_tasks direkt ausgefuehrt.
        from app.config import settings
        from app.services.incident_notify import notify_incident_created
        await notify_incident_created(
            db, incident, org_id=org.id,
            base_url=settings.effective_public_base_url,
            background_tasks=None,
        )


# ── Backfill (historische Einsätze) ──────────────────────────────────────────
async def backfill_organization(
    db: Session, org: FireDept, config: OrgLisConfig, client: LisClient,
    filters: tuple[str, ...] = ("LastDay", "LastMonth"),
) -> None:
    for operation_filter in filters:
        start_index = 0
        count = 50
        while True:
            try:
                operations = await client.get_operations_in_range(
                    config.organization_id, operation_filter=operation_filter,
                    count=count, start_index=start_index,
                )
            except LisClientError:
                logger.exception("LIS-Backfill (%s) für Org %s fehlgeschlagen", operation_filter, org.id)
                break
            if not operations:
                break
            for op in operations:
                try:
                    await sync_operation(db, org, config, client, op)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception(
                        "LIS-Backfill: Operation %s (Org %s) fehlgeschlagen", op.get("Id"), org.id,
                    )
            if len(operations) < count:
                break
            start_index += count


async def _maybe_backfill(db: Session, org: FireDept, config: OrgLisConfig, client: LisClient) -> None:
    now = datetime.now(UTC)
    if config.last_backfill_at and (now - _as_aware(config.last_backfill_at)) < _BACKFILL_INTERVAL:
        return
    await backfill_organization(db, org, config, client)
    config.last_backfill_at = now
    db.commit()


# ── Eine Organisation vollständig synchronisieren ────────────────────────────
async def sync_organization(db: Session, org: FireDept, config: OrgLisConfig) -> None:
    if not config.enabled or not config.is_fully_configured:
        return

    from app.core.crypto import decrypt_secret
    try:
        password = decrypt_secret(config.password_enc)
    except Exception:
        logger.exception("LIS-Passwort für Org %s konnte nicht entschlüsselt werden", org.id)
        return

    client = LisClient(
        config.base_url, config.site, config.username, password,
        project_id=config.project_id, password_is_hash=config.password_is_hash,
        organization_id=config.organization_id,
    )
    try:
        # Muss vor jedem GetTasks einmal aufgerufen werden, sonst NullReferenceException
        # auf dem LIS-Server (siehe select_operation()-Docstring in lis_client.py).
        await client.select_operation(config.organization_id)
    except LisClientError:
        logger.exception("LIS SelectOperation für Org %s fehlgeschlagen", org.id)
        return

    try:
        # Experiment 3 (2026-07-05, nach Fehlschlag von Experiment 1+2 im Live-Test):
        # GetRootOrganizations primt vermutlich den OperationService-seitigen Session-Cache,
        # den GetTasks braucht (siehe get_root_organizations()-Docstring in lis_client.py).
        # Best-effort: ein Fehlschlag hier darf den restlichen Sync nicht blockieren.
        await client.get_root_organizations()
    except LisClientError:
        logger.exception("LIS GetRootOrganizations für Org %s fehlgeschlagen", org.id)

    operations: list[dict] = []
    start_index = 0
    count = 50
    try:
        while True:
            batch = await client.get_operations_in_range(
                config.organization_id, operation_filter="ActiveParticipation",
                count=count, start_index=start_index,
            )
            if not batch:
                break
            operations.extend(batch)
            if len(batch) < count:
                break
            start_index += count
    except LisClientError:
        logger.exception("LIS-Abfrage (ActiveParticipation) für Org %s fehlgeschlagen", org.id)
        return

    for op in operations:
        try:
            await sync_operation(db, org, config, client, op)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "LIS-Operation %s (Org %s) konnte nicht synchronisiert werden", op.get("Id"), org.id,
            )

    active_operation_ids = {op.get("Id") for op in operations if op.get("Id")}
    await _close_incidents_missing_from_lis(db, org, active_operation_ids)

    await _maybe_backfill(db, org, config, client)


# ── Fahrzeugstatus vom Einsatzcockpit zurück ins LIS schreiben (opt-in) ──────
async def push_vehicle_status_to_lis(incident_vehicle_id: int, status: str) -> None:
    """Best-effort: schreibt einen lokal gesetzten Fahrzeugstatus per SetOperationUnitStatus
    zurück ins LIS, wenn die Org LIS aktiviert UND den Schalter push_vehicle_status gesetzt hat.

    Als BackgroundTask NACH dem lokalen Commit aufgerufen (siehe ui_incident.py) — Fehler hier
    dürfen den lokalen Statuswechsel nie beeinflussen, er ist zu diesem Zeitpunkt bereits
    gespeichert. Öffnet eine eigene DB-Session (Background-Task-Kontext, kein Request).
    """
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident, IncidentVehicle

    prefix = unit_status_to_lis_prefix(status)
    if not prefix:
        return

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vehicle = db.get(IncidentVehicle, incident_vehicle_id)
        if not vehicle or not vehicle.lis_operation_unit_id:
            return
        incident = db.get(Incident, vehicle.incident_id)
        if not incident or not incident.primary_org_id:
            return
        config = db.query(OrgLisConfig).filter(OrgLisConfig.org_id == incident.primary_org_id).first()
        if not config or not config.enabled or not config.push_vehicle_status or not config.is_fully_configured:
            return
        try:
            password = decrypt_secret(config.password_enc)
        except Exception:
            logger.exception(
                "LIS-Passwort für Org %s konnte nicht entschlüsselt werden (Status-Push)", incident.primary_org_id,
            )
            return

        client = LisClient(
            config.base_url, config.site, config.username, password,
            project_id=config.project_id, password_is_hash=config.password_is_hash,
            organization_id=config.organization_id,
        )
        try:
            status_types = await client.get_operation_unit_status_types()
            target = next(
                (s for s in status_types if (s.get("Label") or "").strip().upper().startswith(prefix)),
                None,
            )
            if not target or not target.get("Id"):
                logger.warning(
                    "LIS-Status-Typ %s nicht im Katalog gefunden (Org %s) — Status-Push übersprungen",
                    prefix, incident.primary_org_id,
                )
                return
            await client.set_operation_unit_status(vehicle.lis_operation_unit_id, target["Id"])
            logger.info(
                "Fahrzeugstatus %s (IncidentVehicle %s) zurück ins LIS geschrieben (Org %s)",
                status, incident_vehicle_id, incident.primary_org_id,
            )
        except LisClientError:
            logger.exception(
                "SetOperationUnitStatus für IncidentVehicle %s fehlgeschlagen (Org %s)",
                incident_vehicle_id, incident.primary_org_id,
            )
    finally:
        db.close()
