"""Core incident business logic – mirrors startIncident(), changeAlarm() from the HTML version."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.audit import write_audit, write_incident_change
from app.models.incident import (
    FIXED_COLUMN_TITLES,
    FIXED_COLUMNS,
    TRAFFIC_LIGHT_VALUES,
    UNIT_STATUS_VALUES,
    Incident,
    IncidentColumn,
    IncidentVehicle,
    Message,
    Task,
)
from app.models.master import (
    AlarmDispatchVehicle,
    AlarmType,
    DefaultMessage,
    Member,
    MemberQualification,
    Qualification,
    VehicleMaster,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_org_id(db: Session, requested: int | None) -> int | None:
    """Liefert eine gültige fire_dept.id zurück.

    Reihenfolge: 1) explizit übergeben, 2) Home-Org (is_home_org=True),
    3) erste FireDept-Zeile, 4) None (keine Org in der DB → kein Fallback möglich).
    """
    from app.models.master import FireDept
    if requested:
        if db.get(FireDept, requested):
            return requested
    home = db.query(FireDept).filter(FireDept.is_home_org == True).first()  # noqa: E712
    if home:
        return home.id
    any_org = db.query(FireDept).order_by(FireDept.id).first()
    return any_org.id if any_org else None


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
    incident_leader_user_id: int | None = None,
    primary_org_id: int | None = None,
    api_key_id: int | None = None,
    ip: str | None = None,
) -> Incident:
    import hashlib
    import secrets as _secrets
    from app.models.lagekarte import LagekarteToken

    alarm = db.get(AlarmType, alarm_type_code)
    if alarm is None:
        alarm_type_code = "T1"
        alarm = db.get(AlarmType, "T1")  # ohne re-fetch wäre _populate_vehicles ein No-op

    resolved_org_id = _resolve_org_id(db, primary_org_id)
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
        incident_leader_user_id=incident_leader_user_id,
        primary_org_id=resolved_org_id,
        auto_geojson_token=raw_token,
    )
    db.add(incident)
    db.flush()  # get id

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


def _create_fixed_columns(db: Session, incident: Incident) -> None:
    for i, code in enumerate(FIXED_COLUMNS):
        col = IncidentColumn(
            incident_id=incident.id,
            code=code,
            title=FIXED_COLUMN_TITLES[code],
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


def _populate_vehicles(db: Session, incident: Incident, alarm: AlarmType | None) -> None:
    if alarm is None:
        return

    db.refresh(incident, ["columns"])
    dispatched_col = _get_column(incident, "dispatched")
    neighbor_col = _get_column(incident, "neighbor")
    if not dispatched_col:
        return

    from app.models.master import FireDept

    # Check if explicit dispatch order exists for this alarm type
    dispatch_entries = (
        db.query(AlarmDispatchVehicle)
        .filter(AlarmDispatchVehicle.alarm_type_code == alarm.code)
        .order_by(AlarmDispatchVehicle.display_order)
        .all()
    )

    if dispatch_entries:
        # Use explicit dispatch order
        for i, entry in enumerate(dispatch_entries):
            vm = db.get(VehicleMaster, entry.vehicle_master_id)
            if vm and vm.active:
                db.add(IncidentVehicle(
                    incident_id=incident.id,
                    column_id=dispatched_col.id,
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
                column_id=dispatched_col.id,
                vehicle_master_id=vm.id,
                display_order=i,
            ))

    # Neighbor vehicles go into 'neighbor' column (only in fallback mode if notify_neighbors)
    if alarm and alarm.notify_neighbors and neighbor_col and not dispatch_entries:
        neighbor_q = (
            db.query(VehicleMaster)
            .join(VehicleMaster.dept)
            .filter(VehicleMaster.active == True)  # noqa: E712
            .order_by(VehicleMaster.display_order)
        )
        from app.models.master import FireDept as FD
        neighbor_q = neighbor_q.filter(FD.slug != "wolfurt")
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
    msgs = db.query(DefaultMessage).filter(DefaultMessage.alarm_type_code == alarm.code).all()
    for i, dm in enumerate(msgs):
        due_at = None
        if incident.started_at and dm.due_after_sec:
            from datetime import timedelta
            started = incident.started_at if incident.started_at.tzinfo else incident.started_at.replace(tzinfo=UTC)
            due_at = started + timedelta(seconds=dm.due_after_sec)
        db.add(Message(
            incident_id=incident.id,
            title=dm.text,
            due_after_sec=dm.due_after_sec,
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


def close_incident(db: Session, incident: Incident, user_id: int | None = None) -> Incident:
    incident.status = "closed"
    incident.closed_at = _now()
    # Revoke all QR tokens
    for token in incident.tokens:
        if token.revoked_at is None:
            token.revoked_at = _now()
    db.flush()
    write_audit(db, "incident.closed", incident_id=incident.id, user_id=user_id)
    return incident


def add_section_column(
    db: Session,
    incident: Incident,
    title: str,
    user_id: int | None = None,
) -> IncidentColumn:
    max_order = max((c.display_order for c in incident.columns), default=0)
    col = IncidentColumn(
        incident_id=incident.id,
        code=f"section_{_now().timestamp():.0f}",
        title=title,
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


def quick_create_commander(
    db: Session,
    vehicle: IncidentVehicle,
    full_name: str,
    user_id: int | None = None,
) -> IncidentVehicle:
    """Create a Member from a name string and assign as commander."""
    parts = full_name.strip().split(None, 1)
    firstname = parts[0] if parts else full_name
    lastname = parts[1] if len(parts) > 1 else ""
    dept_id = vehicle.vehicle_master.dept_id if vehicle.vehicle_master else None
    member = Member(firstname=firstname, lastname=lastname, org_id=dept_id, active=True)
    db.add(member)
    db.flush()
    return set_commander(db, vehicle, member.id, user_id=user_id)


def quick_create_el(
    db: Session,
    incident: Incident,
    full_name: str,
    user_id: int | None = None,
) -> Incident:
    """Create a Member from a free-text name and assign as incident leader (vor Ort)."""
    parts = full_name.strip().split(None, 1)
    firstname = parts[0] if parts else full_name
    lastname = parts[1] if len(parts) > 1 else ""
    member = Member(firstname=firstname, lastname=lastname, org_id=incident.primary_org_id, active=True)
    db.add(member)
    db.flush()
    incident.incident_leader_member_id = member.id
    db.flush()
    write_incident_change(
        db, incident.id, "vehicle.commander_set", "incident", incident.id,
        before=None, after={"incident_leader_member": full_name},
        user_id=user_id,
    )
    return incident


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
    # Sync: Status "Am Einsatzort" verschiebt das Fahrzeug in die Spalte "active"
    if status == "Am Einsatzort":
        active_col = db.query(IncidentColumn).filter_by(
            incident_id=vehicle.incident_id, code="active"
        ).first()
        if active_col and vehicle.column_id != active_col.id:
            vehicle.column_id = active_col.id
            vehicle.display_order = _next_display_order(db, vehicle.incident_id, active_col.id)
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
    if status not in TRAFFIC_LIGHT_VALUES:
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


def move_card(
    db: Session,
    incident_id: int,
    kind: str,
    uid: int,
    column_id: int | None = None,
    position: int = 0,
    vehicle_id: int | None = None,
    user_id: int | None = None,
) -> None:
    """Generic card move for DnD. kind: 'vehicle'|'task'|'message'."""
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
        vehicle.column_id = column_id
        vehicle.display_order = position
        # Bidirektionaler Sync Spalte ↔ Unit-Status
        if col.code == "active" and vehicle.unit_status != "Am Einsatzort":
            vehicle.unit_status = "Am Einsatzort"
        elif col.code == "dispatched" and vehicle.unit_status == "Am Einsatzort":
            vehicle.unit_status = "Einsatz übernommen"
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
        elif column_id:
            # Drop on a column
            task.vehicle_id = None
            task.column_id = column_id
            task.display_order = position
            db.flush()
            write_incident_change(
                db, incident_id, "task.moved", "task", uid,
                before=before, after={"column_id": column_id, "display_order": position},
                user_id=user_id,
            )

    elif kind == "message":
        from app.models.incident import Message as Msg
        msg = db.get(Msg, uid)
        if not msg:
            return
        before = {"display_order": msg.display_order, "vehicle_id": msg.vehicle_id}
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
        else:
            msg.vehicle_id = None
            msg.display_order = position
            db.flush()
            write_incident_change(
                db, incident_id, "message.moved", "message", uid,
                before=before, after={"display_order": position, "vehicle_id": None},
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
