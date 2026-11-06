"""Atemschutzüberwachung UI."""

from datetime import UTC, datetime
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData

from app.core.async_bridge import run_async
from app.core.audit import write_incident_change
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.breathing import BOTTLE_PRESETS, TROOP_STATUS_LABELS, BreathingAssp, BreathingTroop, BreathingWarnung
from app.models.incident import Incident
from app.models.master import Member, OrgSettings
from app.services.breathing_events import claim_client_event, event_audit, parse_erfasst_at, valid_client_event_id
from app.services.breathing_service import (
    AsuewSettings,
    InvalidTroopTransition,
    PressureIncreaseConflict,
    ReadinessWarning,
    ack_warning,
    assp_uebersicht,
    asuew_heartbeat_fehlt,
    check_troop_warnings,
    clear_notfall,
    create_troop,
    declare_notfall,
    get_asuew_settings,
    get_or_none_assp,
    get_time_warning,
    get_warning_level,
    incident_hinweise,
    log_pressure,
    record_kontrolle,
    redeploy_troop,
    report_back_pressure,
    report_objective_reached,
    set_funk_verloren,
    start_troop,
    troop_timeline,
    troop_urgency,
    update_meldung,
    update_troop_status,
)
from app.services.breathing_sitzung import validiere_sitzung
from app.services.broadcast import manager

router = APIRouter()


def _ereignis_vorbereiten(
    db: Session, incident_id: int, user_id: int, route: str,
    client_event_id: str | None, erfasst_at: str | None,
) -> tuple[datetime, bool, bool]:
    ts, nachgetragen = parse_erfasst_at(erfasst_at, datetime.now(UTC).replace(tzinfo=None))
    claimed = True
    if client_event_id is not None:
        claimed = claim_client_event(
            db, incident_id, user_id, valid_client_event_id(client_event_id), route, ts, nachgetragen,
        )
    return ts, nachgetragen, claimed


def _incident_asuew_settings(db: Session, incident_id: int) -> AsuewSettings:
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    if incident.primary_org_id is None:
        return AsuewSettings(30, 60, 75, 30)
    return get_asuew_settings(db, incident.primary_org_id)


def _current_user_label(request: Request) -> str:
    user = request.state.user
    return user.full_name or user.username


def _parse_members_form(form_data: FormData) -> list[dict]:
    members_data = []
    i = 0
    while True:
        key_id = f"member_{i}_id"
        key_name = f"member_{i}_name"
        key_role = f"member_{i}_role"
        key_press = f"member_{i}_press"
        if key_id not in form_data and key_name not in form_data:
            break
        mid = form_data.get(key_id)
        members_data.append({
            "member_id": int(mid) if mid and str(mid).isdigit() else None,  # type: ignore[arg-type]
            "free_text_name": form_data.get(key_name) or None,
            "role": form_data.get(key_role, "truppmann"),
            "start_press": float(form_data[key_press]) if form_data.get(key_press) else None,  # type: ignore[arg-type]
        })
        i += 1

    return members_data


