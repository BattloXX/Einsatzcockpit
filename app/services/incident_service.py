"""Core incident business logic – mirrors startIncident(), changeAlarm() from the HTML version."""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import write_audit, write_incident_change
from app.models.incident import (
    FIXED_COLUMN_TITLES,
    FIXED_COLUMNS,
    TASK_STATUS_VALUES,
    TRAFFIC_LIGHT_VALUES,
    UNIT_STATUS_VALUES,
    Incident,
    IncidentChange,
    IncidentColumn,
    IncidentLog,
    IncidentVehicle,
    Message,
    RescuedPerson,
    Task,
)
from app.models.master import (
    AlarmDispatchVehicle,
    AlarmType,
    DefaultMessage,
    DefaultMessageAlarm,
    Member,
    MemberQualification,
    Qualification,
    VehicleMaster,
)
from app.models.user import User


def _now() -> datetime:
    return datetime.now(UTC)


def collect_report_context(incident_id: int, db: Session) -> dict[str, Any]:
    """Build a structured, person-data-free payload for AI report generation."""
    from sqlalchemy.orm import selectinload

    from app.services.ai_service import _strip_persons

    incident = (
        db.query(Incident)
        .options(
            selectinload(Incident.vehicles),
            selectinload(Incident.tasks),
            selectinload(Incident.messages),
            selectinload(Incident.log_entries),
            selectinload(Incident.rescued_persons),
        )
        .filter(Incident.id == incident_id)
        .first()
    )
    if not incident:
        raise ValueError(f"Einsatz {incident_id} nicht gefunden")

    duration_min: int | None = None
    if incident.started_at and incident.closed_at:
        duration_min = int((incident.closed_at - incident.started_at).total_seconds() / 60)

    def _vehicle_label(v) -> str:
        vm = getattr(v, "vehicle_master", None)
        return getattr(vm, "display_label", "–") if vm else "–"

    def _vehicle_org(v) -> str:
        vm = getattr(v, "vehicle_master", None)
        dept = getattr(vm, "dept", None) if vm else None
        return getattr(dept, "name", "–") if dept else "–"

    data: dict[str, Any] = {
        "alarm_type": incident.alarm_type_code,
        "einsatz_id": incident.id,
        "started_at": incident.started_at.isoformat() if incident.started_at else None,
        "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
        "dauer_min": duration_min,
        "adresse": " ".join(filter(None, [
            incident.address_street,
            incident.address_no,
            incident.address_city,
        ])),
        "meldung": incident.report_text,
        "einsatzgrund": incident.reason,
        "fahrzeuge": [
            {
                "rufname": _vehicle_label(v),
                "org": _vehicle_org(v),
                "status": v.unit_status,
            }
            for v in incident.vehicles
        ],
        "auftraege": [
            {
                "titel": t.title,
                "detail": t.detail,
                "status": "erledigt" if t.is_done else "abgebrochen" if t.is_cancelled else "offen",
            }
            for t in incident.tasks
        ],
        "meldungen": [
            {
                "titel": m.title,
                "status": m.status,
                "erstellt": m.created_at.isoformat(),
            }
            for m in incident.messages
        ],
        "verlauf": [
            {"ts": e.ts.isoformat(), "text": e.text}
            for e in incident.log_entries
        ],
        "gerettete_personen_anzahl": len(incident.rescued_persons),
    }
    return _strip_persons(data)


def collect_situation_context(incident_id: int, db: Session) -> dict[str, Any]:
    """Build a live situation payload for AI Lagebild generation (person-data-free)."""
    from sqlalchemy.orm import selectinload

    from app.services.ai_service import _strip_persons

    incident = (
        db.query(Incident)
        .options(
            selectinload(Incident.vehicles),
            selectinload(Incident.tasks),
            selectinload(Incident.messages),
            selectinload(Incident.rescued_persons),
        )
        .filter(Incident.id == incident_id)
        .first()
    )
    if not incident:
        raise ValueError(f"Einsatz {incident_id} nicht gefunden")

    now = datetime.now(UTC)
    started = incident.started_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    duration_min = int((now - started).total_seconds() / 60) if started else None

    col_by_id = {c.id: c for c in incident.columns}

    def _col_code(col_id: int | None) -> str:
        col = col_by_id.get(col_id) if col_id else None
        return col.code if col else "unbekannt"

    data: dict[str, Any] = {
        "alarm_type": incident.alarm_type_code,
        "adresse": " ".join(filter(None, [
            incident.address_street,
            incident.address_no,
            incident.address_city,
        ])),
        "meldung": incident.report_text,
        "einsatzgrund": incident.reason,
        "laufzeit_min": duration_min,
        "fahrzeuge": [
            {
                "rufname": (
                    v.vehicle_master.display_label if v.vehicle_master else "–"
                ),
                "abschnitt": _col_code(v.column_id),
                "status": v.unit_status,
            }
            for v in incident.vehicles
            if not v.removed_at
        ],
        "auftraege": [
            {
                "titel": t.title,
                "status": (
                    "erledigt" if t.is_done
                    else "abgebrochen" if t.is_cancelled
                    else t.status or "offen"
                ),
            }
            for t in incident.tasks
            if not t.is_cancelled
        ],
        "meldungen": [
            {"titel": m.title, "status": m.status}
            for m in sorted(incident.messages, key=lambda m: m.created_at, reverse=True)[:10]
        ],
        "gerettete_personen_anzahl": len(incident.rescued_persons),
    }
    return _strip_persons(data)


def _resolve_org_id(db: Session, requested: int | None) -> int | None:
    """Liefert eine gültige fire_dept.id zurück.

    Eine explizit übergebene Org wird NIE durch eine andere Org ersetzt — ist sie
    ungültig, ist das Ergebnis None statt eines Fallbacks (Vorfall: Einsätze ohne
    auflösbare Org landeten sonst still bei der Org mit der kleinsten fire_dept.id,
    unabhängig davon, welche Org eigentlich gemeint war — z.B. bei API-Keys ohne
    org_id). Der "erste Org"-Fallback greift nur noch, wenn gar keine Org verlangt
    wurde UND genau eine Org existiert (Single-Tenant-Altfall) — bei mehreren Orgs
    wäre das Erraten der falschen Org schlimmer als ein fehlender Fallback.
    """
    from app.models.master import FireDept
    if requested is not None:
        return requested if db.get(FireDept, requested) else None
    orgs = db.query(FireDept.id).limit(2).all()
    return orgs[0][0] if len(orgs) == 1 else None


