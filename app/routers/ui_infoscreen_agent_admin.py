"""Org-Administration fuer Infoscreen-Geraete, Displays und Wochenplaene (S4)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.core.timezones import local_input_to_utc, org_tz
from app.db import get_db
from app.models.infoscreen_agent import (
    InfoscreenDevice,
    InfoscreenDisplay,
    InfoscreenOverride,
    InfoscreenSchedule,
    InfoscreenScheduleException,
    InfoscreenScheduleSlot,
    InfoscreenUrlAllowlist,
)
from app.models.master import OrgSettings
from app.models.objekt import AlarmInfoscreenToken, InfoscreenUrl
from app.models.user import User
from app.services import infoscreen_agent_admin as validation
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_overrides as overrides
from app.services import infoscreen_agent_service as devices
from app.services.infoscreen_agent_installer_cmd import installer_command
from app.services.infoscreen_agent_releases import installer_ready
from app.services.infoscreen_agent_resolver import Display, resolve
from app.services.infoscreen_agent_snapshot import alarm_url, lade_overrides, lade_schedule
from app.services.infoscreen_aktivitaet import lade_aktivitaet

router = APIRouter(prefix="/admin/infoscreen-agent", tags=["infoscreen-agent-admin"])
BASE = "/admin/infoscreen-agent"
MODELS: dict[str, Any] = {
    "geraete": InfoscreenDevice,
    "displays": InfoscreenDisplay,
    "plaene": InfoscreenSchedule,
    "slots": InfoscreenScheduleSlot,
    "ausnahmen": InfoscreenScheduleException,
}


def org(user: User) -> int:
    if user.org_id is None:
        raise HTTPException(403, "Organisation auswaehlen")
    return user.org_id


def rows(db: Session, model, org_id: int):
    return db.query(model).filter(model.org_id == org_id)


def own(db: Session, model, row_id: int, org_id: int):
    row = rows(db, model, org_id).filter(model.id == row_id).first()
    if row is None:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return row


def context(request: Request, db: Session, user: User, section: str) -> dict:
    org_id = org(user)
    all_devices = rows(db, InfoscreenDevice, org_id).order_by(InfoscreenDevice.name).all()
    displays = rows(db, InfoscreenDisplay, org_id).order_by(InfoscreenDisplay.name).all()
    schedules = rows(db, InfoscreenSchedule, org_id).order_by(InfoscreenSchedule.name).all()
    slots = rows(db, InfoscreenScheduleSlot, org_id).order_by(InfoscreenScheduleSlot.start_time).all()
    exceptions = rows(db, InfoscreenScheduleException, org_id).order_by(InfoscreenScheduleException.datum).all()
    now = datetime.now(UTC).replace(tzinfo=None)
    extra = {}
    if section == "uebersteuerung":
        history = (
            rows(db, InfoscreenOverride, org_id)
            .filter(InfoscreenOverride.bis >= now - timedelta(days=30))
            .order_by(InfoscreenOverride.von.desc())
            .all()
        )
        extra = dict(
            overrides=history,
            allowlist=rows(db, InfoscreenUrlAllowlist, org_id).all(),
            kinds=overrides.KINDS,
            now=now,
            timezone=org_tz(user.org).key,
            display_names={d.id: d.name for d in displays},
            live=live_states(db, user, displays),
        )
    return dict(
        **extra,
        request=request,
        user=user,
        section=section,
        base=BASE,
        devices=all_devices,
        update_policy=db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first(),
        displays=displays,
        schedules=schedules,
        slots=slots,
        exceptions=exceptions,
        tokens=rows(db, AlarmInfoscreenToken, org_id).filter(AlarmInfoscreenToken.aktiv.is_(True)).all(),
        urls=rows(db, InfoscreenUrl, org_id).filter(InfoscreenUrl.aktiv.is_(True)).all(),
        online={
            d.id: d.status == "active"
            and bool(
                (d.agent_presence_expires_at and d.agent_presence_expires_at > now)
                or (d.last_seen_at and d.last_seen_at > now - timedelta(minutes=3))
            )
            for d in all_devices
        },
        grids={s.id: validation.week_grid([slot for slot in slots if slot.schedule_id == s.id]) for s in schedules},
        weekdays=("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    )


def render(request: Request, db: Session, user: User, section: str, *, error: str = "", status: int = 200):
    if section not in ("geraete", "displays", "plaene", "uebersteuerung"):
        raise HTTPException(404)
    partial = request.headers.get("HX-Request") == "true"
    if error and partial:
        return templates.TemplateResponse(
            request,
            "infoscreen_agent/_error.html",
            {"error": error},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )
    return templates.TemplateResponse(
        request,
        "infoscreen_agent/_content.html" if partial else "infoscreen_agent/admin.html",
        context(request, db, user, section) | {"error": error},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def changed(
    db: Session, tasks: BackgroundTasks, org_id: int, display_ids: list[int], device_ids: list[int] | None = None
) -> None:
    affected = {
        d.device_id for d in rows(db, InfoscreenDisplay, org_id).filter(InfoscreenDisplay.id.in_(display_ids)).all()
    } | set(device_ids or [])
    # Atomarer Zaehler verhindert verlorene Versionen bei parallelen Formularen.
    # Der SQL-Schreibzugriff braucht explizites Tenant-Scoping (kein SELECT-Listener).
    db.execute(
        update(InfoscreenDevice)
        .where(
            InfoscreenDevice.org_id == org_id,
            InfoscreenDevice.id.in_(affected),
        )
        .values(config_version=InfoscreenDevice.config_version + 1)
    )
    db.commit()
    tasks.add_task(events.publish_config_changed, org_id, display_ids)


def dialog(request: Request, db: Session, user: User, device: InfoscreenDevice, code: str = ""):
    now = datetime.now(UTC).replace(tzinfo=None)
    remaining = max(0, int((device.pairing_expires_at - now).total_seconds())) if device.pairing_expires_at else 0
    connected = bool(device.agent_presence_expires_at and device.agent_presence_expires_at > now)
    installer_enabled = installer_ready(db)
    installer_error = ""
    command = env_command = ""
    if code:
        try:
            command = installer_command(code, device.name)
            env_command = installer_command(code, device.name, environment=True)
        except ValueError:
            installer_enabled = False
            installer_error = "Fuer die Installation muss eine HTTPS-Serveradresse ohne Pfad konfiguriert sein."
    return templates.TemplateResponse(
        request,
        "infoscreen_agent/_pairing.html",
        dict(
            user=user,
            device=device,
            code=code,
            remaining=remaining,
            connected=connected,
            base=BASE,
            installer_enabled=installer_enabled,
            installer_error=installer_error,
            command=command,
            env_command=env_command,
        ),
        headers={"Cache-Control": "no-store", "HX-Trigger": "agentChanged"},
    )


def pairing(db: Session, user: User, device: InfoscreenDevice) -> str:
    code = devices.erzeuge_pairing_code(db, device)
    write_audit(
        db,
        "infoscreen_agent.pairing_created",
        org_id=org(user),
        user_id=user.id,
        payload={"device_id": device.id, "message": "Pairing-Code erzeugt"},
    )
    return code


@router.get("", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin"))):
    return render(request, db, user, "geraete")


@router.post("/geraete/neu", response_class=HTMLResponse)
def device_new(
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    form = run_async(request.form)
    try:
        if rows(db, InfoscreenDevice, org(user)).count() >= 200:
            raise ValueError("Maximal 200 Geraete")
        device = InfoscreenDevice(org_id=org(user), name=validation.text(form.get("name")), status="pending")
        db.add(device)
        db.flush()
        code = pairing(db, user, device)
        changed(db, tasks, org(user), [], [device.id])
        return dialog(request, db, user, device, code)
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "infoscreen_agent/_error.html", {"error": str(exc)}, status_code=422)


@router.get("/geraete/{device_id}/status", response_class=HTMLResponse)
def pairing_status(
    device_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin"))
):
    device = own(db, InfoscreenDevice, device_id, org(user))
    return templates.TemplateResponse(
        request,
        "infoscreen_agent/_pairing_status.html",
        dict(user=user, device=device, base=BASE, now=datetime.now(UTC).replace(tzinfo=None)),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/geraete/{device_id}/update", response_class=HTMLResponse)
def device_update(device_id: int, request: Request, tasks: BackgroundTasks,
                  db: Session = Depends(get_db), user: User = Depends(require_role("org_admin"))):
    from app.services.infoscreen_agent_releases import valid_version

    device = own(db, InfoscreenDevice, device_id, org(user))
    form = run_async(request.form)
    try:
        device.channel = validation.choice(form.get("channel"), ("stable", "beta"))
        pin = str(form.get("pin", "")).strip()
        device.update_pinned_version = valid_version(pin) if pin else None
        device.update_locked = form.get("locked") == "1"
        db.execute(update(InfoscreenDevice).where(
            InfoscreenDevice.org_id == org(user), InfoscreenDevice.id == device.id
        ).values(config_version=InfoscreenDevice.config_version + 1))
        write_audit(db, "infoscreen_agent.device_update_policy", org_id=org(user), user_id=user.id,
                    payload={"device_id": device.id, "locked": device.update_locked, "channel": device.channel,
                             "pinned_version": device.update_pinned_version})
        db.commit()
        tasks.add_task(events.publish_update_available, org(user), device.id)
        return render(request, db, user, "geraete")
    except ValueError as exc:
        db.rollback()
        return render(request, db, user, "geraete", error=str(exc), status=422)


@router.post("/geraete/{device_id}/update-check", response_class=HTMLResponse)
def device_update_check(device_id: int, request: Request, tasks: BackgroundTasks,
                        db: Session = Depends(get_db), user: User = Depends(require_role("org_admin"))):
    device = own(db, InfoscreenDevice, device_id, org(user))
    write_audit(db, "infoscreen_agent.update_check", org_id=org(user), user_id=user.id,
                payload={"device_id": device.id})
    db.commit()
    tasks.add_task(events.publish_update_available, org(user), device.id)
    return render(request, db, user, "geraete")


@router.post("/geraete/{device_id}/{action}", response_class=HTMLResponse)
def device_action(
    device_id: int,
    action: str,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    device = own(db, InfoscreenDevice, device_id, org(user))
    form = run_async(request.form)
    display_ids = [
        d.id for d in rows(db, InfoscreenDisplay, org(user)).filter(InfoscreenDisplay.device_id == device.id).all()
    ]
    code = ""
    try:
        if action == "umbenennen":
            device.name = validation.text(form.get("name"))
        elif action == "sperren":
            devices.sperren(device)
        elif action == "entsperren":
            devices.entsperren(device)
            code = pairing(db, user, device)
        elif action in ("rotieren", "code"):
            if device.status == "revoked":
                raise ValueError("Geraet zuerst entsperren")
            # Kein neuer Bearer im Browser. Alle bisherigen Zugangsdaten verwerfen.
            devices.sperren(device)
            devices.entsperren(device)
            code = pairing(db, user, device)
        elif action == "loeschen":
            if form.get("confirm") != "ja":
                raise ValueError("Loeschen bitte bestaetigen")
            devices.sperren(device)
            db.flush()
            for display in (
                rows(db, InfoscreenDisplay, org(user)).filter(InfoscreenDisplay.device_id == device.id).all()
            ):
                db.delete(display)
            db.delete(device)
        else:
            raise HTTPException(404)
        write_audit(
            db, "infoscreen_agent.device_" + action, org_id=org(user), user_id=user.id, payload={"device_id": device_id}
        )
        changed(db, tasks, org(user), display_ids, [] if action == "loeschen" else [device_id])
        if action in ("sperren", "rotieren", "code", "loeschen"):
            tasks.add_task(events.disconnect_device, org(user), device_id)
        if code:
            return dialog(request, db, user, device, code)
        return render(request, db, user, "geraete")
    except ValueError as exc:
        db.rollback()
        return render(request, db, user, "geraete", error=str(exc), status=422)


@router.get("/vorschau", response_class=HTMLResponse)
def preview(
    request: Request,
    display_id: int,
    weekday: int = 0,
    clock: str = "10:00",
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    display = own(db, InfoscreenDisplay, display_id, org(user))
    device = own(db, InfoscreenDevice, display.device_id, org(user))
    schedule = lade_schedule(db, display)
    zone = schedule.timezone if schedule else org_tz(user.org).key
    try:
        weekday = validation.number(weekday, 0, 6)
        if time.fromisoformat(clock).tzinfo is not None:
            raise ValueError("Uhrzeit ohne Zeitzonenoffset eingeben")
        today = datetime.now(ZoneInfo(zone)).date()
        day = today + timedelta(days=(weekday - today.weekday()) % 7)
        # Derselbe Helper wie fuer Formulare, mit der Plan- statt Org-Zeitzone.
        now = local_input_to_utc(day.isoformat() + "T" + clock, SimpleNamespace(timezone=zone))
        if now is None:
            raise ValueError("Ungueltige Uhrzeit")
        url = alarm_url(db, display)
        data = resolve(
            Display(
                display.id,
                org(user),
                url or "about:blank",
                device.status != "active" or not display.aktiv or url is None,
                display.gsl_override_erlaubt,
                display.nachlauf_min,
                device.config_version,
                display.revision,
            ),
            now,
            lade_aktivitaet(db, org(user)),
            lade_overrides(db, display),
            schedule,
        )
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    url_labels = {url.id: url.label for url in rows(db, InfoscreenUrl, org(user)).all()}
    rotation = [
        {
            "label": "Wetter"
            if entry.get("builtin") == "weather"
            else url_labels.get(entry.get("url_id"), "Nicht mehr aktive Seite"),
            "dwell": entry.get("dwell_sec"),
        }
        for entry in data.get("rotation_ref") or []
    ]
    return templates.TemplateResponse(
        request,
        "infoscreen_agent/_preview.html",
        dict(user=user, result=data, display=display, day=day, clock=clock, zone=zone, rotation=rotation),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{entity}/{row_id}/speichern", response_class=HTMLResponse)
def save(
    entity: str,
    row_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    if entity not in ("displays", "plaene", "slots", "ausnahmen"):
        raise HTTPException(404)
    model = MODELS[entity]
    row = own(db, model, row_id, org(user)) if row_id else None
    form = dict(run_async(request.form))
    section = "displays" if entity == "displays" else "plaene"
    try:
        if (
            row is None
            and rows(db, model, org(user)).count()
            >= {"displays": 500, "plaene": 100, "slots": 2000, "ausnahmen": 3660}[entity]
        ):
            raise ValueError("Maximale Anzahl erreicht")
        if entity == "displays":
            device = own(db, InfoscreenDevice, int(str(form.get("device_id"))), org(user))
            schedule_id = int(str(form["schedule_id"])) if form.get("schedule_id") else None
            token_id = int(str(form["alarm_token_id"])) if form.get("alarm_token_id") else None
            if schedule_id:
                own(db, InfoscreenSchedule, schedule_id, org(user))
            if token_id:
                token = own(db, AlarmInfoscreenToken, token_id, org(user))
                if not token.aktiv:
                    raise ValueError("Token ist nicht aktiv")
                if (
                    rows(db, InfoscreenDisplay, org(user))
                    .filter(InfoscreenDisplay.alarm_token_id == token_id, InfoscreenDisplay.id != row_id)
                    .first()
                ):
                    raise ValueError("Token ist bereits einem Display zugeordnet")
            values = dict(
                device_id=device.id,
                display_key=validation.text(form.get("display_key")),
                name=validation.text(form.get("name")),
                schedule_id=schedule_id,
                alarm_token_id=token_id,
                power_mode=validation.choice(
                    form.get("power_mode"), ("auto", "logical_blank", "output_power_save", "hardware_standby")
                ),
                nachlauf_min=validation.number(form.get("nachlauf_min", 10), 0, 1440),
                offline_wake_after_min=validation.number(form["offline_wake_after_min"], 1, 1440)
                if form.get("offline_wake_after_min")
                else None,
                gsl_override_erlaubt=form.get("gsl_override_erlaubt") == "1",
                aktiv=form.get("aktiv") == "1",
            )
        elif entity == "plaene":
            zone = validation.text(form.get("timezone"), 64)
            ZoneInfo(zone)
            values = dict(
                name=validation.text(form.get("name")),
                timezone=zone,
                ruhemodus=validation.choice(form.get("ruhemodus"), ("aus", "schwarz")),
            )
        else:
            schedule_id = int(str(form.get("schedule_id")))
            own(db, InfoscreenSchedule, schedule_id, org(user))
            if row and row.schedule_id != schedule_id:
                raise ValueError("Zeitplan-Zuordnung kann nicht geaendert werden")
            if entity == "slots":
                multi = run_async(request.form)
                form["weekdays"] = str(sum(1 << validation.number(v, 0, 6) for v in set(multi.getlist("days"))))
                values = validation.slot_values(db, org(user), form)
                if validation.check_overlap(db, org(user), schedule_id, values, row_id) and not form.get("ack_overlap"):
                    raise ValueError("Warnung: Slots gleicher Prioritaet ueberlappen. Bitte bestaetigen.")
            else:
                from datetime import date

                values = dict(
                    datum=date.fromisoformat(str(form.get("datum"))),
                    label=validation.text(form.get("label"), 200, required=False),
                    slots_ersatz=validation.replacement_slots(
                        db, org(user), str(form.get("slots_ersatz") or "[]"), bool(form.get("ack_overlap"))
                    ),
                )
            limit = validation.MAX_SLOTS if entity == "slots" else validation.MAX_EXCEPTIONS
            if not row and rows(db, model, org(user)).filter(model.schedule_id == schedule_id).count() >= limit:
                raise ValueError("Maximale Anzahl pro Zeitplan erreicht")
            values["schedule_id"] = schedule_id
        previous_target = (row.device_id, row.display_key, row.revision) if entity == "displays" and row else None
        previous_device = previous_target[0] if previous_target else None
        if row is None:
            row = model(org_id=org(user), **values)
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        db.flush()
        targets = (
            [row.id]
            if entity == "displays"
            else [
                d.id
                for d in rows(db, InfoscreenDisplay, org(user))
                .filter(InfoscreenDisplay.schedule_id == (row.id if entity == "plaene" else row.schedule_id))
                .all()
            ]
        )
        write_audit(
            db, "infoscreen_agent." + entity + "_saved", org_id=org(user), user_id=user.id, payload={"id": row.id}
        )
        changed(db, tasks, org(user), targets, [previous_device] if previous_device else [])
        if previous_target and previous_target[:2] != (row.device_id, row.display_key):
            tasks.add_task(events.publish_device_config_changed, org(user), *previous_target)
        return render(request, db, user, section)
    except (ValueError, KeyError, ZoneInfoNotFoundError, IntegrityError) as exc:
        db.rollback()
        message = (
            "Display-Key, Token oder Ausnahmedatum bereits belegt" if isinstance(exc, IntegrityError) else str(exc)
        )
        return render(request, db, user, section, error=message, status=422)


@router.post("/{entity}/{row_id}/loeschen", response_class=HTMLResponse)
def delete(
    entity: str,
    row_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    if entity not in ("displays", "plaene", "slots", "ausnahmen"):
        raise HTTPException(404)
    row = own(db, MODELS[entity], row_id, org(user))
    if run_async(request.form).get("confirm") != "ja":
        raise HTTPException(422, "Loeschen bitte bestaetigen")
    targets = (
        [row]
        if entity == "displays"
        else rows(db, InfoscreenDisplay, org(user))
        .filter(InfoscreenDisplay.schedule_id == (row.id if entity == "plaene" else row.schedule_id))
        .all()
    )
    display_ids, device_ids = [d.id for d in targets], [d.device_id for d in targets]
    if entity == "plaene":
        for display in targets:
            display.schedule_id = None
        for model in (InfoscreenScheduleSlot, InfoscreenScheduleException):
            for child in rows(db, model, org(user)).filter(model.schedule_id == row.id).all():
                db.delete(child)
    removed_target = (row.device_id, row.display_key, row.revision) if entity == "displays" else None
    db.delete(row)
    write_audit(
        db, "infoscreen_agent." + entity + "_deleted", org_id=org(user), user_id=user.id, payload={"id": row_id}
    )
    changed(db, tasks, org(user), display_ids, device_ids)
    if removed_target:
        tasks.add_task(events.publish_device_config_changed, org(user), *removed_target)
    return render(request, db, user, "displays" if entity == "displays" else "plaene")


def live_states(db: Session, user: User, displays: list[InfoscreenDisplay]) -> list[dict]:
    """Reine Aufloesung: niemals state() und damit keine Revision schreiben."""
    now = datetime.now(UTC).replace(tzinfo=None)
    activity = lade_aktivitaet(db, org(user))
    result = []
    for display in displays:
        device = own(db, InfoscreenDevice, display.device_id, org(user))
        url = alarm_url(db, display)
        loaded_overrides = lade_overrides(db, display)
        state = resolve(
            Display(
                display.id,
                org(user),
                url or "about:blank",
                device.status != "active" or not display.aktiv or url is None,
                display.gsl_override_erlaubt,
                display.nachlauf_min,
                device.config_version,
                display.revision,
            ),
            now,
            activity,
            loaded_overrides,
            lade_schedule(db, display),
        )
        label = {
            "locked": "Gesperrt",
            "gsl": "GSL",
            "incident": "Einsatz",
            "nachlauf": "Nachlauf",
            "default": "Alarm-Infoscreen",
            "schedule": "Zeitplan",
        }.get(state["mode"], "Uebersteuerung")
        for item in loaded_overrides:
            if state["mode"] in ("override", "manual") and item["url"] == state["url"]:
                label = overrides.KINDS.get(item["url_kind"], label)
        result.append(
            dict(
                display=display,
                label=label,
                mode=state["mode"],
                until=datetime.fromisoformat(state["valid_until"]) if state["valid_until"] else None,
            )
        )
    return result


@router.get("/uebersteuerung/status", response_class=HTMLResponse)
def override_status(
    request: Request,
    display_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    displays = (
        [own(db, InfoscreenDisplay, display_id, org(user))]
        if display_id is not None
        else rows(db, InfoscreenDisplay, org(user)).order_by(InfoscreenDisplay.name).all()
    )
    return templates.TemplateResponse(
        request,
        "infoscreen_agent/_override_status.html",
        dict(user=user, base=BASE, live=live_states(db, user, displays)),
        headers={"Cache-Control": "no-store"},
    )


def override_changed(db: Session, tasks: BackgroundTasks, user: User, display_id: int | None) -> None:
    targets = rows(db, InfoscreenDisplay, org(user))
    if display_id is not None:
        targets = targets.filter(InfoscreenDisplay.id == display_id)
    all_device_ids = [d.id for d in rows(db, InfoscreenDevice, org(user)).all()] if display_id is None else []
    changed(db, tasks, org(user), [d.id for d in targets.all()], all_device_ids)


@router.post("/overrides/neu", response_class=HTMLResponse)
def override_new(
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    form = run_async(request.form)
    try:
        display_id = validation.number(form.get("display_id"), 1, 2**63 - 1) if form.get("display_id") else None
        display = own(db, InfoscreenDisplay, display_id, org(user)) if display_id is not None else None
        kind = validation.choice(form.get("kind"), ("gsl_page", "manual"))
        url_kind = validation.choice(form.get("url_kind"), tuple(overrides.KINDS))
        reason = validation.text(form.get("reason"), 500)
        now = datetime.now(UTC).replace(tzinfo=None)
        begin: datetime | None
        end: datetime | None
        if form.get("minutes"):
            if kind != "gsl_page" or url_kind != "weather" or not display or not display.gsl_override_erlaubt:
                raise ValueError("Schnellaktion braucht ein fuer GSL-Uebersteuerung freigegebenes Display")
            minutes = validation.number(form.get("minutes"), 30, 120)
            if minutes not in (30, 120):
                raise ValueError("Schnellaktion: 30 Minuten oder 2 Stunden")
            begin, end = now, now + timedelta(minutes=minutes)
        else:
            for key in ("von", "bis"):
                if datetime.fromisoformat(str(form.get(key, ""))).tzinfo is not None:
                    raise ValueError("Lokale Zeit ohne Zeitzonenoffset eingeben")
            begin = local_input_to_utc(str(form.get("von", "")), user.org)
            end = local_input_to_utc(str(form.get("bis", "")), user.org)
        if begin is None or end is None or not timedelta(0) < end - begin <= timedelta(hours=8) or end <= now:
            raise ValueError("Von/bis erforderlich; Ablauf in der Zukunft, Dauer maximal 8 Stunden")
        url = overrides.page_url(db, org(user), url_kind, str(form.get("url_ref", "")).strip())
        row = InfoscreenOverride(
            org_id=org(user),
            display_id=display_id,
            kind=kind,
            url_kind=url_kind,
            url_ref=url,
            von=begin,
            bis=end,
            reason=reason,
            created_by=user.id,
        )
        db.add(row)
        db.flush()
        write_audit(
            db,
            "infoscreen_agent.override_created",
            org_id=org(user),
            user_id=user.id,
            entity_type="infoscreen_override",
            entity_id=row.id,
            payload={
                "created_by": user.id,
                "reason": reason,
                "display_id": display_id,
                "kind": kind,
                "url_kind": url_kind,
            },
        )
        override_changed(db, tasks, user, display_id)
        return render(request, db, user, "uebersteuerung")
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return render(request, db, user, "uebersteuerung", error=str(exc), status=422)


@router.post("/overrides/{override_id}/aufheben", response_class=HTMLResponse)
def override_cancel(
    override_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    row = own(db, InfoscreenOverride, override_id, org(user))
    try:
        reason = validation.text(run_async(request.form).get("reason"), 500)
        if row.aufgehoben_am is not None:
            raise ValueError("Uebersteuerung wurde bereits aufgehoben")
        row.aufgehoben_am = datetime.now(UTC).replace(tzinfo=None)
        row.aufgehoben_von = user.id
        write_audit(
            db,
            "infoscreen_agent.override_cancelled",
            org_id=org(user),
            user_id=user.id,
            entity_type="infoscreen_override",
            entity_id=row.id,
            payload={"created_by": row.created_by, "aufgehoben_von": user.id, "reason": reason},
        )
        override_changed(db, tasks, user, row.display_id)
        return render(request, db, user, "uebersteuerung")
    except ValueError as exc:
        db.rollback()
        return render(request, db, user, "uebersteuerung", error=str(exc), status=422)


@router.post("/allowlist/neu", response_class=HTMLResponse)
def allowlist_new(
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    try:
        host = overrides.hostname(str(run_async(request.form).get("host", "")))
        db.add(InfoscreenUrlAllowlist(org_id=org(user), host=host))
        db.flush()
        write_audit(db, "infoscreen_agent.allowlist_created", org_id=org(user), user_id=user.id)
        override_changed(db, tasks, user, None)
        return render(request, db, user, "uebersteuerung")
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        message = "Host ist bereits freigegeben" if isinstance(exc, IntegrityError) else str(exc)
        return render(request, db, user, "uebersteuerung", error=message, status=422)


@router.post("/allowlist/{host_id}/entfernen", response_class=HTMLResponse)
def allowlist_remove(
    host_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    row = own(db, InfoscreenUrlAllowlist, host_id, org(user))
    db.delete(row)
    write_audit(db, "infoscreen_agent.allowlist_removed", org_id=org(user), user_id=user.id)
    override_changed(db, tasks, user, None)
    return render(request, db, user, "uebersteuerung")


@router.post("/update-policy", response_class=HTMLResponse)
def org_update_policy(request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db),
                      user: User = Depends(require_role("org_admin"))):
    import re

    form = run_async(request.form)
    start, end = str(form.get("start", "")), str(form.get("end", ""))
    if any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) for value in (start, end)) or start == end:
        raise HTTPException(422, "Gueltige, unterschiedliche Uhrzeiten im Format HH:MM eingeben")
    policy = db.query(OrgSettings).filter(OrgSettings.org_id == org(user)).first()
    if policy is None:
        policy = OrgSettings(org_id=org(user))
        db.add(policy)
    policy.infoscreen_update_start, policy.infoscreen_update_end = start, end
    policy.infoscreen_critical_allowed = form.get("critical_allowed") == "1"
    write_audit(db, "infoscreen_agent.org_update_policy", org_id=org(user), user_id=user.id,
                payload={"start": start, "end": end, "critical_allowed": policy.infoscreen_critical_allowed})
    targets = rows(db, InfoscreenDevice, org(user)).all()
    db.execute(update(InfoscreenDevice).where(
        InfoscreenDevice.org_id == org(user)
    ).values(config_version=InfoscreenDevice.config_version + 1))
    db.commit()
    for device in targets:
        tasks.add_task(events.publish_update_available, org(user), device.id)
    return render(request, db, user, "geraete")


@router.get("/{section}", response_class=HTMLResponse)
def section_page(
    section: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("org_admin"))
):
    return render(request, db, user, section)
