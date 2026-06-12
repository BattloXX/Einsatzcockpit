"""UI Router – Einsatz-Board (HTMX-Endpoints)."""
import base64
import hashlib
import io
import logging
from datetime import UTC, datetime

import qrcode
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.permissions import can_access_incident, has_role, require_role
from app.core.queries import visible_incidents_q
from app.core.security import (
    get_author_name,
    hash_pin,
    sign_pin_access_token,
    sign_qr_token,
    unsign_pin_access_token,
    verify_pin,
)
from app.core.templating import templates
from app.db import get_db
from app.models.breathing import BreathingTroop
from app.models.incident import (
    PERSON_STATUS_VALUES,
    UNIT_STATUS_VALUES,
    Incident,
    IncidentColumn,
    IncidentCommLog,
    IncidentLog,
    IncidentOrg,
    IncidentToken,
    IncidentVehicle,
    Message,
    MessageMedia,
    PersonMedia,
    RescuedPerson,
    Task,
)
from app.models.master import (
    BOS_VALUES,
    AlarmDispatchVehicle,
    AlarmType,
    FireDept,
    LageHint,
    LageHintAlarm,
    Member,
    MemberQualification,
    MessageSuggestion,
    MessageSuggestionAlarm,
    OrgSettings,
    Qualification,
    SystemSettings,
    TaskSuggestion,
    TaskSuggestionAlarm,
    VehicleMaster,
)
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus
from app.models.user import Role, User, UserRole
from app.config import settings
from app.services.ai_service import is_enabled as ai_is_enabled
from app.services.alarm_service import get_alarm_type_by_code
from app.services.broadcast import broadcast_org, manager
from app.services.incident_service import (
    add_section_column,
    add_task,
    assign_task_to_vehicle,
    cancel_task,
    close_incident,
    collect_situation_context,
    create_incident,
    delete_section_column,
    list_commander_candidates,
    list_el_candidates,
    move_card,
    move_vehicle_to_column,
    reopen_incident,
    reorder_columns,
    set_commander,
    set_message_status,
    set_task_status,
    set_unit_status,
    sink_done_cards,
    update_column_card_order,
    update_task,
)

router = APIRouter()
_log = logging.getLogger(__name__)


async def _trigger_ai_task_suggestions(
    incident_id: int, meldung: str, einsatzart: str, org_id: int | None = None,
) -> None:
    """Background: KI-Auftragsvorschläge generieren und an Board broadcasten."""
    from app.db import SessionLocal
    from app.services.ai_service import suggest_tasks

    suggestions = await suggest_tasks(meldung, einsatzart, org_id=org_id)
    if not suggestions:
        return
    from app.core.tenant import set_tenant_context
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.get(Incident, incident_id)
        if not incident:
            return
        # Alte offene KI-Vorschläge entfernen, damit keine Duplikate entstehen
        for t in list(incident.tasks):
            if t.source == "ai_suggestion" and not t.is_done and not t.is_cancelled:
                db.delete(t)
        db.flush()
        tasks_col = next((c for c in incident.columns if c.code == "tasks"), None)
        for i, s in enumerate(suggestions):
            db.add(Task(
                incident_id=incident_id,
                column_id=tasks_col.id if tasks_col else None,
                title=s["titel"],
                detail=s.get("detail"),
                source="ai_suggestion",
                display_order=1000 + i,
            ))
        db.commit()
        await manager.broadcast(incident_id, {
            "type": "ai_suggestions_ready",
            "reload_board": True,
            "count": len(suggestions),
        })
    except Exception:
        _log.exception("KI-Auftragsvorschläge Fehler für Einsatz %d", incident_id)
    finally:
        db.close()


def _prepend_ai_hints(incident: Incident, master_hints: list) -> list:
    """Prepend AI-generated hints (stored as JSON on incident) to master hints list."""
    import json as _json
    from types import SimpleNamespace
    if not incident.ai_lage_hints:
        return master_hints
    try:
        texts = _json.loads(incident.ai_lage_hints)
        if not isinstance(texts, list):
            return master_hints
        ai_objs = [SimpleNamespace(text=t, is_ai=True) for t in texts if isinstance(t, str) and t.strip()]
        return ai_objs + list(master_hints)
    except Exception:
        return master_hints


def _incident_or_404(incident_id: int, db: Session):
    inc = db.get(Incident, incident_id)
    if not inc:
        from fastapi import HTTPException
        raise HTTPException(404, "Einsatz nicht gefunden")
    return inc


_visible_incidents_q = visible_incidents_q


def _create_neighbor_invitations(
    db: Session, incident, alarm_type_code: str, org_id: int | None, user_id: int | None
) -> None:
    """Erstellt pending OrgInvitations für alle Partner-Orgs wenn notify_neighbors aktiv."""
    if org_id is None:
        return
    from app.models.master import AlarmType
    alarm = (
        db.query(AlarmType)
        .filter(AlarmType.org_id == org_id, AlarmType.code == alarm_type_code)
        .first()
    )
    if not alarm or not alarm.notify_neighbors:
        return
    from app.models.invitation import OrgInvitation, OrgPartner
    partners = db.query(OrgPartner).filter(
        OrgPartner.org_id == org_id,
        OrgPartner.notify_on_incident == True,  # noqa: E712
    ).all()
    for p in partners:
        existing = db.query(OrgInvitation).filter(
            OrgInvitation.incident_id == incident.id,
            OrgInvitation.invited_org_id == p.partner_org_id,
        ).first()
        if not existing:
            db.add(OrgInvitation(
                incident_id=incident.id,
                inviting_org_id=org_id,
                invited_org_id=p.partner_org_id,
                status="pending",
                created_by_user_id=user_id,
            ))
    db.flush()


def _entity_logs(db: Session, incident_id: int, entity_type: str, entity_id: int) -> list:
    return (
        db.query(IncidentLog)
        .filter(
            IncidentLog.incident_id == incident_id,
            IncidentLog.entity_type == entity_type,
            IncidentLog.entity_id == entity_id,
        )
        .order_by(IncidentLog.ts)
        .all()
    )


# ── Dashboard / Index ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        # Nicht angemeldet → öffentliche Startseite (Funktionsumfang, Kontakt).
        from app.routers.public import render_public_page
        return render_public_page(request, db, "landing",
                                  kontakt=request.query_params.get("kontakt"))
    active_major = (
        db.query(MajorIncident)
        .filter(MajorIncident.status == MajorIncidentStatus.active)
        .order_by(MajorIncident.started_at.desc())
        .all()
    )
    adopted_ids = (
        db.query(IncidentSite.incident_id)
        .join(MajorIncident, IncidentSite.major_incident_id == MajorIncident.id)
        .filter(
            MajorIncident.status == MajorIncidentStatus.active,
            IncidentSite.incident_id.isnot(None),
        )
        .all()
    )
    adopted_id_set = {row[0] for row in adopted_ids}
    active = (
        _visible_incidents_q(db, user)
        .filter(Incident.status == "active", ~Incident.id.in_(adopted_id_set))
        .order_by(Incident.started_at.desc())
        .all()
    )
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    org = getattr(user, "org", None)
    default_city = (org.city if org and org.city else settings.DEFAULT_INCIDENT_CITY)
    return templates.TemplateResponse(request, "index.html", {
        "user": user,
        "active_incidents": active,
        "active_major_incidents": active_major,
        "alarm_types": alarm_types,
        "default_city": default_city,
    })


# ── Einsatz starten (manuell) ─────────────────────────────────────────────────