def create_incident(
    db: Session,
    alarm_type_code: str,
    *,
    started_at: datetime | None = None,
    external_key: str | None = None,
    nummer: int | None = None,
    is_exercise: bool = False,
    address_street: str | None = None,
    address_no: str | None = None,
    address_city: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    report_text: str | None = None,
    reason: str | None = None,
    caller_name: str | None = None,
    caller_phone: str | None = None,
    incident_leader_user_id: int | None = None,
    primary_org_id: int | None = None,
    api_key_id: int | None = None,
    ip: str | None = None,
) -> Incident:
    import hashlib
    import secrets as _secrets

    from app.models.lagekarte import LagekarteToken
    from app.services.alarm_service import get_alarm_type_by_code as _get_alarm_type

    resolved_org_id = _resolve_org_id(db, primary_org_id)
    alarm = _get_alarm_type(db, resolved_org_id, alarm_type_code) if resolved_org_id else None
    if alarm is None:
        alarm_type_code = "T1"
        alarm = _get_alarm_type(db, resolved_org_id, "T1") if resolved_org_id else None
    raw_token = "lkw_" + _secrets.token_urlsafe(32)

    incident = Incident(
        external_key=external_key,
        nummer=nummer,
        alarm_type_code=alarm_type_code,
        status="active",
        started_at=started_at or _now(),
        is_exercise=is_exercise,
        address_street=address_street,
        address_no=address_no,
        address_city=address_city,
        lat=lat,
        lng=lng,
        report_text=report_text,
        reason=reason,
        caller_name=caller_name,
        caller_phone=caller_phone,
        incident_leader_user_id=incident_leader_user_id,
        primary_org_id=resolved_org_id,
        auto_geojson_token=raw_token,
    )
    db.add(incident)
    db.flush()  # get id

    # Token für die öffentliche No-Login-Alarmübersicht (Teams-Karte, QR-Link) — Plain-Text
    # auf dem Incident selbst (analog auto_geojson_token) für spätere Wiederverwendung.
    from app.models.teams_bot import AlarmToken as _AlarmToken
    _alarm_raw_token = "alm_" + _secrets.token_urlsafe(32)
    incident.alarm_token = _alarm_raw_token
    db.add(_AlarmToken(
        incident_id=incident.id,
        token_hash=hashlib.sha256(_alarm_raw_token.encode()).hexdigest(),
        created_at=_now(),
    ))

    if resolved_org_id:
        lk_token = LagekarteToken(
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            label="Auto",
            org_id=resolved_org_id,
            einsatz_id=incident.id,
            created_at=_now(),
        )
        db.add(lk_token)

    _create_fixed_columns(db, incident)
    _populate_vehicles(db, incident, alarm)
    _create_default_messages(db, incident, alarm)

    write_audit(
        db, "incident.created",
        incident_id=incident.id,
        api_key_id=api_key_id,
        ip=ip,
        payload={
            "alarm_type_code": alarm_type_code,
            "is_exercise": is_exercise,
            "primary_org_id": incident.primary_org_id,
        },
    )
    return incident


_CODE_KINDS: dict[str, str] = {
    "active":     "vehicles",
    "tasks":      "tasks",
    "messages":   "messages",
    "rescued":    "rescued",
    "neighbor":   "neighbor",
}


def _create_fixed_columns(db: Session, incident: Incident) -> None:
    for i, code in enumerate(FIXED_COLUMNS):
        col = IncidentColumn(
            incident_id=incident.id,
            code=code,
            title=FIXED_COLUMN_TITLES[code],
            column_kind=_CODE_KINDS.get(code, "custom"),
            is_fixed=True,
            display_order=i,
        )
        db.add(col)
    db.flush()


def _get_column(incident: Incident, code: str) -> IncidentColumn | None:
    for col in incident.columns:
        if col.code == code:
            return col
    return None


def _lis_enabled_for_org(db: Session, org_id: int | None) -> bool:
    """True, wenn die Org eine aktive LIS-Anbindung hat.

    Steuert, ob Fahrzeuge aus der Ausrückordnung sofort in "Tatsächlich im Einsatz"
    landen (kein LIS: klassischer Ablauf) oder erst mit Status S4/manuell dort
    erscheinen (mit LIS: das LIS meldet den Status, siehe lis_sync.py).
    """
    if not org_id:
        return False
    from app.models.lis import OrgLisConfig
    return bool(
        db.query(OrgLisConfig.id)
        .filter(OrgLisConfig.org_id == org_id, OrgLisConfig.enabled == True)  # noqa: E712
        .first()
    )


def _populate_vehicles(db: Session, incident: Incident, alarm: AlarmType | None) -> None:
    if alarm is None:
        return

    db.refresh(incident, ["columns"])
    active_col = _get_column(incident, "active")
    if not active_col:
        return

    from app.models.master import FireDept

    # Check if explicit dispatch order exists for this alarm type
    dispatch_entries = (
        db.query(AlarmDispatchVehicle)
        .filter(AlarmDispatchVehicle.alarm_type_id == alarm.id)
        .order_by(AlarmDispatchVehicle.display_order)
        .all()
    )

    # Mit aktiver LIS-Anbindung übernimmt das LIS die Ankunftsmeldung (Status S4) —
    # die Ausrückordnung legt dann KEINE eigenen Fahrzeuge automatisch an, sie
    # erscheinen erst mit S4 (lis_sync.py) oder durch manuelles Hinzufügen im Board.
    # Die Nachbar-Wehren-Liste (unten) ist davon unabhängig und wird immer befüllt.
    if not _lis_enabled_for_org(db, incident.primary_org_id):
        if dispatch_entries:
            # Use explicit dispatch order
            for i, entry in enumerate(dispatch_entries):
                vm = db.get(VehicleMaster, entry.vehicle_master_id)
                if vm and vm.active:
                    db.add(IncidentVehicle(
                        incident_id=incident.id,
                        column_id=active_col.id,
                        vehicle_master_id=vm.id,
                        display_order=i,
                    ))
        else:
            # Fallback: use is_first_train flag (original logic)
            wolfurt_q = (
                db.query(VehicleMaster)
                .join(VehicleMaster.dept)
                .filter(VehicleMaster.active == True)  # noqa: E712
                .order_by(VehicleMaster.display_order)
            )
            wolfurt_q = wolfurt_q.filter(FireDept.slug == "wolfurt")

            if alarm and alarm.default_first_train_only:
                wolfurt_q = wolfurt_q.filter(VehicleMaster.is_first_train == True)  # noqa: E712

            for i, vm in enumerate(wolfurt_q.all()):
                db.add(IncidentVehicle(
                    incident_id=incident.id,
                    column_id=active_col.id,
                    vehicle_master_id=vm.id,
                    display_order=i,
                ))

    # Neighbor column: only create when alarm uses notify_neighbors (and no explicit dispatch)
    if alarm and alarm.notify_neighbors and not dispatch_entries:
        neighbor_col = _get_column(incident, "neighbor")
        if neighbor_col is None:
            neighbor_col = IncidentColumn(
                incident_id=incident.id,
                code="neighbor",
                title=FIXED_COLUMN_TITLES["neighbor"],
                column_kind="neighbor",
                is_fixed=True,
                display_order=len(FIXED_COLUMNS),
            )
            db.add(neighbor_col)
            db.flush()
        from app.models.master import FireDept as FD
        neighbor_q = (
            db.query(VehicleMaster)
            .join(VehicleMaster.dept)
            .filter(VehicleMaster.active == True)  # noqa: E712
            .filter(FD.slug != "wolfurt")
            .order_by(VehicleMaster.display_order)
        )
        for i, vm in enumerate(neighbor_q.all()):
            db.add(IncidentVehicle(
                incident_id=incident.id,
                column_id=neighbor_col.id,
                vehicle_master_id=vm.id,
                display_order=i,
            ))
    db.flush()


