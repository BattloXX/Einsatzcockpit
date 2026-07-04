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
from app.models.incident import IncidentColumn, IncidentLog, IncidentVehicle, Message
from app.models.lis import LisSyncedObject, OrgLisConfig
from app.models.major_incident import IncidentSite, VehiclePosition
from app.models.master import FireDept, VehicleMaster
from app.services.incident_service import create_incident, set_unit_status
from app.services.lis.lis_client import LisClient, LisClientError
from app.services.lis.lis_geo import lis_unit_coords_to_wgs84
from app.services.lis.lis_mapping import (
    is_exercise_operation,
    map_stichwort,
    map_unit_status,
    parse_person_response,
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


def _parse_operation(op: dict, org: FireDept | None) -> dict:
    address = op.get("Address") or {}
    type_obj = op.get("Type") or {}
    reason = op.get("Description") or op.get("Name")
    started_raw = op.get("BeginTime") or op.get("CreationTime")
    return {
        "lis_operation_id": op.get("Id"),
        "lis_operation_number": op.get("Number"),
        "reason": reason,
        "street": address.get("Street"),
        "house_no": address.get("Housenumber"),
        "city": address.get("Community"),
        "report_text": reason,
        "alarm_type_code": map_stichwort(type_obj.get("Code") or type_obj.get("Type")),
        "started_at": _parse_operation_datetime(started_raw, org),
        "is_exercise": is_exercise_operation(type_obj),
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

    incident = create_incident(
        db,
        alarm_type_code=parsed["alarm_type_code"],
        started_at=parsed["started_at"],
        is_exercise=parsed["is_exercise"],
        address_street=parsed["street"],
        address_no=parsed["house_no"],
        address_city=parsed["city"],
        report_text=parsed["report_text"],
        reason=parsed["reason"],
        primary_org_id=org.id,
    )
    incident.lis_operation_id = parsed["lis_operation_id"]
    incident.lis_operation_number = parsed["lis_operation_number"]
    db.flush()
    logger.info(
        "Einsatz %s aus LIS-Operation %s neu angelegt (Org %s, zuerst über LIS geliefert)",
        incident.id, parsed["lis_operation_id"], org.id,
    )
    return incident, True


# ── Fahrzeugstatus (S4/S5) ───────────────────────────────────────────────────
def _sync_vehicle_status(db: Session, org: FireDept, incident, units: list[dict]) -> None:
    lage_id = _incident_major_incident_id(db, incident.id)

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
            continue

        status_label = _op_field(unit, "OperationUnitStatusType", "Label")
        if isinstance(status_label, str):
            mapped_status = map_unit_status(status_label)
            if mapped_status and incident_vehicle.unit_status != mapped_status:
                try:
                    set_unit_status(db, incident_vehicle, mapped_status)
                except ValueError:
                    logger.warning("Ungültiger gemappter Status %r für Fahrzeug %s", mapped_status, vehicle_master.id)

        _sync_vehicle_location(db, org, incident, vehicle_master, unit, lage_id)


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


# ── Meldungen (Tasks → Message-Cards, nur normaler Incident) ────────────────
def _sync_messages(db: Session, org: FireDept, incident, tasks: list[dict]) -> None:
    if _incident_belongs_to_major_incident(db, incident.id):
        return

    messages_col = (
        db.query(IncidentColumn)
        .filter(IncidentColumn.incident_id == incident.id, IncidentColumn.code == "messages")
        .first()
    )

    for task in tasks:
        if _op_field(task, "Type", "Type") == "UNITSTATUSHISTORY":
            continue  # separat behandelt (Fahrzeugstatus / Zu-Absagen)
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
            continue

        db.add(Message(
            incident_id=incident.id,
            column_id=messages_col.id if messages_col else None,
            title=task.get("Number") or "LIS-Meldung",
            detail=description or None,
            author_name=task.get("CreatedBy") or "LIS",
            lis_task_id=lis_task_id,
        ))
        db.flush()
        logger.info("Meldung aus LIS-Task %s auf Einsatz %s übernommen", lis_task_id, incident.id)


# ── Zu-/Absagen (Mannschaft) → Einsatz-Verlauf ───────────────────────────────
def _sync_person_responses(db: Session, org: FireDept, incident, tasks: list[dict]) -> None:
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
        logger.info(
            "Zu-/Absage aus LIS-Task %s auf Einsatz %s protokolliert (%s: %s)",
            lis_task_id, incident.id, person, parsed["status"],
        )


# ── Dokumente (MTOM-Download) ────────────────────────────────────────────────
async def _store_lis_document(db: Session, incident, raw: bytes, filename: str, org_id: int) -> None:
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
        title=f"Dokument: {filename}",
        author_name="LIS",
    )
    db.add(doc_message)
    db.flush()

    upload = UploadFile(filename=filename, file=io.BytesIO(raw))
    system_user = SimpleNamespace(id=None)  # kein handelnder Benutzer bei Hintergrund-Sync
    await media_service.store_upload_for_message(upload, doc_message, system_user, db, org_id=org_id)  # type: ignore[arg-type]


async def _sync_documents(db: Session, org: FireDept, incident, client: LisClient, operation_id: str) -> None:
    try:
        documents = await client.get_documents_by_operation_id(operation_id)
    except LisClientError:
        logger.exception(
            "LIS-Dokumente für Operation %s (Org %s) konnten nicht geladen werden", operation_id, org.id,
        )
        return

    for doc in documents:
        doc_id = doc.get("Id")
        if not doc_id or _already_synced(db, org.id, "document", doc_id):
            continue
        try:
            raw = await client.download_document(doc_id, entity=doc.get("Entity") or "PR_OPERATION")
        except LisClientError:
            logger.exception("LIS-Dokument %s (Einsatz %s) konnte nicht geladen werden", doc_id, incident.id)
            continue

        name = doc.get("Name") or doc_id
        ext = (doc.get("FileExtension") or "").lstrip(".")
        filename = f"{name}.{ext}" if ext else name

        try:
            await _store_lis_document(db, incident, raw, filename, org.id)
        except Exception:
            logger.exception("LIS-Dokument %s (Einsatz %s) konnte nicht gespeichert werden", doc_id, incident.id)
            continue

        db.add(LisSyncedObject(org_id=org.id, obj_type="document", lis_id=doc_id, incident_id=incident.id))
        db.flush()
        logger.info("Dokument %s aus LIS auf Einsatz %s übernommen", filename, incident.id)


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
        )
        .all()
    )
    for incident in stale:
        close_incident(db, incident, user_id=None)
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
        tasks = await client.get_tasks(parsed["lis_operation_id"])
    except LisClientError:
        logger.exception(
            "LIS-Tasks für Operation %s (Org %s) fehlgeschlagen", parsed["lis_operation_id"], org.id,
        )
        tasks = []

    _sync_messages(db, org, incident, tasks)
    _sync_person_responses(db, org, incident, tasks)

    try:
        units = await client.get_operation_units(config.organization_id, parsed["lis_operation_id"])
    except LisClientError:
        logger.exception(
            "LIS-Einheiten für Operation %s (Org %s) fehlgeschlagen", parsed["lis_operation_id"], org.id,
        )
        units = []
    _sync_vehicle_status(db, org, incident, units)

    await _sync_documents(db, org, incident, client, parsed["lis_operation_id"])

    if created:
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

    client = LisClient(config.base_url, config.site, config.username, password)
    try:
        # Muss vor jedem GetTasks einmal aufgerufen werden, sonst NullReferenceException
        # auf dem LIS-Server (siehe select_operation()-Docstring in lis_client.py).
        await client.select_operation(config.organization_id)
    except LisClientError:
        logger.exception("LIS SelectOperation für Org %s fehlgeschlagen", org.id)
        return

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