@router.post("/einsatz/neu")
async def new_incident(
    request: Request,
    background_tasks: BackgroundTasks,
    alarm_type_code: str = Form(...),
    address_street: str = Form(""),
    address_no: str = Form(""),
    address_city: str = Form(""),
    report_text: str = Form(""),
    is_exercise: bool = Form(False),
    lat: str = Form(""),
    lng: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    from app.services.geocoding import geocode_address as _geocode

    user = request.state.user

    # Ort aus Org-Stammdaten wenn nicht angegeben
    if not address_city.strip():
        _org = getattr(user, "org", None)
        address_city = (_org.city if _org and _org.city else settings.DEFAULT_INCIDENT_CITY)

    lat_f: float | None = None
    lng_f: float | None = None
    try:
        if lat.strip():
            lat_f = float(lat)
        if lng.strip():
            lng_f = float(lng)
    except ValueError:
        pass

    incident = create_incident(
        db, alarm_type_code=alarm_type_code,
        address_street=address_street or None,
        address_no=address_no or None,
        address_city=address_city or None,
        lat=lat_f,
        lng=lng_f,
        report_text=report_text or None,
        is_exercise=is_exercise,
        incident_leader_user_id=user.id,
        primary_org_id=user.org_id,
        ip=request.client.host if request.client else None,
    )

    # Org-Standard-PIN auf neuen Einsatz übertragen
    if user.org_id:
        _org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
        if _org_settings and _org_settings.default_access_pin_hash:
            incident.access_pin_hash = _org_settings.default_access_pin_hash

    db.commit()

    # Automatisches Geocoding wenn Adresse vorhanden aber keine Koordinaten gesetzt
    if (not lat_f or not lng_f) and (address_street or address_city):
        geo = await _geocode(address_street or None, address_no or None, address_city or None)
        if geo:
            incident.lat = geo.lat
            incident.lng = geo.lng
            db.commit()

    if user.org_id:
        await broadcast_org(user.org_id, {
            "type": "incident_created", "incident_id": incident.id,
            "alarm": alarm_type_code, "is_exercise": is_exercise,
            "url": f"/einsatz/{incident.id}",
            "title": f"{'[ÜBUNG] ' if is_exercise else ''}Neuer Einsatz: {alarm_type_code}",
        })
    _create_neighbor_invitations(db, incident, alarm_type_code, user.org_id, user.id)

    if ai_is_enabled() and not is_exercise:
        background_tasks.add_task(
            _trigger_ai_task_suggestions,
            incident.id,
            report_text or "",
            alarm_type_code,
            incident.primary_org_id,
        )
    elif not is_exercise:
        _log.debug("KI-Auftragsvorschläge übersprungen: KI nicht aktiviert (Einsatz %d)", incident.id)

    # Großschadenslage-Trigger: AlarmTyp-Flag prüfen
    if user.org_id:
        from app.models.master import AlarmType as _AlarmType, OrgSettings as _OrgSettings
        from app.services.major_incident_service import (
            handle_alarm_trigger as _handle_alarm_trigger,
            get_active_lage as _get_active_lage,
            adopt_incident_as_site as _adopt_incident_as_site,
        )
        at = get_alarm_type_by_code(db, user.org_id, alarm_type_code)
        if at and at.triggers_major_incident:
            lage, _site, _created = _handle_alarm_trigger(
                db, user.org_id, alarm_type_code, incident.id,
                external_key=f"ui_{incident.id}",
                is_exercise=is_exercise,
                ort=address_city or None,
                strasse=address_street or None,
                hausnr=address_no or None,
                lat=incident.lat,
                lng=incident.lng,
                einsatzgrund=report_text or None,
            )
            db.commit()
            return RedirectResponse(f"/lage/{lage.id}", status_code=303)
        else:
            active_lage = _get_active_lage(db, user.org_id)
            if active_lage:
                org_settings = db.query(_OrgSettings).filter(_OrgSettings.org_id == user.org_id).first()
                if not org_settings or org_settings.mi_auto_adopt:
                    _adopt_incident_as_site(
                        db, active_lage,
                        incident_id=incident.id,
                        external_key=f"ui_{incident.id}",
                        alarm_type_code=alarm_type_code,
                        org_id=user.org_id,
                        ort=address_city or None,
                        strasse=address_street or None,
                        hausnr=address_no or None,
                        lat=incident.lat,
                        lng=incident.lng,
                        einsatzgrund=report_text or None,
                    )
                    db.commit()

    return RedirectResponse(f"/einsatz/{incident.id}", status_code=303)


@router.post("/einsatz/{incident_id}/ki-aufgaben-vorschlaege")
async def request_ai_task_suggestions(
    incident_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    """Manuell KI-Auftragsvorschläge für einen Einsatz anfordern."""
    if not ai_is_enabled():
        from fastapi import HTTPException
        raise HTTPException(503, detail="KI ist nicht aktiviert.")
    incident = _incident_or_404(incident_id, db)
    background_tasks.add_task(
        _trigger_ai_task_suggestions,
        incident.id,
        incident.report_text or "",
        incident.alarm_type_code,
        incident.primary_org_id,
    )
    return Response(status_code=202)


# ── Alarmstufe / Einsatzgrund bearbeiten ─────────────────────────────────────

@router.get("/einsatz/{incident_id}/alarm/bearbeiten", response_class=HTMLResponse)
async def alarm_edit_modal(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    return templates.TemplateResponse(request, "incident/_alarm_edit_modal.html", {
        "incident": incident,
        "alarm_types": alarm_types,
    })


@router.post("/einsatz/{incident_id}/alarm", response_class=HTMLResponse)
async def alarm_save(
    incident_id: int, request: Request,
    alarm_type_code: str = Form(...),
    report_text: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    incident.alarm_type_code = alarm_type_code
    incident.report_text = report_text.strip() or None
    incident.reason = None
    db.commit()
    await manager.broadcast(incident_id, {"type": "alarm_type_changed", "reload_board": True})
    return templates.TemplateResponse(request, "incident/_alarm_confirm_fahrzeuge.html", {
        "incident": incident,
    })


@router.post("/einsatz/{incident_id}/alarm/fahrzeuge", response_class=HTMLResponse)
async def alarm_dispatch_vehicles(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Add vehicles from dispatch order for current alarm type, without duplicating existing ones."""
    incident = _incident_or_404(incident_id, db)
    db.refresh(incident, ["columns", "vehicles"])

    _at_for_dispatch = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code) if incident.alarm_type_code else None
    dispatch_entries = (
        db.query(AlarmDispatchVehicle)
        .filter(AlarmDispatchVehicle.alarm_type_id == _at_for_dispatch.id)
        .order_by(AlarmDispatchVehicle.display_order)
        .all()
    ) if _at_for_dispatch else []

    dispatched_col = next((c for c in incident.columns if c.code == "dispatched"), None)
    added = 0
    if dispatched_col and dispatch_entries:
        already_master_ids = {
            v.vehicle_master_id for v in incident.vehicles if v.removed_at is None
        }
        for entry in dispatch_entries:
            if entry.vehicle_master_id not in already_master_ids:
                vm = db.get(VehicleMaster, entry.vehicle_master_id)
                if vm and vm.active:
                    db.add(IncidentVehicle(
                        incident_id=incident.id,
                        column_id=dispatched_col.id,
                        vehicle_master_id=vm.id,
                        display_order=999,
                    ))
                    already_master_ids.add(entry.vehicle_master_id)
                    added += 1
        if added:
            db.commit()
            await manager.broadcast(incident_id, {"type": "vehicle_added", "reload_board": True})

    return templates.TemplateResponse(request, "incident/_alarm_confirm_ki.html", {
        "incident": incident,
        "ai_enabled": ai_is_enabled(),
        "vehicles_added": added,
    })


@router.post("/einsatz/{incident_id}/alarm/fahrzeuge-ueberspringen", response_class=HTMLResponse)
async def alarm_skip_vehicles(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    return templates.TemplateResponse(request, "incident/_alarm_confirm_ki.html", {
        "incident": incident,
        "ai_enabled": ai_is_enabled(),
        "vehicles_added": None,
    })


@router.post("/einsatz/{incident_id}/alarm/ki-aufgaben-neu", response_class=HTMLResponse)
async def alarm_regenerate_ki(
    incident_id: int, request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Remove pending AI suggestions and generate new ones based on updated alarm/reason."""
    if not ai_is_enabled():
        from fastapi import HTTPException
        raise HTTPException(503, "KI ist nicht aktiviert.")
    incident = _incident_or_404(incident_id, db)
    background_tasks.add_task(
        _trigger_ai_task_suggestions,
        incident.id,
        incident.report_text or "",
        incident.alarm_type_code,
        incident.primary_org_id,
    )
    return templates.TemplateResponse(request, "incident/_alarm_ki_triggered.html", {
        "incident": incident,
    })


# ── Einsatz-Board ─────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}", response_class=HTMLResponse)
async def incident_board(incident_id: int, request: Request, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)
    if not can_access_incident(user, incident):
        raise HTTPException(403, "Kein Zugriff auf diesen Einsatz")
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons"])
    alarm_types = db.query(AlarmType).order_by(AlarmType.code).all()
    _at_board = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code) if incident.alarm_type_code else None
    from sqlalchemy import exists as _exists
    _has_any_alarm = _exists().where(LageHintAlarm.lage_hint_id == LageHint.id)
    _has_matching = _exists().where(
        (LageHintAlarm.lage_hint_id == LageHint.id)
        & (LageHintAlarm.alarm_type_id == _at_board.id)
    ) if _at_board else None
    lage_hints = (
        db.query(LageHint)
        .filter(or_(~_has_any_alarm, _has_matching))
        .order_by(LageHint.display_order)
        .all()
    ) if _at_board else (
        db.query(LageHint)
        .filter(~_has_any_alarm)
        .order_by(LageHint.display_order)
        .all()
    )
    lage_hints = _prepend_ai_hints(incident, lage_hints)
    lage_hints_ai = [bool(getattr(h, 'is_ai', False)) for h in lage_hints]
    task_suggestions = (
        db.query(TaskSuggestion)
        .join(TaskSuggestionAlarm, TaskSuggestionAlarm.task_suggestion_id == TaskSuggestion.id)
        .filter(TaskSuggestionAlarm.alarm_type_id == _at_board.id)
        .order_by(TaskSuggestionAlarm.display_order)
        .all()
    ) if _at_board else []
    msg_suggestions = (
        db.query(MessageSuggestion)
        .join(MessageSuggestionAlarm, MessageSuggestionAlarm.message_suggestion_id == MessageSuggestion.id)
        .filter(MessageSuggestionAlarm.alarm_type_id == _at_board.id)
        .order_by(MessageSuggestionAlarm.display_order)
        .all()
    ) if _at_board else []
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    # Leader candidates: active users of same org with relevant roles
    leader_roles = {"incident_leader", "admin", "org_admin", "system_admin"}
    leader_candidates = (
        db.query(User)
        .join(UserRole, User.id == UserRole.user_id)
        .join(Role, UserRole.role_id == Role.id)
        .filter(
            User.active,
            or_(
                Role.code == "system_admin",
                User.org_id == incident.primary_org_id,
            ) if incident.primary_org_id else True,  # type: ignore[arg-type]
            Role.code.in_(leader_roles),
        )
        .distinct()
        .order_by(User.display_name)
        .all()
    )
    org_ids = [incident.primary_org_id] if incident.primary_org_id else []
    for _io in (incident.collaborating_orgs or []):
        if _io.org_id not in org_ids:
            org_ids.append(_io.org_id)
    el_member_candidates = list_el_candidates(db, org_ids)
    gk_member_candidates = list_commander_candidates(db, org_ids)
    # Pending AI task suggestions (not yet adopted/rejected) override admin chips
    ai_task_suggestions = [
        t for t in incident.tasks
        if t.source == "ai_suggestion" and not t.is_done and not t.is_cancelled
    ]
    _bs = db.get(SystemSettings, "breathing_enabled")
    breathing_enabled = (_bs.value if _bs else "true") != "false"
    lage_sprueche = (
        db.query(IncidentCommLog)
        .filter(
            IncidentCommLog.incident_id == incident_id,
            IncidentCommLog.is_lage_relevant == True,  # noqa: E712
        )
        .order_by(IncidentCommLog.ts.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse(request, "incident/board.html", {
        "user": user, "incident": incident,
        "alarm_types": alarm_types, "lage_hints": lage_hints, "lage_hints_ai": lage_hints_ai,
        "task_suggestions": task_suggestions, "msg_suggestions": msg_suggestions,
        "ai_task_suggestions": ai_task_suggestions,
        "can_edit": can_edit, "leader_candidates": leader_candidates,
        "el_member_candidates": el_member_candidates,
        "gk_member_candidates": gk_member_candidates,
        "unit_status_values": UNIT_STATUS_VALUES,
        "ai_enabled": ai_is_enabled(),
        "breathing_enabled": breathing_enabled,
        "lage_sprueche": lage_sprueche,
    })


@router.get("/einsatz/{incident_id}/dashboard", response_class=HTMLResponse)
async def incident_dashboard(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    incident = _incident_or_404(incident_id, db)
    db.refresh(incident, ["columns", "vehicles", "tasks", "messages", "rescued_persons"])

    col_by_id = {c.id: c for c in incident.columns}

    active_vehicles, dispatched_vehicles, other_vehicles = [], [], []
    for v in incident.vehicles:
        if v.removed_at:
            continue
        col = col_by_id.get(v.column_id)
        code = col.code if col else ""
        if code == "active":
            active_vehicles.append(v)
        elif code == "dispatched":
            dispatched_vehicles.append(v)
        else:
            other_vehicles.append(v)

    tasks_open = [t for t in incident.tasks if not t.is_done and not t.is_cancelled]
    tasks_done = [t for t in incident.tasks if t.is_done]

    msgs_open = [m for m in incident.messages if not m.is_done and not m.is_cancelled]
    msgs_done = [m for m in incident.messages if m.is_done]

    person_stats: dict[str, list] = {s: [] for s in PERSON_STATUS_VALUES}
    for p in incident.rescued_persons:
        if p.status in person_stats:
            person_stats[p.status].append(p)

    started_at_iso = incident.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    _at_board2 = get_alarm_type_by_code(db, incident.primary_org_id, incident.alarm_type_code) if incident.alarm_type_code else None
    from sqlalchemy import exists as _exists2
    _has_any2 = _exists2().where(LageHintAlarm.lage_hint_id == LageHint.id)
    _has_match2 = _exists2().where(
        (LageHintAlarm.lage_hint_id == LageHint.id)
        & (LageHintAlarm.alarm_type_id == _at_board2.id)
    ) if _at_board2 else None
    lage_hints = (
        db.query(LageHint)
        .filter(or_(~_has_any2, _has_match2))
        .order_by(LageHint.display_order)
        .all()
    ) if _at_board2 else (
        db.query(LageHint)
        .filter(~_has_any2)
        .order_by(LageHint.display_order)
        .all()
    )
    lage_hints = _prepend_ai_hints(incident, lage_hints)
    lage_hints_ai = [bool(getattr(h, 'is_ai', False)) for h in lage_hints]

    breathing_troops = (
        db.query(BreathingTroop)
        .filter(
            BreathingTroop.incident_id == incident_id,
            BreathingTroop.status.in_(["im_einsatz", "rueckzug"]),
        )
        .all()
    )
    for t in breathing_troops:
        _ = list(t.pressure_logs)

    # QR-Code für Dashboard-Header (Login-QR)
    qr_img_b64 = None
    qr_url_str = None
    if user:
        token = sign_qr_token(incident_id, user.id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        existing_token = db.query(IncidentToken).filter(
            IncidentToken.incident_id == incident_id,
            IncidentToken.issued_by_user_id == user.id,
            IncidentToken.revoked_at.is_(None),
        ).first()
        if not existing_token:
            from app.models.incident import IncidentToken as IT
            db.add(IT(incident_id=incident_id, token_hash=token_hash, issued_by_user_id=user.id))
            db.commit()
        qr_url_str = f"{request.base_url}qr-login?incident_id={incident_id}&token={token}"
        qr_img = qrcode.make(qr_url_str, box_size=4, border=1)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        qr_img_b64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse(
        request,
        "incident/dashboard.html",
        {
            "user": user,
            "incident": incident,
            "active_vehicles": active_vehicles,
            "dispatched_vehicles": dispatched_vehicles,
            "other_vehicles": other_vehicles,
            "tasks_open": tasks_open,
            "tasks_done": tasks_done,
            "msgs_open": msgs_open,
            "msgs_done": msgs_done,
            "person_stats": person_stats,
            "started_at_iso": started_at_iso,
            "lage_hints": lage_hints,
            "lage_hints_ai": lage_hints_ai,
            "breathing_troops": breathing_troops,
            "qr_img": qr_img_b64,
            "qr_url": qr_url_str,
        },
    )


@router.get("/dashboard/aktuell", response_class=HTMLResponse)
async def dashboard_latest(request: Request, db: Session = Depends(get_db)):
    """Permanent-Link: leitet immer auf das Dashboard des neuesten aktiven Einsatzes."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    latest = (
        _visible_incidents_q(db, user)
        .filter(Incident.status == "active")
        .order_by(Incident.started_at.desc())
        .first()
    )
    if latest:
        return RedirectResponse(f"/einsatz/{latest.id}/dashboard", status_code=302)
    return RedirectResponse("/", status_code=302)


# ── Einsatzleiter wechseln ────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/einsatzleiter-mitglied")
async def set_incident_leader_member(
    incident_id: int, request: Request,
    member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    incident.incident_leader_member_id = member_id or None
    incident.incident_leader_name = None
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "incident_leader_changed",
        "reload_board": True,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/einsatzleiter-mitglied-neu")
async def set_incident_leader_member_new(
    incident_id: int, request: Request,
    full_name: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Speichert einen Freitext-EL-Namen — legt KEINEN Member-Eintrag an."""
    if not full_name.strip():
        return Response(status_code=422)
    incident = _incident_or_404(incident_id, db)
    incident.incident_leader_name = full_name.strip()
    incident.incident_leader_member_id = None
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "incident_leader_changed",
        "reload_board": True,
    })
    return Response(status_code=204)



@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/gk")
async def set_vehicle_gk_quick(
    incident_id: int, vehicle_id: int, request: Request,
    member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    if member_id:
        ok = (
            db.query(MemberQualification)
            .join(Qualification, Qualification.id == MemberQualification.qualification_id)
            .filter(
                MemberQualification.member_id == member_id,
                Qualification.is_gruppenkommandant.is_(True),
            )
            .first()
        )
        if not ok:
            return Response(status_code=422)
    vehicle.commander_name = None
    set_commander(db, vehicle, member_id or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/kommandant-neu")
async def set_vehicle_commander_new(
    incident_id: int, vehicle_id: int, request: Request,
    full_name: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Speichert einen Freitext-GK-Namen — legt KEINEN Member-Eintrag an."""
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle or not full_name.strip():
        return Response(status_code=404)
    vehicle.commander_name = full_name.strip()
    vehicle.commander_member_id = None
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", status_code=303)


@router.post("/einsatz/{incident_id}/einsatzleiter")
async def set_incident_leader(
    incident_id: int, request: Request,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    leader = db.get(User, user_id)
    if leader and leader.active:
        incident.incident_leader_user_id = leader.id
        db.commit()
        await manager.broadcast(incident_id, {
            "type": "incident_leader_changed",
            "leader_id": leader.id,
            "leader_name": leader.display_name,
        })
    return Response(status_code=204)


# ── Aufgaben ───────────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/aufgabe", response_class=HTMLResponse)
async def create_task(
    incident_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    column_id: int | None = Form(None),
    vehicle_id: int | None = Form(None),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    incident = _incident_or_404(incident_id, db)
    task = add_task(
        db, incident, title, detail or None,
        user_id=request.state.user.id, column_id=column_id,
    )
    # Einheit direkt mit-zuweisen — column_id bleibt erhalten, damit der Auftrag
    # gleichzeitig auf dem Board UND in der Fahrzeug-Karte sichtbar ist
    # (analog assign_task_to_vehicle).
    if vehicle_id:
        task.vehicle_id = vehicle_id
    if note.strip():
        db.add(IncidentLog(incident_id=incident_id, text=note.strip(),
                           user_id=request.state.user.id, author_name=get_author_name(request)))
    db.commit()
    await manager.broadcast(incident_id, {
        "type": "task_created", "task_id": task.id, "reload_board": True,
    })
    return templates.TemplateResponse(request, "incident/_task_card.html", {
        "task": task, "incident": incident,
        "can_edit": True,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/erledigt")
async def toggle_task_done(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    # Toggle: sowohl is_done als auch status synchron halten,
    # damit die Status-Pille (Ampel) den Wechsel reflektiert.
    new_status = "open" if task.is_done else "done"
    col_id = task.column_id
    set_task_status(db, task, new_status, user_id=request.state.user.id)
    if new_status == "done":
        sink_done_cards(db, col_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": True})
    # Re-render der Vehicle-Card, falls die Task zugewiesen ist (Strikethrough sichtbar).
    if task.vehicle_id:
        vehicle = db.get(IncidentVehicle, task.vehicle_id)
        if vehicle:
            inc = task.incident
            _org_ids = [inc.primary_org_id] if inc.primary_org_id else []
            for io in (inc.collaborating_orgs or []):
                if io.org_id not in _org_ids:
                    _org_ids.append(io.org_id)
            return templates.TemplateResponse(request, "incident/_vehicle_card.html", {
                "vehicle": vehicle, "incident": inc,
                "can_edit": True,
                "unit_status_values": UNIT_STATUS_VALUES,
                "gk_member_candidates": list_commander_candidates(db, _org_ids),
            })
    # Free Task (nicht zugewiesen): Task-Card refreshen.
    return templates.TemplateResponse(request, "incident/_task_card.html", {
        "task": task, "incident": task.incident, "can_edit": True,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ampel")
async def set_task_ampel(
    incident_id: int, task_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    col_id = task.column_id
    try:
        set_task_status(db, task, status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
    if status in ("done", "cancelled"):
        sink_done_cards(db, col_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/zuweisen", response_class=HTMLResponse)
async def assign_task(
    incident_id: int, task_id: int, request: Request,
    vehicle_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    if vehicle_id:
        vehicle = db.get(IncidentVehicle, vehicle_id)
        if vehicle:
            assign_task_to_vehicle(db, task, vehicle, user_id=request.state.user.id)
    else:
        task.vehicle_id = None
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_assigned", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    task_logs = _entity_logs(db, incident_id, "task", task_id)
    return templates.TemplateResponse(request, "incident/_task_modal.html", {
        "user": request.state.user, "incident": incident, "task": task, "can_edit": can_edit,
        "entity_logs": task_logs,
    })


# ── Fahrzeug hinzufügen (Inline-Wizard) ───────────────────────────────────────

@router.get("/einsatz/{incident_id}/fahrzeug-vorschlaege")
async def vehicle_suggestions(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    q: str = "",
):
    """JSON-Endpoint: Vorschläge an Fahrzeugen, die zu diesem Einsatz hinzugefügt werden können.

    Liefert VehicleMaster-Einträge der primären Org + kollaborierenden Orgs,
    abzüglich der bereits aktuell zugewiesenen Fahrzeuge.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    incident = _incident_or_404(incident_id, db)
    if not has_role(user, "incident_leader", "admin", "recorder"):
        return Response(status_code=403)

    org_ids = set()
    if incident.primary_org_id:
        org_ids.add(incident.primary_org_id)
    for _io in (incident.collaborating_orgs or []):
        org_ids.add(_io.org_id)

    already_master_ids = {
        v.vehicle_master_id for v in incident.vehicles if v.removed_at is None
    }
    base_q = db.query(VehicleMaster).filter(
        VehicleMaster.active == True,  # noqa: E712
        VehicleMaster.is_adhoc == False,  # noqa: E712
    )
    if q:
        like = f"%{q.strip()}%"
        from sqlalchemy import or_ as _or
        base_q = base_q.filter(
            _or(VehicleMaster.code.ilike(like), VehicleMaster.name.ilike(like))
        )
    vehicles = base_q.order_by(VehicleMaster.display_order, VehicleMaster.code).all()
    items = [
        {
            "id": v.id,
            "code": v.code,
            "display_label": v.display_label,
            "name": v.name,
            "type": v.type or "",
            "dept_id": v.dept_id,
            "dept_name": v.dept.name if v.dept else "",
            "in_use": v.id in already_master_ids,
        }
        for v in vehicles
    ]
    from fastapi.responses import JSONResponse
    return JSONResponse({"items": items})


@router.post("/einsatz/{incident_id}/fahrzeug-hinzufuegen")
async def attach_vehicle_to_incident(
    incident_id: int, request: Request,
    vehicle_master_id: int | None = Form(None),
    new_code: str = Form(""),
    new_name: str = Form(""),
    new_type: str = Form(""),
    new_org_name: str = Form(""),
    new_org_short: str = Form(""),
    commander_member_id: int | None = Form(None),
    commander_free_text: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    """Fügt ein Fahrzeug zum laufenden Einsatz hinzu — entweder per Master-ID oder
    durch Anlegen eines neuen, nicht in den Stammdaten existierenden Fahrzeugs.
    """
    incident = _incident_or_404(incident_id, db)
    db.refresh(incident, ["columns", "vehicles"])

    # Stammfahrzeug bestimmen / neu anlegen
    if vehicle_master_id:
        vm = db.get(VehicleMaster, vehicle_master_id)
        if not vm:
            return Response("Fahrzeug nicht gefunden", status_code=404)
    elif new_code.strip() and new_name.strip():
        dept_id = incident.primary_org_id or request.state.user.org_id
        if not dept_id:
            return Response("Keine Organisation zugeordnet", status_code=400)
        vm = VehicleMaster(
            dept_id=dept_id,
            code=new_code.strip()[:30],
            name=new_name.strip()[:150],
            type=(new_type.strip() or None),
            is_first_train=False,
            active=True,
            display_order=999,
            is_adhoc=True,
            adhoc_org_name=new_org_name.strip()[:150] or None,
            adhoc_org_short=new_org_short.strip()[:3] or None,
        )
        db.add(vm)
        db.flush()
    else:
        return Response("vehicle_master_id ODER (new_code + new_name) erforderlich", status_code=400)

    # Schon im Einsatz?
    existing = next(
        (v for v in incident.vehicles if v.vehicle_master_id == vm.id and v.removed_at is None),
        None,
    )
    if existing:
        return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)

    target_col = next((c for c in incident.columns if c.code == "dispatched"), None)
    if target_col is None and incident.columns:
        target_col = incident.columns[0]
    if target_col is None:
        return Response("Keine Spalte vorhanden", status_code=400)

    iv = IncidentVehicle(
        incident_id=incident.id,
        column_id=target_col.id,
        vehicle_master_id=vm.id,
        display_order=999,
        commander_member_id=commander_member_id or None,
    )
    db.add(iv)
    db.flush()
    if not commander_member_id and commander_free_text.strip():
        iv.commander_name = commander_free_text.strip()
    if note.strip():
        db.add(IncidentLog(incident_id=incident_id, text=note.strip(),
                           user_id=request.state.user.id, author_name=get_author_name(request)))
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_added", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)


# ── Fahrzeug verschieben ──────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/verschieben")
async def move_vehicle(
    incident_id: int, vehicle_id: int, request: Request,
    column_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    column = db.get(IncidentColumn, column_id)
    if not vehicle or not column:
        return Response(status_code=404)
    move_vehicle_to_column(db, vehicle, column, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_moved", "reload_board": True})
    return Response(status_code=204)


# ── Abschnitt-Spalte anlegen ──────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/abschnitt")
async def create_section(
    incident_id: int, request: Request,
    title: str = Form(...),
    column_kind: str = Form("vehicles"),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    add_section_column(db, incident, title, column_kind=column_kind, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "column_created", "reload_board": True})
    return Response(status_code=204)


@router.delete("/einsatz/{incident_id}/spalten/{column_id}")
async def delete_column_endpoint(
    incident_id: int, column_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    from fastapi.responses import JSONResponse
    column = db.get(IncidentColumn, column_id)
    if not column or column.incident_id != incident_id:
        return Response(status_code=404)
    try:
        delete_section_column(db, column, user_id=request.state.user.id)
        db.commit()
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    await manager.broadcast(incident_id, {"type": "column_deleted", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/spalten/reihenfolge")
async def reorder_columns_endpoint(
    incident_id: int, request: Request,
    column_order: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    import json as _json
    try:
        ids = _json.loads(column_order)
        if isinstance(ids, list):
            reorder_columns(db, incident_id, [int(i) for i in ids])
            db.commit()
    except Exception:
        pass
    await manager.broadcast(incident_id, {"type": "columns_reordered", "reload_board": True})
    return Response(status_code=204)


# ── Meldungen ─────────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/meldung")
async def create_message(
    incident_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    status: str = Form("meldung"),
    due_after_min: int = Form(0),
    vehicle_id: int | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.models.incident import _TRAFFIC_LIGHT_LEGACY, TRAFFIC_LIGHT_VALUES
    status = _TRAFFIC_LIGHT_LEGACY.get(status, status)
    if status not in TRAFFIC_LIGHT_VALUES:
        status = "meldung"
    incident = _incident_or_404(incident_id, db)
    due_sec = due_after_min * 60 if due_after_min > 0 else None
    due_at = None
    if due_sec:
        from datetime import timedelta
        due_at = incident.started_at + timedelta(seconds=due_sec)
    from app.services.incident_service import _get_column as _gc
    msgs_col = _gc(incident, "messages")
    msg = Message(
        incident_id=incident_id,
        column_id=msgs_col.id if msgs_col else None,
        title=title, detail=detail or None,
        status=status,
        due_after_sec=due_sec, due_at=due_at,
        vehicle_id=vehicle_id or None,
        author_name=get_author_name(request),
    )
    db.add(msg)
    db.commit()
    # Optional: Dateien direkt beim Anlegen anhängen
    if files:
        from fastapi import HTTPException as _HE
        from app.services.media_service import store_upload_for_message
        for f in files:
            if not f.filename:
                continue
            try:
                await store_upload_for_message(f, msg, request.state.user, db, org_id=request.state.user.org_id)
            except _HE:
                pass
        db.commit()
    await manager.broadcast(incident_id, {"type": "message_created", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/meldung/{msg_id}/erledigt")
async def toggle_message(
    incident_id: int, msg_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, msg_id)
    if not msg:
        return Response(status_code=404)
    msg.is_done = not msg.is_done
    msg.done_at = datetime.now(UTC) if msg.is_done else None
    col_id = msg.column_id
    if msg.is_done:
        sink_done_cards(db, col_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/meldung/{msg_id}/ampel")
async def set_message_ampel(
    incident_id: int, msg_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, msg_id)
    if not msg:
        return Response(status_code=404)
    col_id = msg.column_id
    try:
        set_message_status(db, msg, status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
    if status in ("erledigt", "storniert"):
        sink_done_cards(db, col_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    return Response(status_code=204)


# ── Person erfassen ──────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/person")
async def create_person(
    incident_id: int, request: Request,
    gender: str = Form("Unbekannt"), person_group: str = Form("Erwachsen"),
    age_range: str = Form(""), name: str = Form(""), location: str = Form(""),
    vehicle_id: int | None = Form(None),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    person = RescuedPerson(
        incident_id=incident_id,
        gender=gender, person_group=person_group,
        age_range=age_range or None, name=name or None,
        location=location or None, vehicle_id=vehicle_id,
    )
    db.add(person)
    if note.strip():
        db.add(IncidentLog(incident_id=incident_id, text=note.strip(),
                           user_id=request.state.user.id, author_name=get_author_name(request)))
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_created", "reload_board": True})
    return Response(status_code=204)


# ── Einsatz abschließen ───────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/abschliessen")
async def close_incident_view(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    incident = _incident_or_404(incident_id, db)
    close_incident(db, incident, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "incident_closed"})
    return RedirectResponse(f"/archiv/{incident_id}", status_code=303)


# ── Einsatz wiedereröffnen (system_admin / org_admin) ─────────────────────────

@router.post("/einsatz/{incident_id}/wiedereroeffnen")
async def reopen_incident_view(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("org_admin", "admin")),
):
    """Reaktiviert einen abgeschlossenen Einsatz. Nur system_admin/org_admin."""
    incident = _incident_or_404(incident_id, db)
    user = request.state.user
    if not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(403, "Kein Zugriff auf diesen Einsatz")
    if incident.status != "closed":
        # Bereits aktiv – einfach zurück zum Board.
        return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)
    reopen_incident(db, incident, user_id=user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "incident_reopened"})
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)


@router.post("/einsatz/{incident_id}/autoclose/keepopen")
async def autoclose_keepopen(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    """"Offen halten"-Bestätigung aus dem 48h-Warning-Banner.

    Setzt den Warning-Stempel zurück und aktualisiert started_at, damit
    der 48h-Zähler von neuem läuft.
    """
    incident = _incident_or_404(incident_id, db)
    incident.autoclose_warn_sent_at = None
    incident.started_at = datetime.now(UTC).replace(tzinfo=None)
    incident.autoclose_keepopen_count = (incident.autoclose_keepopen_count or 0) + 1
    db.commit()
    await manager.broadcast(incident_id, {"type": "autoclose_dismissed"})
    return Response(status_code=204)


# ── Log-Eintrag ───────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/log")
async def add_log(
    incident_id: int, request: Request,
    text: str = Form(...),
    entity_type: str = Form(""),
    entity_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    etype = entity_type.strip() or None
    entry = IncidentLog(
        incident_id=incident_id, text=text, user_id=request.state.user.id,
        author_name=get_author_name(request),
        entity_type=etype,
        entity_id=entity_id if etype else None,
    )
    db.add(entry)
    db.commit()
    await manager.broadcast(incident_id, {"type": "log_updated"})
    return Response(status_code=204)


# ── QR-Code ───────────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/qr", response_class=HTMLResponse)
async def get_qr_code(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)

    token = sign_qr_token(incident_id, user.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Store token in DB once per user+incident – never overwrite so distributed QR codes
    # remain valid for all devices that have already printed/displayed them.
    existing = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident_id,
        IncidentToken.issued_by_user_id == user.id,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if not existing:
        from app.models.incident import IncidentToken as IT
        db.add(IT(incident_id=incident_id, token_hash=token_hash, issued_by_user_id=user.id))
        db.commit()

    url = f"{request.base_url}qr-login?incident_id={incident_id}&token={token}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    return templates.TemplateResponse(request, "incident/qr_modal.html", {
        "incident": incident,
        "qr_img": img_b64, "qr_url": url,
    })


@router.get("/einsatz/{incident_id}/qr/print", response_class=HTMLResponse)
async def qr_print(incident_id: int, request: Request, db: Session = Depends(get_db)):
    """Druckoptimierte Seite mit großem QR-Code + Einsatz-Eckdaten.
    Wiederverwendung der Token-Logik von /qr."""
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)

    token = sign_qr_token(incident_id, user.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    existing = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident_id,
        IncidentToken.issued_by_user_id == user.id,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if not existing:
        from app.models.incident import IncidentToken as IT
        db.add(IT(incident_id=incident_id, token_hash=token_hash, issued_by_user_id=user.id))
        db.commit()

    url = f"{request.base_url}qr-login?incident_id={incident_id}&token={token}"
    # Größerer QR-Code für den Druck (box_size erhöht)
    img = qrcode.make(url, box_size=14, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    logo_url = (org.logo_path if org and org.logo_path else None) or "/static/img/Logo-rot.png"
    return templates.TemplateResponse(request, "incident/qr_print.html", {
        "incident": incident,
        "qr_img": img_b64, "qr_url": url,
        "logo_url": logo_url,
        "base_url": str(request.base_url).rstrip("/"),
    })


# ── Gäste-PIN für QR-Zugang ──────────────────────────────────────────────────

_PIN_COOKIE = "board_pin"
_PIN_COOKIE_MAX_AGE = 86400  # 24 h


def _set_pin_cookie(response: Response, incident_id: int) -> None:
    from app.config import settings as _cfg
    token = sign_pin_access_token(incident_id)
    response.set_cookie(
        _PIN_COOKIE, token,
        httponly=True, secure=_cfg.COOKIE_SECURE,
        samesite="lax", max_age=_PIN_COOKIE_MAX_AGE,
    )


def _check_pin_cookie(request: Request) -> int | None:
    """Gibt incident_id zurück wenn ein gültiges PIN-Zugangscookie vorhanden."""
    token = request.cookies.get(_PIN_COOKIE)
    if not token:
        return None
    return unsign_pin_access_token(token)


@router.post("/einsatz/{incident_id}/pin")
async def set_incident_pin(
    incident_id: int, request: Request,
    pin: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Setzt oder löscht den Gäste-PIN für QR-Zugang ohne Account."""
    incident = _incident_or_404(incident_id, db)
    if pin.strip():
        incident.access_pin_hash = hash_pin(pin.strip()[:16])
    else:
        incident.access_pin_hash = None
    db.commit()
    if request.headers.get("HX-Request"):
        return Response(status_code=204)
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=302)


@router.get("/einsatz/{incident_id}/pin-zugang", response_class=HTMLResponse)
async def pin_entry_page(incident_id: int, request: Request, db: Session = Depends(get_db)):
    """Öffentliche PIN-Eingabeseite für Gäste – kein Login erforderlich."""
    incident = db.get(Incident, incident_id)
    if not incident or incident.status != "active" or not incident.access_pin_hash:
        return RedirectResponse("/login", status_code=302)
    error = request.query_params.get("error")
    return templates.TemplateResponse(request, "incident/pin_entry.html", {
        "incident": incident,
        "error": error,
    })


from app.core.rate_limit import limiter as _limiter  # noqa: E402

@(_limiter.limit("5/15minutes") if _limiter else lambda f: f)
@router.post("/einsatz/{incident_id}/pin-zugang", response_class=HTMLResponse)
async def pin_verify(
    incident_id: int, request: Request,
    pin: str = Form(""),
    db: Session = Depends(get_db),
):
    """Prüft den PIN und setzt bei Erfolg ein signiertes Zugangscookie."""
    incident = db.get(Incident, incident_id)
    if not incident or incident.status != "active" or not incident.access_pin_hash:
        return RedirectResponse("/login", status_code=302)
    if not verify_pin(pin.strip(), incident.access_pin_hash):
        return RedirectResponse(
            f"/einsatz/{incident_id}/pin-zugang?error=wrong_pin", status_code=302
        )
    redirect = RedirectResponse(f"/einsatz/{incident_id}/screensaver", status_code=302)
    _set_pin_cookie(redirect, incident_id)
    return redirect


# ── Bildschirmschoner ─────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/screensaver", response_class=HTMLResponse)
async def screensaver(incident_id: int, request: Request, db: Session = Depends(get_db)):
    """Bildschirmschoner-Modus: Logo, Uhrzeit, Alarmtext.

    Hält den Bildschirm über die native Wake-Lock-Bridge der Android-App aktiv
    (oder Web Screen Wake Lock API im Browser). Live-Update via WebSocket.
    """
    user = getattr(request.state, "user", None)
    pin_incident_id = _check_pin_cookie(request)
    if not user and pin_incident_id != incident_id:
        return RedirectResponse(
            f"/einsatz/{incident_id}/pin-zugang", status_code=302
        )
    incident = _incident_or_404(incident_id, db)
    return templates.TemplateResponse(request, "incident/screensaver.html", {
        "user": user,
        "incident": incident,
    })


# ── Verlauf / Historie ────────────────────────────────────────────────────────

def _enrich_history(changes, db, incident_id: int) -> list[dict]:
    """Convert raw IncidentChange records to human-readable dicts for the template."""
    import json as _json

    tasks    = {t.id: t for t in db.query(Task).filter_by(incident_id=incident_id).all()}
    msgs     = {m.id: m for m in db.query(Message).filter_by(incident_id=incident_id).all()}
    vehicles = {v.id: v for v in db.query(IncidentVehicle).filter_by(incident_id=incident_id).all()}
    columns  = {c.id: c for c in db.query(IncidentColumn).filter_by(incident_id=incident_id).all()}

    user_ids = {c.user_id for c in changes if c.user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    member_ids: set[int] = set()
    for c in changes:
        if c.after_json:
            try:
                mid = _json.loads(c.after_json).get("commander_member_id")
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
        elif action == "message.status_set":
            st = STATUS_DE.get(after.get("status", ""), after.get("status", ""))
            summary = f'Meldung "{mtitle(eid)}": {st}'
        elif action == "message.assigned":
            vid = after.get("vehicle_id")
            summary = f'Meldung "{mtitle(eid)}" -> {vname(vid) if vid else "-"}'
        elif action == "message.moved":
            summary = f'Meldung "{mtitle(eid)}" verschoben'
        elif action == "person.assigned":
            vid = after.get("vehicle_id")
            summary = f'Person → {vname(vid) if vid else "—"}'
        elif action == "person.moved":
            summary = 'Person: Fahrzeugzuweisung aufgehoben'
        elif action == "column.created":
            summary = f'Neue Sektion erstellt: "{after.get("title", f"#{eid}")}"'
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

        actor = ""
        if change.user_id:
            u = users.get(change.user_id)
            actor = u.display_name if u else f"Benutzer #{change.user_id}"
        elif change.api_key_id:
            actor = "API"

        result.append({"ts": change.ts, "summary": summary, "actor": actor})

    return result


@router.get("/einsatz/{incident_id}/historie", response_class=HTMLResponse)
async def incident_history(incident_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = _incident_or_404(incident_id, db)
    from app.models.incident import IncidentChange
    raw_changes = db.query(IncidentChange).filter(
        IncidentChange.incident_id == incident_id
    ).order_by(IncidentChange.ts.desc()).limit(500).all()
    changes = _enrich_history(raw_changes, db, incident_id)
    return templates.TemplateResponse(request, "incident/history.html", {
        "user": user, "incident": incident, "changes": changes,
    })


# ── Fahrzeug-Detail-Modal ─────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", response_class=HTMLResponse)
async def vehicle_detail(
    incident_id: int, vehicle_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)

    org_ids = [incident.primary_org_id] + [io.org_id for io in (incident.collaborating_orgs or [])]
    org_ids = [oid for oid in org_ids if oid]
    commander_candidates = list_commander_candidates(db, org_ids)

    from app.models.incident import IncidentChange
    recent_changes = (
        db.query(IncidentChange)
        .filter(
            IncidentChange.incident_id == incident_id,
            IncidentChange.entity_type == "incident_vehicle",
            IncidentChange.entity_id == vehicle_id,
        )
        .order_by(IncidentChange.ts.desc())
        .limit(10)
        .all()
    )
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    vehicle_logs = _entity_logs(db, incident_id, "vehicle", vehicle_id)
    return templates.TemplateResponse(request, "incident/_vehicle_modal.html", {
        "user": user, "incident": incident, "vehicle": vehicle,
        "members": commander_candidates, "recent_changes": recent_changes,
        "can_edit": can_edit, "unit_status_values": UNIT_STATUS_VALUES,
        "bos_values": BOS_VALUES, "entity_logs": vehicle_logs,
    })


@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/kommandant")
async def set_vehicle_commander(
    incident_id: int, vehicle_id: int, request: Request,
    member_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    set_commander(db, vehicle, member_id or None, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}/fahrzeug/{vehicle_id}/detail", status_code=303)



@router.post("/einsatz/{incident_id}/fahrzeug/{vehicle_id}/status")
async def set_vehicle_unit_status(
    incident_id: int, vehicle_id: int, request: Request,
    unit_status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    vehicle = db.get(IncidentVehicle, vehicle_id)
    if not vehicle:
        return Response(status_code=404)
    try:
        set_unit_status(db, vehicle, unit_status, user_id=request.state.user.id)
    except ValueError:
        return Response(status_code=422)
    db.commit()
    await manager.broadcast(incident_id, {"type": "vehicle_updated", "reload_board": True})
    return Response(status_code=204)


# ── Auftrags-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/aufgabe/{task_id}/detail", response_class=HTMLResponse)
async def task_detail(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    task = db.get(Task, task_id)
    if not task:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    task_logs = _entity_logs(db, incident_id, "task", task_id)
    return templates.TemplateResponse(request, "incident/_task_modal.html", {
        "user": user, "incident": incident, "task": task, "can_edit": can_edit,
        "entity_logs": task_logs,
    })


# ── Meldungs-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/meldung/{message_id}/detail", response_class=HTMLResponse)
async def message_detail(
    incident_id: int, message_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    entity_logs = _entity_logs(db, incident_id, "message", message_id)
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": user, "incident": incident, "msg": msg, "can_edit": can_edit,
        "entity_logs": entity_logs,
    })


@router.post("/einsatz/{incident_id}/meldung/{message_id}", response_class=HTMLResponse)
async def update_message_endpoint(
    incident_id: int, message_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response(status_code=404)
    msg.title = title.strip() or msg.title
    msg.detail = detail.strip() or None
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_updated", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    entity_logs = _entity_logs(db, incident_id, "message", message_id)
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
        "entity_logs": entity_logs,
    })


# ── Personen-Detail / Edit-Modal ──────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/person/{person_id}/detail", response_class=HTMLResponse)
async def person_detail(
    incident_id: int, person_id: int, request: Request, db: Session = Depends(get_db)
):
    user = getattr(request.state, "user", None)
    if not user:
        return Response("Nicht eingeloggt", status_code=401)
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response("Nicht gefunden", status_code=404)
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    person_logs = _entity_logs(db, incident_id, "person", person_id)
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": user, "incident": incident, "person": person, "can_edit": can_edit,
        "person_status_values": PERSON_STATUS_VALUES, "entity_logs": person_logs,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}", response_class=HTMLResponse)
async def update_person_endpoint(
    incident_id: int, person_id: int, request: Request,
    gender: str = Form(""), person_group: str = Form(""),
    age_range: str = Form(""), name: str = Form(""), location: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    if gender.strip():
        person.gender = gender.strip()
    if person_group.strip():
        person.person_group = person_group.strip()
    person.age_range = age_range.strip() or None
    person.name = name.strip() or None
    person.location = location.strip() or None
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_updated", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    person_logs = _entity_logs(db, incident_id, "person", person_id)
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
        "person_status_values": PERSON_STATUS_VALUES, "entity_logs": person_logs,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}/status")
async def set_person_status_endpoint(
    incident_id: int, person_id: int, request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    if status not in PERSON_STATUS_VALUES:
        return Response("Ungültiger Status", status_code=400)
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    person.status = status
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/person/{person_id}/loeschen")
async def delete_person_endpoint(
    incident_id: int, person_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    db.delete(person)
    db.commit()
    await manager.broadcast(incident_id, {"type": "person_deleted", "reload_board": True})
    return RedirectResponse(f"/einsatz/{incident_id}", status_code=303)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}", response_class=HTMLResponse)
async def update_task_endpoint(
    incident_id: int, task_id: int, request: Request,
    title: str = Form(...), detail: str = Form(""),
    due_after_min: int = Form(0),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from datetime import timedelta
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    update_task(db, task, title, detail or None, user_id=request.state.user.id)
    if due_after_min > 0:
        task.due_after_sec = due_after_min * 60
        task.due_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=due_after_min)
        task.popup_shown = False
    else:
        task.due_at = None
        task.due_after_sec = None
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "reload_board": True})
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    task_logs = _entity_logs(db, incident_id, "task", task_id)
    return templates.TemplateResponse(request, "incident/_task_modal.html", {
        "user": request.state.user, "incident": incident, "task": task, "can_edit": can_edit,
        "entity_logs": task_logs,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ausblenden")
async def cancel_task_endpoint(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    cancel_task(db, task, user_id=request.state.user.id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ki-annehmen")
async def accept_ai_suggestion(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task or task.incident_id != incident_id or task.source != "ai_suggestion":
        return Response(status_code=404)
    task.source = "manual"
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_updated", "reload_board": True})
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/ki-verwerfen")
async def reject_ai_suggestion(
    incident_id: int, task_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task or task.incident_id != incident_id or task.source != "ai_suggestion":
        return Response(status_code=404)
    db.delete(task)
    db.commit()
    await manager.broadcast(incident_id, {"type": "task_cancelled", "reload_board": True})
    return Response(status_code=204)


# ── KI-Lagebild ──────────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/ki-lagebild")
async def generate_lagebild(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from app.core.audit import write_audit
    from app.services.ai_service import AIServiceError, generate_situation_brief

    if not ai_is_enabled():
        raise HTTPException(503, "KI-Dienst ist nicht aktiviert.")

    incident = _incident_or_404(incident_id, db)
    context = collect_situation_context(incident_id, db)
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    write_audit(db, "ai.lagebild.generated", incident_id=incident_id, user_id=user_id)
    db.commit()
    try:
        text = await generate_situation_brief(context, org_id=incident.primary_org_id)
    except AIServiceError as exc:
        raise HTTPException(503, str(exc)) from exc
    return JSONResponse({"text": text})


@router.post("/einsatz/{incident_id}/ki-lagebild/journal")
async def save_lagebild_journal(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from fastapi import HTTPException

    from app.core.audit import write_audit
    from app.services.incident_service import _get_column as _gc

    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Kein Text übergeben.")
    incident = _incident_or_404(incident_id, db)
    msgs_col = _gc(incident, "messages")
    msg = Message(
        incident_id=incident_id,
        column_id=msgs_col.id if msgs_col else None,
        title="✨ KI-Lagebild",
        detail=text,
        status="information",
        author_name=get_author_name(request),
    )
    db.add(msg)
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    write_audit(db, "ai.lagebild.journal", incident_id=incident_id, user_id=user_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "message_created", "reload_board": True})
    return Response(status_code=204)


# ── KI-Lage-Hinweise ─────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/ki-lagehinweise")
async def regenerate_lage_hints(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    import json as _json
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from app.core.audit import write_audit
    from app.services.ai_service import AIServiceError, generate_lage_hints

    if not ai_is_enabled():
        raise HTTPException(503, "KI-Dienst ist nicht aktiviert.")

    incident = _incident_or_404(incident_id, db)
    address = " ".join(filter(None, [
        incident.address_street, incident.address_no, incident.address_city,
    ]))
    try:
        hints = await generate_lage_hints(
            incident.report_text or "",
            incident.alarm_type_code or "",
            address,
            org_id=incident.primary_org_id,
        )
    except AIServiceError as exc:
        raise HTTPException(503, str(exc)) from exc

    incident.ai_lage_hints = _json.dumps(hints, ensure_ascii=False)
    user_id = getattr(getattr(request.state, "user", None), "id", None)
    write_audit(db, "ai.lagehinweise.generated", incident_id=incident_id, user_id=user_id)
    db.commit()
    await manager.broadcast(incident_id, {"type": "ai_hints_ready", "reload_board": True})
    return JSONResponse({"hints": hints})


# ── Media-Upload / -Löschen ───────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/medien", response_class=HTMLResponse)
async def upload_task_media(
    incident_id: int, task_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    task = db.get(Task, task_id)
    if not task or task.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload
    errors: list[str] = []
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload(f, task, request.state.user, db, org_id=request.state.user.org_id)
        except _HE as exc:
            errors.append(str(exc.detail))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.filename}: {exc}")
    db.commit()
    db.refresh(task, ["media"])
    incident = _incident_or_404(incident_id, db)
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": False})
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_media.html", {
        "user": request.state.user, "task": task, "incident": incident,
        "can_edit": can_edit, "errors": errors,
    })


@router.post("/einsatz/{incident_id}/aufgabe/{task_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_task_media(
    incident_id: int, task_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.models.incident import TaskMedia
    from app.services.media_service import delete_media
    media = db.get(TaskMedia, media_id)
    if not media or media.task_id != task_id:
        return Response(status_code=404)
    user = request.state.user
    if media.uploaded_by_user_id != user.id and not has_role(user, "admin", "org_admin"):
        return Response(status_code=403)
    delete_media(media, db)
    db.commit()
    task = db.get(Task, task_id)
    db.refresh(task, ["media"])
    incident = _incident_or_404(incident_id, db)
    await manager.broadcast(incident_id, {"type": "task_updated", "task_id": task_id, "reload_board": False})
    can_edit = has_role(user, "incident_leader", "admin", "recorder")
    return templates.TemplateResponse(request, "incident/_task_media.html", {
        "user": user, "task": task, "incident": incident,
        "can_edit": can_edit, "errors": [],
    })


# ── Meldungs-Medien ──────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/meldung/{message_id}/medien", response_class=HTMLResponse)
async def upload_message_media(
    incident_id: int, message_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    msg = db.get(Message, message_id)
    if not msg or msg.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload_for_message
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload_for_message(f, msg, request.state.user, db, org_id=request.state.user.org_id)
        except _HE:
            pass
    db.commit()
    db.refresh(msg, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    entity_logs = _entity_logs(db, incident_id, "message", message_id)
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
        "entity_logs": entity_logs,
    })


@router.post("/einsatz/{incident_id}/meldung/{message_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_message_media(
    incident_id: int, message_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.services.media_service import delete_media
    media = db.get(MessageMedia, media_id)
    if not media or media.message_id != message_id:
        return Response(status_code=404)
    delete_media(media, db)
    db.commit()
    msg = db.get(Message, message_id)
    db.refresh(msg, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    entity_logs = _entity_logs(db, incident_id, "message", message_id)
    return templates.TemplateResponse(request, "incident/_message_modal.html", {
        "user": request.state.user, "incident": incident, "msg": msg, "can_edit": can_edit,
        "entity_logs": entity_logs,
    })


# ── Personen-Medien ───────────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/person/{person_id}/medien", response_class=HTMLResponse)
async def upload_person_media(
    incident_id: int, person_id: int, request: Request,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    person = db.get(RescuedPerson, person_id)
    if not person or person.incident_id != incident_id:
        return Response(status_code=404)
    from fastapi import HTTPException as _HE

    from app.services.media_service import store_upload_for_person
    for f in files:
        if not f.filename:
            continue
        try:
            await store_upload_for_person(f, person, request.state.user, db, org_id=request.state.user.org_id)
        except _HE:
            pass
    db.commit()
    db.refresh(person, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    person_logs = _entity_logs(db, incident_id, "person", person_id)
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
        "entity_logs": person_logs,
    })


@router.post("/einsatz/{incident_id}/person/{person_id}/medien/{media_id}/loeschen", response_class=HTMLResponse)
async def delete_person_media(
    incident_id: int, person_id: int, media_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "recorder")),
):
    from app.services.media_service import delete_media
    media = db.get(PersonMedia, media_id)
    if not media or media.person_id != person_id:
        return Response(status_code=404)
    delete_media(media, db)
    db.commit()
    person = db.get(RescuedPerson, person_id)
    db.refresh(person, ["media"])
    incident = _incident_or_404(incident_id, db)
    can_edit = has_role(request.state.user, "incident_leader", "admin", "recorder")
    person_logs = _entity_logs(db, incident_id, "person", person_id)
    return templates.TemplateResponse(request, "incident/_person_modal.html", {
        "user": request.state.user, "incident": incident, "person": person, "can_edit": can_edit,
        "entity_logs": person_logs,
    })


# ── Drag & Drop: generischer Karte-verschieben-Endpoint ──────────────────────

@router.post("/einsatz/{incident_id}/karte/verschieben")
async def move_card_endpoint(
    incident_id: int, request: Request,
    kind: str = Form(...),
    uid: int = Form(...),
    column_id: int | None = Form(None),
    position: int = Form(0),
    vehicle_id: int | None = Form(None),
    zone_order: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    move_card(
        db, incident_id, kind, uid,
        column_id=column_id, position=position,
        vehicle_id=vehicle_id,
        user_id=request.state.user.id,
    )
    if zone_order and column_id:
        import json as _json
        try:
            _json.loads(zone_order)
            update_column_card_order(db, column_id, zone_order)
        except Exception:
            pass
    db.commit()
    await manager.broadcast(incident_id, {"type": "card_moved", "reload_board": True})
    return Response(status_code=204)


# ── Adress-Autocomplete (Photon/Historie, GET, kein CSRF) ─────────────────────

@router.get("/adresse/vorschlaege")
async def address_suggestions(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    field: str = "street",
    city: str = "",
    street: str = "",
):
    """Liefert Adress-Vorschläge via Photon/Historie für das Typeahead-Autocomplete."""
    from dataclasses import asdict as _asdict
    from fastapi.responses import JSONResponse as _JSONResponse
    from app.services.address_autocomplete import suggest_addresses as _suggest

    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    if not has_role(user, "incident_leader", "admin", "recorder"):
        return Response(status_code=403)
    if field not in ("street", "house", "city") or not q.strip():
        return _JSONResponse({"items": []})
    items = await _suggest(
        db,
        q=q.strip(),
        field=field,
        city=city.strip() or None,
        street=street.strip() or None,
        org_id=user.org_id,
        limit=settings.PHOTON_SUGGEST_LIMIT,
    )
    return _JSONResponse({"items": [_asdict(s) for s in items]})


# ── Standalone Geocoding (für Neuer-Einsatz-Dialog ohne Incident-ID) ──────────

@router.post("/adresse/geocode")
async def standalone_geocode(
    request: Request,
    address_street: str = Form(""),
    address_no: str = Form(""),
    address_city: str = Form(""),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Geocodiert eine Adresse ohne Incident-Kontext — für das Neuer-Einsatz-Formular."""
    from fastapi.responses import JSONResponse as _JSONResponse

    from app.services.geocoding import geocode_address
    result = await geocode_address(
        address_street.strip() or None,
        address_no.strip() or None,
        address_city.strip() or None,
    )
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Adresse konnte nicht gefunden werden")
    return _JSONResponse({"lat": result.lat, "lng": result.lng, "display_name": result.display_name})


# ── Adresse & Koordinaten bearbeiten ─────────────────────────────────────────

@router.get("/einsatz/{incident_id}/adresse/bearbeiten", response_class=HTMLResponse)
async def address_edit_modal(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Rendert das Adresse-Edit-Modal-Fragment für HTMX."""
    user = request.state.user
    incident = _incident_or_404(incident_id, db)
    from app.core.permissions import can_access_incident
    if not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(403, "Kein Zugriff auf diesen Einsatz")
    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    _user_org = getattr(user, "org", None)
    default_city = (_user_org.city if _user_org and _user_org.city else settings.DEFAULT_INCIDENT_CITY)
    return templates.TemplateResponse(request, "incident/_address_modal.html", {
        "user": user, "incident": incident, "org": org,
        "auto_token": incident.auto_geojson_token,
        "default_city": default_city,
    })


@router.post("/einsatz/{incident_id}/adresse/geocode")
async def address_geocode(
    incident_id: int, request: Request,
    address_street: str = Form(""),
    address_no: str = Form(""),
    address_city: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Geocodiert die angegebene Adresse via Nominatim und gibt lat/lng als JSON zurück."""
    from fastapi.responses import JSONResponse as _JSONResponse

    from app.services.geocoding import geocode_address
    result = await geocode_address(
        address_street.strip() or None,
        address_no.strip() or None,
        address_city.strip() or None,
    )
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Adresse konnte nicht gefunden werden")
    return _JSONResponse({"lat": result.lat, "lng": result.lng, "display_name": result.display_name})


@router.post("/einsatz/{incident_id}/adresse", response_class=HTMLResponse)
async def address_save(
    incident_id: int, request: Request,
    address_street: str = Form(""),
    address_no: str = Form(""),
    address_city: str = Form(""),
    lat: str = Form(""),
    lng: str = Form(""),
    lagekarte_shash_url: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin")),
):
    """Speichert Adresse, Koordinaten und optionalen Lagekarte.info-Link."""
    user = request.state.user
    incident = _incident_or_404(incident_id, db)
    from app.core.permissions import can_access_incident
    if not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(403, "Kein Zugriff auf diesen Einsatz")

    before = {
        "address_street": incident.address_street,
        "address_no": incident.address_no,
        "address_city": incident.address_city,
        "lat": incident.lat,
        "lng": incident.lng,
        "lagekarte_shash_url": incident.lagekarte_shash_url,
    }

    incident.address_street = address_street.strip() or None
    incident.address_no = address_no.strip() or None
    incident.address_city = address_city.strip() or None

    try:
        incident.lat = float(lat) if lat.strip() else None
        incident.lng = float(lng) if lng.strip() else None
    except ValueError:
        incident.lat = None
        incident.lng = None

    incident.lagekarte_shash_url = lagekarte_shash_url.strip() or None

    after = {
        "address_street": incident.address_street,
        "address_no": incident.address_no,
        "address_city": incident.address_city,
        "lat": incident.lat,
        "lng": incident.lng,
        "lagekarte_shash_url": incident.lagekarte_shash_url,
    }

    from app.core.audit import write_incident_change
    write_incident_change(
        db, incident_id, "incident.address_updated", "incident", incident_id,
        before, after, user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()

    await manager.broadcast(incident_id, {"type": "address_updated", "reload_board": True})

    org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    return templates.TemplateResponse(request, "incident/_address_modal.html", {
        "user": user, "incident": incident, "org": org, "saved": True,
        "auto_token": incident.auto_geojson_token,
    })


# ── Funkjournal ───────────────────────────────────────────────────────────────

@router.get("/einsatz/{incident_id}/funkjournal", response_class=HTMLResponse)
async def funkjournal_page(
    request: Request,
    incident_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    incident = db.get(Incident, incident_id)
    if not incident or not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    comms = (
        db.query(IncidentCommLog)
        .filter(IncidentCommLog.incident_id == incident_id)
        .order_by(IncidentCommLog.ts.desc())
        .limit(200)
        .all()
    )
    open_requests = sum(1 for c in comms if c.is_request and not c.handled)
    return templates.TemplateResponse(request, "incident/funkjournal.html", {
        "user": user,
        "incident": incident,
        "comms": comms,
        "open_requests": open_requests,
        "can_edit": has_role(user, "incident_leader", "admin", "org_admin", "recorder"),
    })


@router.post("/einsatz/{incident_id}/funkjournal")
async def funkjournal_add(
    request: Request,
    incident_id: int,
    direction: str = Form(...),
    channel: str = Form(""),
    partner: str = Form(""),
    message: str = Form(...),
    is_request: bool = Form(False),
    is_lage_relevant: bool = Form(False),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    incident = db.get(Incident, incident_id)
    if not incident or not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    if direction not in ("in", "out", "int"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Ungültige Richtung")

    db.add(IncidentCommLog(
        incident_id=incident_id,
        direction=direction,
        channel=channel.strip() or None,
        partner=partner.strip() or None,
        message=message.strip(),
        is_request=is_request,
        is_lage_relevant=is_lage_relevant,
        user_id=user.id,
        author_name=get_author_name(request),
    ))
    db.commit()
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/funkjournal/{entry_id}/erledigt")
async def funkjournal_toggle_handled(
    request: Request,
    incident_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    incident = db.get(Incident, incident_id)
    if not incident or not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    entry = db.get(IncidentCommLog, entry_id)
    if not entry or entry.incident_id != incident_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    entry.handled = not entry.handled
    db.commit()
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/funkjournal/{entry_id}/lage")
async def funkjournal_toggle_lage(
    request: Request,
    incident_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    incident = db.get(Incident, incident_id)
    if not incident or not can_access_incident(user, incident):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    entry = db.get(IncidentCommLog, entry_id)
    if not entry or entry.incident_id != incident_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    entry.is_lage_relevant = not entry.is_lage_relevant
    db.commit()
    return Response(status_code=204)