def _create_default_messages(db: Session, incident: Incident, alarm: AlarmType | None) -> None:
    if alarm is None:
        return
    msgs_col = _get_column(incident, "messages")
    assignments = (
        db.query(DefaultMessageAlarm)
        .filter(DefaultMessageAlarm.alarm_type_id == alarm.id)
        .order_by(DefaultMessageAlarm.display_order)
        .all()
    )
    for i, a in enumerate(assignments):
        dm = db.get(DefaultMessage, a.default_message_id)
        if not dm:
            continue
        due_at = None
        if incident.started_at and a.due_after_sec:
            from datetime import timedelta
            started = incident.started_at if incident.started_at.tzinfo else incident.started_at.replace(tzinfo=UTC)
            due_at = started + timedelta(seconds=a.due_after_sec)
        db.add(Message(
            incident_id=incident.id,
            column_id=msgs_col.id if msgs_col else None,
            title=dm.text,
            due_after_sec=a.due_after_sec,
            due_at=due_at,
            display_order=i,
        ))
    db.flush()


def add_task(
    db: Session,
    incident: Incident,
    title: str,
    detail: str | None = None,
    user_id: int | None = None,
    column_id: int | None = None,
) -> Task:
    if column_id is None:
        tasks_col = _get_column(incident, "tasks")
        column_id = tasks_col.id if tasks_col else None
    task = Task(
        incident_id=incident.id,
        column_id=column_id,
        title=title,
        detail=detail,
        created_by_user_id=user_id,
    )
    db.add(task)
    db.flush()
    write_incident_change(
        db, incident.id, "task.created", "task", task.id,
        before=None, after={"title": title, "detail": detail},
        user_id=user_id,
    )
    return task


def assign_task_to_vehicle(
    db: Session,
    task: Task,
    vehicle: IncidentVehicle,
    user_id: int | None = None,
) -> Task:
    before = {"vehicle_id": task.vehicle_id, "column_id": task.column_id}
    task.vehicle_id = vehicle.id
    # Keep column_id so task remains visible on the board AND on the vehicle
    db.flush()
    write_incident_change(
        db, task.incident_id, "task.assigned", "task", task.id,
        before=before, after={"vehicle_id": vehicle.id},
        user_id=user_id,
    )
    if vehicle.vehicle_master_id:
        from app.services.push_service import notify_vehicle
        notify_vehicle(db, vehicle.vehicle_master_id, "📋 Neuer Auftrag", task.title,
                       url=f"/einsatz/{task.incident_id}?open_task={task.id}")
    return task


def move_vehicle_to_column(
    db: Session,
    vehicle: IncidentVehicle,
    new_column: IncidentColumn,
    user_id: int | None = None,
) -> IncidentVehicle:
    before = {"column_id": vehicle.column_id}
    vehicle.column_id = new_column.id
    db.flush()
    write_incident_change(
        db, vehicle.incident_id, "vehicle.moved", "incident_vehicle", vehicle.id,
        before=before, after={"column_id": new_column.id},
        user_id=user_id,
    )
    return vehicle