def require_breathing_enabled(request: Request) -> None:
    """HTTP 404 wenn die Atemschutzueberwachung nicht effektiv aktiv ist."""
    if not getattr(request.state, "breathing_module_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _asuew_app_pflicht(db: Session, incident_id: int) -> bool:
    incident = db.get(Incident, incident_id)
    org = db.query(OrgSettings).filter(
        OrgSettings.org_id == incident.primary_org_id,
    ).first() if incident else None
    return bool(org and org.asuew_app_pflicht)


def _asuew_session_ok(request: Request, db: Session, incident_id: int) -> bool:
    user = getattr(request.state, "user", None)
    key = request.headers.get("X-ECP-ASUEW") or request.cookies.get("ec_asuew", "")
    return bool(user and validiere_sitzung(db, key, incident_id, user.id))


def require_asuew_session(
    incident_id: int, request: Request, db: Session = Depends(get_db),
) -> None:
    """App-Sitzung fuer Eingaben; Notfall und Funkverlust-Melden bleiben frei.

    Ein Notruf darf fuer berechtigte Rollen nie an einer fehlenden Sitzung
    scheitern. Funkkontakt-Wiederherstellung und Notfall-Aufheben sind Eingaben.
    """
    if _asuew_app_pflicht(db, incident_id) and not _asuew_session_ok(request, db, incident_id):
        raise HTTPException(403, "Atemschutzüberwachung nur in der Einsatzcockpit-App.")


@router.get("/einsatz/{incident_id}/atemschutz", response_class=HTMLResponse)
def breathing_board(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "breathing/board.html", _board_kontext(request, db, incident, user))


def _board_kontext(request: Request, db: Session, incident: Incident, user) -> dict:
    incident_id = incident.id
    db.refresh(incident, ["breathing_troops", "vehicles"])
    all_members = db.query(Member).filter(Member.active == True).order_by(Member.lastname).all()  # noqa: E712
    members = [m for m in all_members if m.is_agt]
    belegte_mitglieder = {
        m.member_id: t.name for t in incident.breathing_troops
        if t.status in ("bereit", "im_einsatz", "rueckzug")
        for m in t.active_members if m.member_id
    }
    personen = [{"id": m.id, "name": m.full_name, "firstname": m.firstname,
                 "lastname": m.lastname, "is_agt": m.is_agt,
                 "occupied": belegte_mitglieder.get(m.id, "")} for m in members]
    now = datetime.now(UTC).replace(tzinfo=None)
    vehicles = [v for v in incident.vehicles if not v.removed_at]

    app_pflicht = _asuew_app_pflicht(db, incident_id)
    session_ok = _asuew_session_ok(request, db, incident_id)
    readonly = app_pflicht and not session_ok
    can_alarm = has_role(user, "breathing_supervisor", "incident_leader", "admin", "recorder")
    asuew_settings = _incident_asuew_settings(db, incident_id)
    troops_with_warnings = [
        (t, *get_warning_level(t, asuew_settings.gelb_prozent), get_time_warning(t), check_troop_warnings(t))
        for t in incident.breathing_troops
    ]
    erholzeit_rest = {
        t.id: max(0, ceil(asuew_settings.erholzeit_min - (now - t.back_at).total_seconds() / 60))
        for t in incident.breathing_troops if t.back_at and asuew_settings.erholzeit_min
    }
    trupp_uebersicht = dict.fromkeys(TROOP_STATUS_LABELS, 0)
    trupp_uebersicht["sicherheitstrupp_bereit"] = 0
    troop_ids = [t.id for t in incident.breathing_troops]
    warnungen_by_troop: dict[int, list[BreathingWarnung]] = {}
    for troop in incident.breathing_troops:
        trupp_uebersicht[troop.status] += 1
        if troop.status == "bereit" and troop.is_sicherheitstrupp:
            trupp_uebersicht["sicherheitstrupp_bereit"] += 1
    if troop_ids:
        for warnung in db.query(BreathingWarnung).options(
            selectinload(BreathingWarnung.zustellungen),
        ).filter(
            BreathingWarnung.troop_id.in_(troop_ids),
            BreathingWarnung.erledigt_at.is_(None),
        ).order_by(BreathingWarnung.ausgeloest_at, BreathingWarnung.id).all():
            warnungen_by_troop.setdefault(warnung.troop_id, []).append(warnung)
    assp = get_or_none_assp(db, incident_id)
    hinweise = incident_hinweise(incident.breathing_troops, bool(assp and assp.aktiv))
    kein_heartbeat = asuew_heartbeat_fehlt(db, incident, datetime.now(UTC).replace(tzinfo=None))
    alert_kinds = {
        "notfall": (0, "red", "ATEMSCHUTZNOTFALL"),
        "funk_verloren": (1, "orange", "Kein Funkkontakt"),
        "withdraw": (2, "red", "Rückzugsdruck erreicht"),
        "max_exceeded": (3, "orange", "Max-Zeit überschritten"),
        "two_third_due": (4, "yellow", "Kontrolle fällig (2/3)"),
        "one_third_due": (4, "yellow", "Kontrolle fällig (1/3)"),
        "kein_heartbeat": (5, "yellow", "Kein Überwachungsgerät in der App aktiv "
                           "(kein Heartbeat seit über 60 Sekunden). "
                           "Überwachung in der Einsatzcockpit-App öffnen oder Funkkontrolle der Trupps durchführen."),
        "kein_sicherheitstrupp": (6, "yellow", "Kein bereiter Sicherheitstrupp: "
                                "Rettungstrupp ab Einsatzbeginn bereitstellen"),
        "assp": (7, "yellow", "Atemschutzsammelplatz (ASSP) über die Einsatzleitung alarmieren; "
                 "die Überwachung bleibt beim Gruppenkommandanten"),
    }
    alerts = []

    def add_alert(troop, kind):
        rank, color, message = alert_kinds[kind]
        alerts.append({"troop_id": troop.id, "key": f"{troop.id}:{kind}",
                       "rank": rank, "color": color, "emergency": kind == "notfall",
                       "text": f"{troop.name}: {message}"})

    for troop, _, _, _, active_warnings in troops_with_warnings:
        for kind in active_warnings:
            if kind in alert_kinds:
                add_alert(troop, kind)
        if troop.funk_verloren_at and "funk_verloren" not in active_warnings:
            add_alert(troop, "funk_verloren")
    for kind, troop in hinweise.items():
        add_alert(troop, kind)
    if kein_heartbeat and incident.breathing_troops:
        anchor = next((t for t in incident.breathing_troops if t.status in ("im_einsatz", "rueckzug")),
                      incident.breathing_troops[0])
        add_alert(anchor, "kein_heartbeat")
    alerts.sort(key=lambda alert: (alert["rank"], alert["troop_id"], alert["key"]))
    return {
        "asuew_app_pflicht": app_pflicht, "asuew_session_ok": session_ok,
        "asuew_readonly": readonly, "can_alarm": can_alarm,
        "can_edit": can_alarm and not readonly,
        "request": request, "user": user, "incident": incident,
        "server_now_iso": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "troop_urgency": troop_urgency, "troop_timeline": troop_timeline,
        "safety": {"troop": next((t for t in incident.breathing_troops
                                 if t.status == "bereit" and t.is_sicherheitstrupp), None)},
        "troops_with_warnings": troops_with_warnings, "members": members,
        "vehicles": vehicles, "bottle_presets": BOTTLE_PRESETS,
        "belegte_mitglieder": belegte_mitglieder, "personen": personen,
        "naechste_trupp_nr": len(incident.breathing_troops) + 1, "erholzeit_rest": erholzeit_rest,
        "troop_status_labels": TROOP_STATUS_LABELS,
        "asuew_settings": asuew_settings, "current_user_label": _current_user_label(request),
        "hinweise": hinweise, "breathing_alerts": alerts,
        "kein_heartbeat": kein_heartbeat,
        "trupp_uebersicht": trupp_uebersicht, "warnungen_by_troop": warnungen_by_troop,
    }


@router.get("/einsatz/{incident_id}/atemschutz/assp", response_class=HTMLResponse)
def breathing_assp(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    assp = get_or_none_assp(db, incident_id)
    now = datetime.now(UTC)
    context = {
        "incident": incident, "user": user, "assp": assp, "server_now": now,
        "can_edit": has_role(user, "breathing_supervisor", "incident_leader", "admin", "recorder"),
        "uebersicht": assp_uebersicht(incident.breathing_troops, _incident_asuew_settings(db, incident_id), assp, now),
    }
    template = "breathing/_assp_content.html" if request.headers.get("HX-Request") else "breathing/assp.html"
    return templates.TemplateResponse(request, template, context, headers={"Cache-Control": "no-store"})


def _assp_speichern(db: Session, incident_id: int, request: Request, values: dict, *, delta: bool = False) -> Response:
    # Der Einsatz wird vor jedem Zugriff geprueft (inklusive Tenant-Filter).
    if db.get(Incident, incident_id) is None:
        raise HTTPException(404)
    assp = get_or_none_assp(db, incident_id)
    fields = ("aktiv", "leiter_name", "standort", "flaschen_voll", "flaschen_leer", "notiz")
    before = {key: getattr(assp, key) for key in fields} if assp else None
    if delta:
        values = {key: max(0, (getattr(assp, key) if assp else 0) + value) for key, value in values.items()}
        if any(value > 999 for value in values.values()):
            return Response("Flaschenzahl muss zwischen 0 und 999 liegen.", status_code=400, media_type="text/plain")
    if assp is None:
        assp = BreathingAssp(incident_id=incident_id, aktiv=False, flaschen_voll=0, flaschen_leer=0)
        db.add(assp)
    for key, value in values.items():
        setattr(assp, key, value)
    now = datetime.now(UTC).replace(tzinfo=None)
    if assp.aktiv and assp.aktiviert_at is None:
        assp.aktiviert_at = now
    assp.updated_at, assp.updated_by_user_id = now, request.state.user.id
    db.flush()
    write_incident_change(
        db, incident_id, "assp.updated", "breathing_assp", assp.id,
        before, {key: getattr(assp, key) for key in fields}, user_id=request.state.user.id,
    )
    db.commit()
    run_async(manager.broadcast, incident_id, {"type": "assp_updated"})
    return Response(status_code=204, headers={"HX-Trigger": "assp-refresh"})


def _assp_form_values(form: FormData, *, delta: bool = False) -> dict:
    values: dict = {}
    for key in (("delta_voll", "delta_leer") if delta else ("flaschen_voll", "flaschen_leer")):
        if key not in form:
            continue
        raw = str(form[key]).strip()
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("Flaschenzahl muss eine ganze Zahl sein.") from None
        if not (-999 if delta else 0) <= value <= 999:
            raise ValueError("Flaschenzahl ausserhalb des erlaubten Bereichs.")
        values[key.replace("delta_", "flaschen_")] = value
    if not delta:
        for key, limit in (("leiter_name", 150), ("standort", 200), ("notiz", 300)):
            if key in form:
                value_text = str(form[key]).strip()
                if len(value_text) > limit:
                    raise ValueError(f"{key}: maximal {limit} Zeichen.")
                values[key] = value_text or None
        if "aktiv" in form:
            raw = str(form["aktiv"]).lower()
            if raw not in ("true", "false", "1", "0", "on", "off"):
                raise ValueError("Ungueltiger ASSP-Status.")
            values["aktiv"] = raw in ("true", "1", "on")
    return values


@router.post("/einsatz/{incident_id}/atemschutz/assp")
def update_breathing_assp(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
):
    if db.get(Incident, incident_id) is None:
        raise HTTPException(404)
    try:
        values = _assp_form_values(run_async(request.form))
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    return _assp_speichern(db, incident_id, request, values)


@router.post("/einsatz/{incident_id}/atemschutz/assp/flaschen")
def update_breathing_assp_flaschen(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
):
    if db.get(Incident, incident_id) is None:
        raise HTTPException(404)
    try:
        values = _assp_form_values(run_async(request.form), delta=True)
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    return _assp_speichern(db, incident_id, request, values, delta=True)


@router.get("/einsatz/{incident_id}/atemschutz/uebersicht", response_class=HTMLResponse)
def breathing_summary(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "breathing/_summary.html", _board_kontext(request, db, incident, user),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/einsatz/{incident_id}/atemschutz/{troop_id}/karte", response_class=HTMLResponse)
def breathing_troop_card(
    incident_id: int, troop_id: int, request: Request, db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    incident = db.get(Incident, incident_id)
    troop = db.get(BreathingTroop, troop_id)
    if not incident or not troop or troop.incident_id != incident_id:
        raise HTTPException(404)
    context = _board_kontext(request, db, incident, user)
    values = next(row for row in context["troops_with_warnings"] if row[0].id == troop_id)
    context.update(zip(("troop", "warning", "warning_member", "time_warning", "active_warnings"), values, strict=True))
    return templates.TemplateResponse(
        request, "breathing/_troop_card.html", context, headers={"Cache-Control": "no-store"},
    )


@router.post("/einsatz/{incident_id}/atemschutz/trupp")
def create_breathing_troop(
    incident_id: int, request: Request,
    name: str = Form(...),
    task_text: str = Form(""),
    vehicle_id: int | None = Form(None),
    unit_name: str = Form(""),
    location_text: str = Form(""),
    bottle_preset: str = Form(""),
    planned_duration_min: int | None = Form(None),
    is_sicherheitstrupp: bool = Form(False),
    ueberwacher_name: str | None = Form(None),
    angriffsweg: str = Form(""),
    funkkanal: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    duration = _incident_asuew_settings(db, incident_id).erwartete_einsatzzeit_min
    if bottle_preset == "manuell" and planned_duration_min is not None:
        if not 1 <= planned_duration_min <= 120:
            return RedirectResponse(
                f"/einsatz/{incident_id}/atemschutz?error=invalid_input", status_code=303,
            )
        duration = planned_duration_min

    # Parse member data from form (member_0_id, member_0_role, member_0_press, etc.)
    form_data = run_async(request.form)
    try:
        members_data = _parse_members_form(form_data)
    except ValueError:
        return RedirectResponse(
            f"/einsatz/{incident_id}/atemschutz?error=invalid_input",
            status_code=303,
        )

    # Validierung: mindestens 2 Mitglieder ausgefüllt (Member-ID ODER Freitext-Name)
    filled = [
        m for m in members_data
        if m["member_id"] or (m["free_text_name"] and m["free_text_name"].strip())  # type: ignore[union-attr]
    ]
    if len(filled) < 2:
        return RedirectResponse(
            f"/einsatz/{incident_id}/atemschutz?error=min_two_members",
            status_code=303,
        )

    try:
        create_troop(
            db, incident_id=incident_id, name=name,
            members_data=members_data, task_text=task_text or None,
            vehicle_id=vehicle_id, unit_name=unit_name.strip() or None,
            location_text=location_text.strip() or None,
            planned_duration_min=duration,
            bottle_preset=bottle_preset.strip() or None,
            is_sicherheitstrupp=is_sicherheitstrupp,
            ueberwacher_user_id=request.state.user.id,
            ueberwacher_name=ueberwacher_name if ueberwacher_name is not None else _current_user_label(request),
            angriffsweg=angriffsweg, funkkanal=funkkanal,
            user_id=request.state.user.id,
        )
    except ValueError:
        return RedirectResponse(
            f"/einsatz/{incident_id}/atemschutz?error=invalid_input",
            status_code=303,
        )
    db.commit()
    run_async(manager.broadcast, incident_id, {"type": "troop_created", "reload_breathing": True})
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/erneut-einsetzen")
def redeploy_troop_view(
    incident_id: int, troop_id: int, request: Request,
    name: str | None = Form(None),
    unit_name: str | None = Form(None),
    vehicle_id: int | None = Form(None),
    ueberwacher_name: str | None = Form(None),
    angriffsweg: str | None = Form(None),
    funkkanal: str | None = Form(None),
    is_sicherheitstrupp: bool | None = Form(None),
    task_text: str | None = Form(None),
    location_text: str | None = Form(None),
    bottle_preset: str | None = Form(None),
    planned_duration_min: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    """Archiviert den letzten Zyklus und legt eine neue Truppbesetzung an."""
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)

    form_data = run_async(request.form)
    duration = _incident_asuew_settings(db, incident_id).erwartete_einsatzzeit_min
    if bottle_preset == "manuell" and planned_duration_min is not None:
        if not 1 <= planned_duration_min <= 120:
            return Response(
                "Die Einsatzzeit muss zwischen 1 und 120 Minuten liegen.", status_code=400, media_type="text/plain",
            )
        duration = planned_duration_min

    try:
        members_data = _parse_members_form(form_data)
        redeploy_troop(
            db, troop, members_data,
            task_text=task_text,
            location_text=location_text,
            planned_duration_min=duration,
            bottle_preset=bottle_preset,
            user_id=request.state.user.id,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400)
    for field, value in {"name": name, "unit_name": unit_name,
                         "ueberwacher_name": ueberwacher_name,
                         "angriffsweg": angriffsweg, "funkkanal": funkkanal}.items():
        if value is not None:
            setattr(troop, field, value.strip())
    if "vehicle_id" in form_data:
        troop.vehicle_id = vehicle_id
    if is_sicherheitstrupp is not None or "name" in form_data:
        troop.is_sicherheitstrupp = bool(is_sicherheitstrupp)
    if ueberwacher_name is not None:
        troop.ueberwacher_user_id = request.state.user.id
    db.commit()
    lfd_nr = troop.deployments[-1].lfd_nr
    run_async(manager.broadcast, incident_id, {
        "type": "troop_redeployed", "troop_id": troop_id, "lfd_nr": lfd_nr,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/starten")
def start_troop_view(
    incident_id: int, troop_id: int, request: Request,
    override_reason: str | None = Form(None),
    db: Session = Depends(get_db),
    user=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    can_override = has_role(user, "breathing_supervisor", "incident_leader", "admin")
    permitted_reason = override_reason if can_override else None
    try:
        start_troop(
            db, troop, user_id=user.id,
            override_reason=permitted_reason,
            override_user_id=user.id if permitted_reason else None,
        )
    except ReadinessWarning as warning:
        return templates.TemplateResponse(request, "breathing/_readiness_warning.html", {
            "user": user,
            "incident": troop.incident,
            "troop": troop,
            "issues": warning.issues,
            "can_override_readiness": can_override,
        })
    except ValueError as exc:
        return Response(str(exc), status_code=400)
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_started", "troop_id": troop_id,
        "readiness_overridden": bool(permitted_reason),
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/enddruck")
def back_pressure_view(
    incident_id: int, troop_id: int, request: Request,
    troop_member_id: int = Form(...),
    pressure_bar: float = Form(...),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    """Erfasst den Enddruck eines Mitglieds nach Rückkehr des Trupps."""
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "enddruck", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        member = report_back_pressure(
            db, troop, troop_member_id, pressure_bar,
            recorded_by_user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen,
        )
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_back_pressure_reported", "troop_id": troop_id,
        "troop_member_id": member.id, "pressure": pressure_bar,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/status")
def update_status(
    incident_id: int, troop_id: int, request: Request,
    status: str = Form(...),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "status", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)
    try:
        update_troop_status(db, troop, status, user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen)
    except InvalidTroopTransition as exc:
        db.rollback()
        return Response(str(exc), status_code=409)
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400)
    db.commit()
    warning = get_warning_level(troop, _incident_asuew_settings(db, incident_id).gelb_prozent)
    time_warn = get_time_warning(troop)
    run_async(manager.broadcast, incident_id, {
        "type": "troop_status_changed", "troop_id": troop_id,
        "status": status, "warning": warning.level, "time_warning": time_warn,
    })
    return RedirectResponse(f"/einsatz/{incident_id}/atemschutz", status_code=303)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/druck")
def log_pressure_view(
    incident_id: int, troop_id: int, request: Request,
    troop_member_id: int = Form(...),
    pressure_bar: float = Form(...),
    note: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    korrektur_grund: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "druck", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        log_pressure(db, troop, troop_member_id, pressure_bar,
                 note=note.strip() or None,
                 recorded_by_user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen,
                 korrektur_grund=korrektur_grund)
    except ValueError as exc:
        db.rollback()
        code = 409 if isinstance(exc, PressureIncreaseConflict) else 400
        return Response(str(exc), status_code=code, media_type="text/plain")
    db.commit()
    updated_troop = db.get(BreathingTroop, troop_id)
    assert updated_troop is not None
    warning = get_warning_level(updated_troop, _incident_asuew_settings(db, incident_id).gelb_prozent)
    time_warn = get_time_warning(updated_troop)
    lowest = updated_troop.lowest_current_pressure or pressure_bar
    run_async(manager.broadcast, incident_id, {
        "type": "pressure_logged", "troop_id": troop_id,
        "troop_member_id": troop_member_id,
        "pressure": pressure_bar, "lowest_pressure": lowest,
        "warning": warning.level,
        "warning_member_id": warning.member.id if warning.member else None,
        "warning_member_name": warning.member.display_name if warning.member else None,
        "time_warning": time_warn,
        "last_meldung_at": updated_troop.last_meldung_at.isoformat() if updated_troop.last_meldung_at else None,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/einsatzziel")
def objective_reached_view(
    incident_id: int, troop_id: int, request: Request,
    troop_member_id: int = Form(...),
    pressure_bar: float = Form(...),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    """Erfasst den Druck am Einsatzziel und finalisiert den Rückzugsdruck."""
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "einsatzziel", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        member = report_objective_reached(
            db, troop, troop_member_id, pressure_bar,
            recorded_by_user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen,
        )
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    warning = get_warning_level(troop, _incident_asuew_settings(db, incident_id).gelb_prozent)
    run_async(manager.broadcast, incident_id, {
        "type": "troop_objective_reached", "troop_id": troop_id,
        "troop_member_id": member.id, "pressure": pressure_bar,
        "withdraw_press": member.withdraw_press,
        "warning": warning.level,
        "warning_member_id": warning.member.id if warning.member else None,
    })
    return Response(status_code=204)


@router.get("/einsatz/{incident_id}/atemschutz/aktive-warnungen")
def active_breathing_warnings(
    incident_id: int, request: Request, db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    """Liefert aktive Warnungen für Reload und WebSocket-Wiederverbindung."""
    if not getattr(request.state, "user", None):
        return Response(status_code=401)
    incident = db.get(Incident, incident_id)
    if not incident:
        return Response(status_code=404)
    db.refresh(incident, ["breathing_troops"])
    active = []
    for troop in incident.breathing_troops:
        if troop.status not in ("im_einsatz", "rueckzug"):
            continue
        for kind in check_troop_warnings(troop):
            active.append({"troop_id": troop.id, "kind": kind})
    return JSONResponse({"warnings": active})


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/meldung")
def troop_meldung(
    incident_id: int, troop_id: int, request: Request,
    text: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "meldung", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    update_meldung(db, troop, text.strip() or None, user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen)
    db.commit()
    time_warn = get_time_warning(troop)
    run_async(manager.broadcast, incident_id, {
        "type": "troop_meldung", "troop_id": troop_id,
        "text": text.strip() or None,
        "time_warning": time_warn,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/ack")
def troop_ack(
    incident_id: int, troop_id: int, request: Request,
    kind: str = Form(...),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    if kind not in ("one_third", "two_third", "max_time", "withdraw"):
        return Response(status_code=400)
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "ack", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        ack_warning(db, troop, kind, user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen)
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_warning_acked", "troop_id": troop_id, "kind": kind,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/kontrolle")
def troop_kontrolle_view(
    incident_id: int, troop_id: int, request: Request,
    stufe: str = Form(...),
    art: str = Form("funk"),
    pressure_bar: float | None = Form(None),
    troop_member_id: int | None = Form(None),
    note: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "kontrolle", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        record_kontrolle(
            db, troop, stufe, art, pressure_bar=pressure_bar,
            troop_member_id=troop_member_id, note=note.strip() or None,
            user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen,
        )
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_kontrolle", "troop_id": troop_id, "stufe": stufe, "art": art,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/funkverlust")
def troop_funk_view(
    incident_id: int, troop_id: int, request: Request,
    aktiv: str = Form(...),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
):
    # Nur das Melden ist aus Sicherheitsgruenden ohne App-Sitzung erlaubt.
    if aktiv != "1":
        require_asuew_session(incident_id, request, db)
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "funkverlust", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        if aktiv not in ("1", "0"):
            raise ValueError("Aktiv muss 1 oder 0 sein.")
        set_funk_verloren(db, troop, aktiv == "1", user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen)
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_funk", "troop_id": troop_id,
    })
    if aktiv == "1":
        run_async(manager.broadcast, incident_id, {
            "type": "troop_warning", "troop_id": troop_id, "kind": "funk_verloren",
        })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/notfall")
def troop_notfall_view(
    incident_id: int, troop_id: int, request: Request,
    bestaetigt: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "notfall", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    try:
        if bestaetigt != "1":
            raise ValueError("Notfall muss ausdrücklich bestätigt werden.")
        declare_notfall(db, troop, user_id=request.state.user.id, ts=ts, nachgetragen=nachgetragen)
    except ValueError as exc:
        db.rollback()
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_notfall", "troop_id": troop_id,
    })
    run_async(manager.broadcast, incident_id, {
        "type": "troop_warning", "troop_id": troop_id, "kind": "notfall",
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/notfall/aufheben")
def troop_notfall_cleared_view(
    incident_id: int, troop_id: int, request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        clear_notfall(db, troop, user_id=request.state.user.id)
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_notfall_cleared", "troop_id": troop_id,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/details")
def troop_details_view(
    incident_id: int, troop_id: int, request: Request,
    angriffsweg: str | None = Form(None),
    funkkanal: str | None = Form(None),
    ueberwacher_name: str | None = Form(None),
    uebernehmen: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "details", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    form_data = run_async(request.form)
    # Leere, ausdrücklich gesendete Felder dürfen bestehende Angaben löschen.
    if angriffsweg is None and "angriffsweg" in form_data:
        angriffsweg = ""
    if funkkanal is None and "funkkanal" in form_data:
        funkkanal = ""
    if ueberwacher_name is None and "ueberwacher_name" in form_data:
        ueberwacher_name = ""
    fields = ("angriffsweg", "funkkanal", "ueberwacher_name", "ueberwacher_user_id")
    before = {field: getattr(troop, field) for field in fields}
    if uebernehmen == "1":
        troop.ueberwacher_user_id = request.state.user.id
        if ueberwacher_name is None:
            ueberwacher_name = _current_user_label(request)
    for field, value, length in (
        ("angriffsweg", angriffsweg, 300),
        ("funkkanal", funkkanal, 50),
        ("ueberwacher_name", ueberwacher_name, 150),
    ):
        if value is not None:
            setattr(troop, field, value.strip()[:length] or None)
    write_incident_change(
        db, incident_id, "troop.details", "breathing_troop", troop_id,
        before=before, after={**{field: getattr(troop, field) for field in fields}, **event_audit(ts, nachgetragen)},
        user_id=request.state.user.id,
    )
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_details", "troop_id": troop_id,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/standort")
def troop_standort(
    incident_id: int, troop_id: int, request: Request,
    location_text: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "standort", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    troop.location_text = location_text.strip() or None
    write_incident_change(
        db, incident_id, "troop.standort", "breathing_troop", troop_id,
        before=None, after={"location_text": troop.location_text, **event_audit(ts, nachgetragen)},
        user_id=request.state.user.id,
    )
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_standort", "troop_id": troop_id,
        "location_text": troop.location_text,
    })
    return Response(status_code=204)


@router.post("/einsatz/{incident_id}/atemschutz/{troop_id}/auftrag")
def troop_auftrag(
    incident_id: int, troop_id: int, request: Request,
    task_text: str = Form(""),
    client_event_id: str | None = Form(None),
    erfasst_at: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("breathing_supervisor", "incident_leader", "admin", "recorder")),
    _guard: None = Depends(require_breathing_enabled),
    _session: None = Depends(require_asuew_session),
):
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        return Response(status_code=404)
    try:
        ts, nachgetragen, claimed = _ereignis_vorbereiten(
            db, incident_id, request.state.user.id, "auftrag", client_event_id, erfasst_at,
        )
    except ValueError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")
    if not claimed:
        return Response(status_code=204)
    troop.task_text = task_text.strip() or None
    write_incident_change(
        db, incident_id, "troop.auftrag", "breathing_troop", troop_id,
        before=None, after={"task_text": troop.task_text, **event_audit(ts, nachgetragen)},
        user_id=request.state.user.id,
    )
    db.commit()
    run_async(manager.broadcast, incident_id, {
        "type": "troop_auftrag", "troop_id": troop_id, "task_text": troop.task_text,
    })
    return Response(status_code=204)


@router.get("/einsatz/{incident_id}/atemschutz/{troop_id}/pdf")
def troop_pdf(
    incident_id: int, troop_id: int, request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_breathing_enabled),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)
    troop = db.get(BreathingTroop, troop_id)
    if not troop or troop.incident_id != incident_id:
        raise HTTPException(404)
    # Eager-load relationships needed for PDF
    db.refresh(troop, ["members", "pressure_logs", "deployments"])
    incident = db.get(Incident, incident_id)
    db.refresh(incident)

    from app.services.pdf_service import render_troop_pdf
    base_url = str(request.base_url).rstrip("/")
    pdf_bytes = render_troop_pdf(troop, incident, base_url=base_url)  # type: ignore[arg-type]

    safe_name = troop.name.replace(" ", "_").replace("/", "-")
    filename = f"AS-Protokoll_{safe_name}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