def close_incident(
    db: Session, incident: Incident, user_id: int | None = None, auto_closed_by_lis: bool = False,
) -> Incident:
    incident.status = "closed"
    incident.closed_at = _now()
    incident.closed_via_lis_auto = auto_closed_by_lis
    now = _now()
    # Revoke all QR (IncidentToken) tokens
    for token in incident.tokens:
        if token.revoked_at is None:
            token.revoked_at = now
    # Revoke LagekarteTokens scoped to this incident (auto-token + manuelle)
    from app.models.lagekarte import LagekarteToken
    db.query(LagekarteToken).filter(
        LagekarteToken.einsatz_id == incident.id,
        LagekarteToken.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    # Revoke den öffentlichen Alarm-Token (Teams-Karte, QR-Link) — nach Abschluss nicht
    # mehr sinnvoll erreichbar
    from app.models.teams_bot import AlarmToken
    db.query(AlarmToken).filter(
        AlarmToken.incident_id == incident.id,
        AlarmToken.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
    db.flush()
    write_audit(db, "incident.closed", incident_id=incident.id, user_id=user_id)
    return incident


def reopen_incident(db: Session, incident: Incident, user_id: int | None = None) -> Incident:
    """Wiedereröffnen eines abgeschlossenen Einsatzes (system_admin/org_admin).

    Setzt den Status zurück auf "active" und löscht den Abschluss-Zeitstempel.
    Beim Abschließen widerrufene QR-/Lagekarte-Tokens werden NICHT automatisch
    neu erzeugt – diese können bei Bedarf neu generiert werden.
    """
    incident.status = "active"
    incident.closed_at = None
    incident.closed_via_lis_auto = False
    # Ursprüngliche started_at bleibt erhalten (Datenintegrität). Eine evtl. anstehende
    # Autoclose-Warnung wird zurückgesetzt; bei >48h alten Einsätzen erscheint erneut das
    # "Offen halten?"-Banner, über das der Zähler bei Bedarf neu gestartet wird.
    incident.autoclose_warn_sent_at = None
    db.flush()
    write_audit(db, "incident.reopened", incident_id=incident.id, user_id=user_id)
    return incident


def add_section_column(
    db: Session,
    incident: Incident,
    title: str,
    column_kind: str = "vehicles",
    user_id: int | None = None,
) -> IncidentColumn:
    allowed_kinds = {"vehicles", "tasks", "messages"}
    kind = column_kind if column_kind in allowed_kinds else "vehicles"
    max_order = max((c.display_order for c in incident.columns), default=0)
    col = IncidentColumn(
        incident_id=incident.id,
        code=f"section_{_now().timestamp():.0f}",
        title=title,
        column_kind=kind,
        is_fixed=False,
        display_order=max_order + 1,
    )
    db.add(col)
    db.flush()
    write_incident_change(
        db, incident.id, "column.created", "incident_column", col.id,
        before=None, after={"title": title},
        user_id=user_id,
    )
    return col


def reorder_columns(db: Session, incident_id: int, column_ids: list[int]) -> None:
    for order, col_id in enumerate(column_ids):
        col = (
            db.query(IncidentColumn)
            .filter_by(id=col_id, incident_id=incident_id)
            .first()
        )
        if col:
            col.display_order = order
    db.flush()


def set_commander(
    db: Session,
    vehicle: IncidentVehicle,
    member_id: int | None,
    user_id: int | None = None,
) -> IncidentVehicle:
    before = {"commander_member_id": vehicle.commander_member_id}
    vehicle.commander_member_id = member_id
    db.flush()
    write_incident_change(
        db, vehicle.incident_id, "vehicle.commander_set", "incident_vehicle", vehicle.id,
        before=before, after={"commander_member_id": member_id},
        user_id=user_id,
    )
    return vehicle



def _next_display_order(db: Session, incident_id: int, column_id: int) -> int:
    """Liefert den nächsten freien display_order-Wert für eine Spalte (ans Ende)."""
    from sqlalchemy import func
    max_order = db.query(func.max(IncidentVehicle.display_order)).filter(
        IncidentVehicle.incident_id == incident_id,
        IncidentVehicle.column_id == column_id,
        IncidentVehicle.removed_at.is_(None),
    ).scalar()
    return (max_order + 1) if max_order is not None else 0


def set_unit_status(
    db: Session,
    vehicle: IncidentVehicle,
    status: str,
    user_id: int | None = None,
) -> IncidentVehicle:
    if status not in UNIT_STATUS_VALUES:
        raise ValueError(f"Ungültiger Status: {status}")
    before = {"unit_status": vehicle.unit_status, "column_id": vehicle.column_id}
    vehicle.unit_status = status
    # Sync: Status "Einsatz übernommen" (S4) verschiebt das Fahrzeug nur dann automatisch in
    # die Spalte "active", wenn es noch spaltenlos ist. Ist das Fahrzeug bereits bewusst einem
    # benannten Abschnitt zugeordnet, bleibt es dort – nur der Status ändert sich. (Neu
    # angelegte Fahrzeuge sind seit Entfernen der "Disponiert"-Spalte entweder direkt in
    # "active" (ohne LIS) oder werden erst mit S4/manuell angelegt (mit LIS, siehe
    # lis_sync.py) — dieser Zweig greift daher praktisch nur noch bei Altdaten.)
    if status == "Einsatz übernommen":
        current_col = db.get(IncidentColumn, vehicle.column_id) if vehicle.column_id else None
        if current_col is None:
            active_col = db.query(IncidentColumn).filter_by(
                incident_id=vehicle.incident_id, code="active"
            ).first()
            if active_col and vehicle.column_id != active_col.id:
                vehicle.column_id = active_col.id
                vehicle.display_order = _next_display_order(db, vehicle.incident_id, active_col.id)
                db.flush()
                append_card(db, active_col.id, "vehicle", vehicle.id)
    db.flush()
    write_incident_change(
        db, vehicle.incident_id, "vehicle.status_set", "incident_vehicle", vehicle.id,
        before=before, after={"unit_status": status, "column_id": vehicle.column_id},
        user_id=user_id,
    )
    return vehicle


def list_commander_candidates(db: Session, org_ids: list[int]) -> list[Member]:
    """Return active members with Gruppenkommandant qualification.

    Zeigt alle aktiven Mitglieder mit GK-Qualifikation, unabhängig von der
    org_id-Zuweisung. In Single-Org-Installationen kann org_id der Mitglieder
    von der primary_org_id des Einsatzes abweichen (z.B. nach Excel-Import),
    weshalb hier bewusst kein org_id-Filter angewendet wird.
    """
    return (
        db.query(Member)
        .join(MemberQualification, MemberQualification.member_id == Member.id)
        .join(Qualification, Qualification.id == MemberQualification.qualification_id)
        .filter(
            Member.active.is_(True),
            Qualification.is_gruppenkommandant.is_(True),
        )
        .order_by(Member.lastname, Member.firstname)
        .distinct()
        .all()
    )


def list_el_candidates(db: Session, org_ids: list[int]) -> list[Member]:
    """Return active members with Einsatzleiter qualification.

    Zeigt alle aktiven Mitglieder mit EL-Qualifikation, unabhängig von der
    org_id-Zuweisung (siehe Kommentar bei list_commander_candidates).
    """
    return (
        db.query(Member)
        .join(MemberQualification, MemberQualification.member_id == Member.id)
        .join(Qualification, Qualification.id == MemberQualification.qualification_id)
        .filter(
            Member.active.is_(True),
            Qualification.is_einsatzleiter.is_(True),
        )
        .order_by(Member.lastname, Member.firstname)
        .distinct()
        .all()
    )


def list_section_leader_candidates(db: Session, org_ids: list[int]) -> list[Member]:
    """Return active members qualified as Abschnittsleiter (Lane-Leiter).

    Ein Abschnittsleiter benötigt die Qualifikation Einsatzleiter ODER
    Gruppenkommandant (Vereinigung der beiden bestehenden Kandidaten-Listen).
    Kein org_id-Filter, siehe Kommentar bei list_commander_candidates.
    """
    return (
        db.query(Member)
        .join(MemberQualification, MemberQualification.member_id == Member.id)
        .join(Qualification, Qualification.id == MemberQualification.qualification_id)
        .filter(
            Member.active.is_(True),
            (Qualification.is_einsatzleiter.is_(True)) | (Qualification.is_gruppenkommandant.is_(True)),
        )
        .order_by(Member.lastname, Member.firstname)
        .distinct()
        .all()
    )


def update_task(
    db: Session,
    task: Task,
    title: str,
    detail: str | None = None,
    user_id: int | None = None,
) -> Task:
    before = {"title": task.title, "detail": task.detail}
    task.title = title
    task.detail = detail or None
    db.flush()
    write_incident_change(
        db, task.incident_id, "task.updated", "task", task.id,
        before=before, after={"title": title, "detail": detail},
        user_id=user_id,
    )
    return task


def cancel_task(
    db: Session,
    task: Task,
    user_id: int | None = None,
) -> Task:
    before = {"is_cancelled": task.is_cancelled}
    task.is_cancelled = not task.is_cancelled
    task.cancelled_at = _now() if task.is_cancelled else None
    db.flush()
    write_incident_change(
        db, task.incident_id, "task.cancelled" if task.is_cancelled else "task.restored", "task", task.id,
        before=before, after={"is_cancelled": task.is_cancelled},
        user_id=user_id,
    )
    return task


def set_task_status(
    db: Session,
    task: Task,
    status: str,
    user_id: int | None = None,
) -> Task:
    if status not in TASK_STATUS_VALUES:
        raise ValueError(f"Ungültiger Status: {status}")
    before = {"status": task.status, "is_done": task.is_done, "is_cancelled": task.is_cancelled}
    task.status = status
    if status == "done":
        task.is_done = True
        task.done_at = _now()
        task.is_cancelled = False
        task.cancelled_at = None
    elif status == "cancelled":
        task.is_cancelled = True
        task.cancelled_at = _now()
        task.is_done = False
        task.done_at = None
    else:
        task.is_done = False
        task.done_at = None
        task.is_cancelled = False
        task.cancelled_at = None
    db.flush()
    write_incident_change(
        db, task.incident_id, "task.status_set", "task", task.id,
        before=before, after={"status": status},
        user_id=user_id,
    )
    return task


def set_message_status(
    db: Session,
    message: Message,
    status: str,
    user_id: int | None = None,
) -> Message:
    # Toleriere auch Legacy-Werte (open/in_progress/done/cancelled)
    from app.models.incident import _TRAFFIC_LIGHT_LEGACY
    status = _TRAFFIC_LIGHT_LEGACY.get(status, status)
    if status not in TRAFFIC_LIGHT_VALUES:
        raise ValueError(f"Ungültiger Status: {status}")
    before = {"status": message.status, "is_done": message.is_done, "is_cancelled": message.is_cancelled}
    message.status = status
    if status == "erledigt":
        message.is_done = True
        message.done_at = _now()
        message.is_cancelled = False
    elif status == "storniert":
        message.is_cancelled = True
        message.is_done = False
        message.done_at = None
    else:
        message.is_done = False
        message.done_at = None
        message.is_cancelled = False
    db.flush()
    write_incident_change(
        db, message.incident_id, "message.status_set", "message", message.id,
        before=before, after={"status": status},
        user_id=user_id,
    )
    return message


def update_column_card_order(db: Session, column_id: int, zone_order_json: str) -> None:
    """Speichert die vollständige Karten-Reihenfolge einer Spalte (JSON-Array)."""
    col = db.get(IncidentColumn, column_id)
    if col:
        col.card_order = zone_order_json
        db.flush()


def _card_order_or_default(db: Session, col: IncidentColumn) -> list[dict]:
    """Liefert die gespeicherte card_order, oder baut sie aus dem aktuellen DB-Zustand
    auf (vehicles, tasks, messages, persons), wenn noch keine gespeichert ist."""
    import json as _json
    if col.card_order:
        try:
            return _json.loads(col.card_order)
        except Exception:
            return []

    vehicles = (
        db.query(IncidentVehicle)
        .filter(IncidentVehicle.column_id == col.id, IncidentVehicle.removed_at.is_(None))
        .order_by(IncidentVehicle.display_order)
        .all()
    )
    tasks = (
        db.query(Task)
        .filter(Task.column_id == col.id, Task.vehicle_id.is_(None))
        .order_by(Task.display_order)
        .all()
    )
    msgs = (
        db.query(Message)
        .filter(Message.column_id == col.id)
        .order_by(Message.display_order)
        .all()
    )
    persons = []
    if col.column_kind == "rescued":
        persons = (
            db.query(RescuedPerson)
            .filter(RescuedPerson.incident_id == col.incident_id)
            .order_by(RescuedPerson.created_at)
            .all()
        )
    return (
        [{"kind": "vehicle", "id": v.id} for v in vehicles]
        + [{"kind": "task", "id": t.id} for t in tasks]
        + [{"kind": "message", "id": m.id} for m in msgs]
        + [{"kind": "person", "id": p.id} for p in persons]
    )


def prepend_card(db: Session, column_id: int | None, kind: str, uid: int) -> None:
    """Reiht ein neu erstelltes Item ganz oben (Position 0) in die card_order der
    Zielspalte ein, statt es (wie beim Default-Sort) am Ende anzuhängen.

    Wird von allen "neu hinzufügen"-Aktionen (Auftrag/Meldung/Person, manuell
    hinzugefügte Einheit in einen benannten Abschnitt) aufgerufen, damit neue
    Karten immer ganz oben in der Lane erscheinen.
    """
    import json as _json
    if not column_id:
        return
    col = db.get(IncidentColumn, column_id)
    if not col:
        return

    current = _card_order_or_default(db, col)
    # Falls schon (fälschlich) vorhanden, zuerst entfernen, dann ganz oben einfügen.
    current = [item for item in current if not (item.get("kind") == kind and item.get("id") == uid)]
    current.insert(0, {"kind": kind, "id": uid})
    col.card_order = _json.dumps(current)
    db.flush()


def append_card(db: Session, column_id: int | None, kind: str, uid: int) -> None:
    """Reiht ein Item ganz UNTEN in die card_order der Zielspalte ein.

    Wird für Fahrzeuge verwendet, die in "Tatsächlich im Einsatz" ankommen
    (Status S4 per LIS oder manuell) — dort soll die Reihenfolge chronologisch
    bleiben (wer zuerst S4 meldet, steht oben), nicht wie bei anderen neuen
    Karten ganz oben.
    """
    import json as _json
    if not column_id:
        return
    col = db.get(IncidentColumn, column_id)
    if not col:
        return

    current = _card_order_or_default(db, col)
    current = [item for item in current if not (item.get("kind") == kind and item.get("id") == uid)]
    current.append({"kind": kind, "id": uid})
    col.card_order = _json.dumps(current)
    db.flush()


def sink_done_cards(db: Session, column_id: int | None) -> None:
    """Verschiebt erledigte/stornierte Karten ans Ende der card_order (unterhalb aller aktiven)."""
    import json as _json
    if not column_id:
        return
    col = db.get(IncidentColumn, column_id)
    if not col:
        return

    done_tasks = {
        t.id for t in db.query(Task).filter(Task.column_id == column_id, Task.vehicle_id.is_(None)).all()
        if t.is_done or t.is_cancelled
    }
    done_msgs = {
        m.id for m in db.query(Message).filter(Message.column_id == column_id).all()
        if m.is_done or m.is_cancelled
    }

    try:
        current: list[dict] = _json.loads(col.card_order) if col.card_order else []
    except Exception:
        current = []

    if not current:
        # Build minimal order from DB when no card_order exists yet
        vehicles = db.query(IncidentVehicle).filter(
            IncidentVehicle.column_id == column_id,
            IncidentVehicle.removed_at.is_(None),
        ).all()
        tasks = db.query(Task).filter(Task.column_id == column_id, Task.vehicle_id.is_(None)).all()
        msgs = db.query(Message).filter(Message.column_id == column_id).all()
        current = (
            [{"kind": "vehicle", "id": v.id} for v in vehicles]
            + [{"kind": "task", "id": t.id} for t in tasks]
            + [{"kind": "message", "id": m.id} for m in msgs]
        )

    active, done = [], []
    for item in current:
        kind = item.get("kind")
        uid = item.get("id")
        if (kind == "task" and uid in done_tasks) or (kind == "message" and uid in done_msgs):
            done.append(item)
        else:
            active.append(item)

    col.card_order = _json.dumps(active + done)
    db.flush()


def delete_section_column(db: Session, column: IncidentColumn, user_id: int | None = None) -> None:
    """Löscht eine nicht-fixierte Spalte. Wirft ValueError wenn Elemente zugeordnet sind."""
    if column.is_fixed:
        raise ValueError("Fixierte Spalten können nicht gelöscht werden.")

    active_vehicles = db.query(IncidentVehicle).filter(
        IncidentVehicle.column_id == column.id,
        IncidentVehicle.removed_at.is_(None),
    ).count()
    tasks = db.query(Task).filter(
        Task.column_id == column.id,
        Task.vehicle_id.is_(None),
    ).count()
    msgs = db.query(Message).filter(Message.column_id == column.id).count()

    parts = []
    if active_vehicles:
        parts.append(f"{active_vehicles} Einheit{'en' if active_vehicles != 1 else ''}")
    if tasks:
        parts.append(f"{tasks} Auftrag{'äge' if tasks != 1 else ''}")
    if msgs:
        parts.append(f"{msgs} Meldung{'en' if msgs != 1 else ''}")

    if parts:
        raise ValueError(
            f"Spalte kann nicht gelöscht werden – es sind noch {', '.join(parts)} zugeordnet. "
            "Bitte zuerst alle Elemente verschieben oder entfernen."
        )

    write_incident_change(
        db, column.incident_id, "column.deleted", "incident_column", column.id,
        before={"title": column.title}, after=None,
        user_id=user_id,
    )
    db.delete(column)
    db.flush()


def move_card(
    db: Session,
    incident_id: int,
    kind: str,
    uid: int,
    column_id: int | None = None,
    position: int = 0,
    vehicle_id: int | None = None,
    user_id: int | None = None,
    detach_vehicle: bool = False,
) -> None:
    """Generic card move for DnD. kind: 'vehicle'|'task'|'message'.

    detach_vehicle: True nur, wenn die Karte aus einer Fahrzeug-Zone heraus auf eine Spalte
    gezogen wurde (bewusstes Lösen der Einheiten-Zuordnung). Beim reinen Umsortieren einer
    mit einer Einheit verbundenen Karte innerhalb/zwischen Spalten bleibt die vehicle_id
    erhalten — sonst ginge die Verbindung zur Einheit beim Verschieben verloren.
    """
    if kind == "vehicle":
        vehicle = db.get(IncidentVehicle, uid)
        if not vehicle:
            return
        col = db.get(IncidentColumn, column_id)
        if not col:
            return
        before = {"column_id": vehicle.column_id, "display_order": vehicle.display_order,
                  "unit_status": vehicle.unit_status}
        # Reorder other vehicles in target column
        siblings = (
            db.query(IncidentVehicle)
            .filter(
                IncidentVehicle.incident_id == incident_id,
                IncidentVehicle.column_id == column_id,
                IncidentVehicle.id != uid,
                IncidentVehicle.removed_at.is_(None),
            )
            .order_by(IncidentVehicle.display_order)
            .all()
        )
        for i, sib in enumerate(siblings):
            sib.display_order = i if i < position else i + 1
        vehicle.column_id = column_id  # type: ignore[assignment]
        vehicle.display_order = position
        # Sync Spalte → Unit-Status: manuelles Verschieben in "active" setzt den Status.
        if col.code == "active" and vehicle.unit_status != "Am Einsatzort":
            vehicle.unit_status = "Am Einsatzort"
        db.flush()
        write_incident_change(
            db, incident_id, "vehicle.moved", "incident_vehicle", uid,
            before=before, after={"column_id": column_id, "display_order": position,
                                   "unit_status": vehicle.unit_status},
            user_id=user_id,
        )

    elif kind == "task":
        task = db.get(Task, uid)
        if not task:
            return
        before = {"column_id": task.column_id, "vehicle_id": task.vehicle_id, "display_order": task.display_order}
        if vehicle_id:
            # Drop on a vehicle
            v = db.get(IncidentVehicle, vehicle_id)
            if not v:
                return
            task.vehicle_id = vehicle_id
            task.column_id = None
            db.flush()
            write_incident_change(
                db, incident_id, "task.assigned", "task", uid,
                before=before, after={"vehicle_id": vehicle_id},
                user_id=user_id,
            )
            if v.vehicle_master_id:
                from app.services.push_service import notify_vehicle
                notify_vehicle(db, v.vehicle_master_id, "📋 Neuer Auftrag", task.title,
                               url=f"/einsatz/{incident_id}?open_task={uid}")
        elif column_id:
            # Drop on a column — reorder siblings first
            siblings = (
                db.query(Task)  # type: ignore[assignment]
                .filter(
                    Task.incident_id == incident_id,
                    Task.column_id == column_id,
                    Task.id != uid,
                )
                .order_by(Task.display_order)
                .all()
            )
            for i, sib in enumerate(siblings):
                sib.display_order = i if i < position else i + 1
            # Einheiten-Zuordnung nur lösen, wenn die Karte bewusst aus der Fahrzeug-Zone
            # gezogen wurde. Beim reinen Umsortieren auf dem Board bleibt sie erhalten.
            if detach_vehicle:
                task.vehicle_id = None
            task.column_id = column_id
            task.display_order = position
            db.flush()
            write_incident_change(
                db, incident_id, "task.moved", "task", uid,
                before=before, after={"column_id": column_id, "display_order": position,
                                      "vehicle_id": task.vehicle_id},
                user_id=user_id,
            )

    elif kind == "message":
        from app.models.incident import Message as Msg
        msg = db.get(Msg, uid)
        if not msg:
            return
        before = {"display_order": msg.display_order, "vehicle_id": msg.vehicle_id, "column_id": msg.column_id}
        if vehicle_id:
            v = db.get(IncidentVehicle, vehicle_id)
            if not v:
                return
            msg.vehicle_id = vehicle_id
            db.flush()
            write_incident_change(
                db, incident_id, "message.assigned", "message", uid,
                before=before, after={"vehicle_id": vehicle_id},
                user_id=user_id,
            )
            if v.vehicle_master_id:
                from app.services.push_service import notify_vehicle
                notify_vehicle(db, v.vehicle_master_id, "📩 Neue Meldung", msg.title,
                               url=f"/einsatz/{incident_id}?open_msg={uid}")
        elif column_id:
            # Drop on a column — reorder siblings first
            siblings = (
                db.query(Message)  # type: ignore[assignment]
                .filter(
                    Message.incident_id == incident_id,
                    Message.column_id == column_id,
                    Message.id != uid,
                )
                .order_by(Message.display_order)
                .all()
            )
            for i, sib in enumerate(siblings):
                sib.display_order = i if i < position else i + 1
            # Einheiten-Zuordnung nur lösen, wenn die Karte bewusst aus der Fahrzeug-Zone
            # gezogen wurde (siehe move_card-Docstring / task-Zweig).
            if detach_vehicle:
                msg.vehicle_id = None
            msg.column_id = column_id
            msg.display_order = position
            db.flush()
            write_incident_change(
                db, incident_id, "message.moved", "message", uid,
                before=before,
                after={"column_id": column_id, "display_order": position, "vehicle_id": msg.vehicle_id},
                user_id=user_id,
            )

    elif kind == "person":
        from app.models.incident import RescuedPerson
        person = db.get(RescuedPerson, uid)
        if not person:
            return
        before = {"vehicle_id": person.vehicle_id}
        if vehicle_id:
            v = db.get(IncidentVehicle, vehicle_id)
            if not v:
                return
            person.vehicle_id = vehicle_id
            db.flush()
            write_incident_change(
                db, incident_id, "person.assigned", "rescued_person", uid,
                before=before, after={"vehicle_id": vehicle_id},
                user_id=user_id,
            )
        else:
            person.vehicle_id = None
            db.flush()
            write_incident_change(
                db, incident_id, "person.moved", "rescued_person", uid,
                before=before, after={"vehicle_id": None},
                user_id=user_id,
            )


def enrich_history(changes, db, incident_id: int) -> list[dict]:
    """Convert raw IncidentChange records to human-readable dicts for the template."""
    import json as _json

    tasks    = {t.id: t for t in db.query(Task).filter_by(incident_id=incident_id).all()}
    msgs     = {m.id: m for m in db.query(Message).filter_by(incident_id=incident_id).all()}
    vehicles = {v.id: v for v in db.query(IncidentVehicle).filter_by(incident_id=incident_id).all()}
    columns  = {c.id: c for c in db.query(IncidentColumn).filter_by(incident_id=incident_id).all()}
    persons  = {p.id: p for p in db.query(RescuedPerson).filter_by(incident_id=incident_id).all()}

    user_ids = {c.user_id for c in changes if c.user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    member_ids: set[int] = set()
    for c in changes:
        if c.after_json:
            try:
                _after = _json.loads(c.after_json)
                for key in ("commander_member_id", "section_leader_member_id"):
                    mid = _after.get(key)
                    if mid:
                        member_ids.add(mid)
            except Exception:
                pass
    members = {m.id: m for m in db.query(Member).filter(Member.id.in_(member_ids)).all()} if member_ids else {}

    def vname(vid):
        v = vehicles.get(vid)
        return v.vehicle_master.name if v and v.vehicle_master else f"Fahrzeug #{vid}"

    def cname(cid):
        c = columns.get(cid)
        return c.title if c else f"Spalte #{cid}"

    def ttitle(tid):
        t = tasks.get(tid)
        return t.title if t else f"Auftrag #{tid}"

    def mtitle(mid):
        m = msgs.get(mid)
        return m.title if m else f"Meldung #{mid}"

    def pname(pid):
        # Name führend – Fallback auf Gruppe/Ort, damit im Journal immer ein
        # aussagekräftiger Bezeichner steht statt nur der internen ID.
        p = persons.get(pid)
        if p and p.name:
            return p.name
        if p and (p.person_group or p.location):
            return p.person_group or p.location
        return f"Person #{pid}"

    STATUS_DE = {
        "meldung":     "Meldung (aktiv)",
        "achtung":     "Achtung",
        "hinweis":     "Hinweis",
        "information": "Information",
        "erledigt":    "Erledigt",
        "storniert":   "Storniert",
        # Legacy
        "done": "Erledigt", "cancelled": "Storniert",
        "open": "Meldung (aktiv)", "in_progress": "Achtung",
        "yellow": "In Bearbeitung", "red": "Dringend",
    }

    result = []
    for change in changes:
        before: dict = {}
        after: dict = {}
        try:
            if change.before_json:
                before = _json.loads(change.before_json)
        except Exception:
            pass
        try:
            if change.after_json:
                after = _json.loads(change.after_json)
        except Exception:
            pass

        action = change.action
        eid = change.entity_id
        summary = action

        if action == "task.created":
            summary = f'Auftrag erstellt: "{after.get("title") or ttitle(eid)}"'
        elif action == "task.updated":
            old_t = before.get("title", "")
            new_t = after.get("title", "")
            if old_t and new_t and old_t != new_t:
                summary = f'Auftrag umbenannt: "{old_t}" -> "{new_t}"'
            else:
                summary = f'Auftrag bearbeitet: "{new_t or ttitle(eid)}"'
        elif action == "task.moved":
            to_col = cname(after.get("column_id"))
            from_col = cname(before.get("column_id")) if before.get("column_id") else None
            t = ttitle(eid)
            summary = (f'Auftrag "{t}": {from_col} -> {to_col}'
                       if from_col and from_col != to_col
                       else f'Auftrag "{t}" verschoben nach {to_col}')
        elif action == "task.assigned":
            vid = after.get("vehicle_id")
            t = ttitle(eid)
            summary = (f'Auftrag "{t}" -> {vname(vid)}'
                       if vid else f'Auftrag "{t}": Fahrzeugzuweisung entfernt')
        elif action == "task.status_set":
            st = STATUS_DE.get(after.get("status", ""), after.get("status", ""))
            summary = f'Auftrag "{ttitle(eid)}": {st}'
        elif action == "task.cancelled":
            summary = f'Auftrag storniert: "{ttitle(eid)}"'
        elif action == "task.restored":
            summary = f'Auftrag wiederhergestellt: "{ttitle(eid)}"'
        elif action == "vehicle.moved":
            to_col = cname(after.get("column_id"))
            from_col = cname(before.get("column_id")) if before.get("column_id") else None
            v = vname(eid)
            summary = (f'Fahrzeug {v}: {from_col} → {to_col}'
                       if from_col and from_col != to_col
                       else f'Fahrzeug {v} → {to_col}')
        elif action == "vehicle.commander_set":
            el_name = after.get("incident_leader_member")
            mid = after.get("commander_member_id")
            if el_name:
                summary = f'Einsatzleiter gesetzt: {el_name}'
            elif mid:
                m = members.get(mid)
                summary = f'Gruppenkommandant {vname(eid)}: {m.full_name if m else f"#{mid}"}'
            else:
                summary = f'Gruppenkommandant {vname(eid)} entfernt'
        elif action == "vehicle.status_set":
            summary = f'Fahrzeug {vname(eid)}: {after.get("unit_status", "")}'
        elif action == "message.created":
            summary = f'Meldung erstellt: "{after.get("title") or mtitle(eid)}"'
        elif action == "message.updated":
            old_t = before.get("title", "")
            new_t = after.get("title", "")
            if old_t and new_t and old_t != new_t:
                summary = f'Meldung umbenannt: "{old_t}" -> "{new_t}"'
            else:
                summary = f'Meldung bearbeitet: "{new_t or mtitle(eid)}"'
        elif action == "message.status_set":
            st = STATUS_DE.get(after.get("status", ""), after.get("status", ""))
            summary = f'Meldung "{mtitle(eid)}": {st}'
        elif action == "message.assigned":
            vid = after.get("vehicle_id")
            summary = f'Meldung "{mtitle(eid)}" -> {vname(vid) if vid else "-"}'
        elif action == "message.moved":
            summary = f'Meldung "{mtitle(eid)}" verschoben'
        elif action == "person.created":
            nm = after.get("name") or pname(eid)
            vid = after.get("vehicle_id")
            summary = f'Person erfasst: {nm}' + (f' → {vname(vid)}' if vid else '')
        elif action == "person.updated":
            nm = after.get("name") or pname(eid)
            summary = f'Person bearbeitet: {nm}'
        elif action == "person.status_set":
            nm = pname(eid)
            st = after.get("status", "")
            summary = f'{nm}: Status → {st}'
        elif action == "person.assigned":
            nm = pname(eid)
            vid = after.get("vehicle_id")
            summary = f'{nm} → {vname(vid) if vid else "—"}'
        elif action == "person.moved":
            summary = f'{pname(eid)}: Fahrzeugzuweisung aufgehoben'
        elif action == "column.created":
            summary = f'Neue Sektion erstellt: "{after.get("title", f"#{eid}")}"'
        elif action == "column.title_set":
            summary = f'Abschnitt umbenannt: "{before.get("title", "")}" -> "{after.get("title", "")}"'
        elif action == "column.section_leader_set":
            nm = after.get("section_leader_name")
            mid = after.get("section_leader_member_id")
            if not nm and mid:
                m = members.get(mid)
                nm = m.full_name if m else f"#{mid}"
            summary = f'Abschnittsleiter "{cname(eid)}": {nm}' if nm else f'Abschnittsleiter "{cname(eid)}" entfernt'
        elif action == "troop.meldung":
            txt = after.get("text") or ""
            summary = f'AS-Trupp Lagemeldung: "{txt}"' if txt else f'AS-Trupp #{eid}: Lagemeldung abgesetzt'
        elif action == "troop.created":
            summary = f'AS-Trupp angelegt: "{after.get("name", f"#{eid}")}"'
        elif action == "troop.started":
            summary = f'AS-Trupp eingesetzt: "{after.get("name", f"#{eid}")}"'
        elif action.startswith("troop.warn_acked."):
            kind_map = {"one_third": "1/3-Lagemeldung", "max_time": "Max-Einsatzzeit", "withdraw": "Rückzugsdruck"}
            kind = action.split(".")[-1]
            summary = f'AS-Warnung quittiert: {kind_map.get(kind, kind)}'
        elif action == "troop.status":
            status_map = {"im_einsatz": "Im Einsatz", "rueckzug": "Rückzug", "zurueck": "Zurück", "erholt": "Erholt"}
            summary = f'AS-Trupp Status: {status_map.get(after.get("status", ""), after.get("status", ""))}'
        elif action == "as_pruefung.created":
            geraet_label = after.get("geraet_label", f"#{eid}")
            if after.get("alles_ok"):
                summary = f'Atemschutz-Geräteprüfung: Gerät {geraet_label} – i.O.'
            else:
                punkte = after.get("defekte_punkte", "")
                summary = f'Atemschutz-Geräteprüfung: Gerät {geraet_label} – NICHT i.O. ({punkte})'

        actor = ""
        if change.user_id:
            u = users.get(change.user_id)
            actor = u.display_name if u else f"Benutzer #{change.user_id}"
        elif change.api_key_id:
            actor = "API"

        result.append({"ts": change.ts, "summary": summary, "actor": actor})

    return result


def card_journal(db: Session, incident_id: int, entity_type: str, entity_id: int, limit: int = 20) -> list[dict]:
    """Lädt die letzten IncidentChange-Einträge einer Karte (Einheit/Auftrag/Meldung/Person)
    und rendert sie über enrich_history zu lesbaren "Verlauf"-Zeilen — analog zur
    Major-Incident SiteLogEntry-Anzeige, aber aus dem bereits vorhandenen Change-Log gespeist.
    """
    raw = (
        db.query(IncidentChange)
        .filter(
            IncidentChange.incident_id == incident_id,
            IncidentChange.entity_type == entity_type,
            IncidentChange.entity_id == entity_id,
        )
        .order_by(IncidentChange.ts.desc())
        .limit(limit)
        .all()
    )
    return enrich_history(raw, db, incident_id)


def combined_verlauf(db: Session, incident_id: int, limit: int | None = None) -> list[dict]:
    """Kombiniert das strukturierte Karten-Journal (IncidentChange) mit manuellen
    Freitext-Notizen (IncidentLog) zu einem einzigen chronologischen Verlauf, damit
    Board-Sidebar, Vollansicht (/historie) und der Einsatz-Ausdruck dieselben Einträge
    zeigen wie das Karten-Journal in den Detail-Modals – bisher blieben Statusänderungen,
    Zuweisungen etc. dort unsichtbar, weil nur die Notizen (IncidentLog) angezeigt wurden.
    """
    changes_q = (
        db.query(IncidentChange)
        .filter(IncidentChange.incident_id == incident_id)
        .order_by(IncidentChange.ts.desc())
    )
    logs_q = (
        db.query(IncidentLog)
        .filter(IncidentLog.incident_id == incident_id)
        .order_by(IncidentLog.ts.desc())
    )
    if limit:
        changes_q = changes_q.limit(limit)
        logs_q = logs_q.limit(limit)

    entries = enrich_history(changes_q.all(), db, incident_id)
    for e in entries:
        e["level"] = ""

    for log in logs_q.all():
        entries.append({
            "ts": log.ts,
            "summary": log.text,
            "actor": log.author_name or "",
            "level": log.level or "",
        })

    entries.sort(key=lambda e: e["ts"], reverse=True)
    if limit:
        entries = entries[:limit]
    return entries
