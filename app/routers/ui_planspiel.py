"""Uebersicht der Planspiel-Laeufe."""

import asyncio
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.permissions import PLANSPIEL_LEITER_ROLES, PLANSPIEL_LESE_ROLLEN, has_role, require_role
from app.core.templating import templates
from app.db import SessionLocal, get_db
from app.models.master import AlarmType
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
from app.models.planspiel import (
    PLANSPIEL_STATUS_LABELS,
    PlanspielAdresse,
    PlanspielBeobachtung,
    PlanspielMitglied,
    PlanspielRun,
    PlanspielRunEreignis,
    PlanspielSzenario,
)
from app.models.user import User
from app.services.broadcast import broadcast_org
from app.services.planspiel_engine import (
    beenden,
    ereignis_jetzt,
    ereignis_ueberspringen,
    ereignis_wiederholen,
    folgemeldungen_setzen,
    fortsetzen,
    freie_meldung,
    nach_ausfuehrung_benachrichtigen,
    pausieren,
    sim_sekunden,
    stichwort_aendern,
    tick,
)
from app.services.planspiel_katalog_service import aktive_version, stichwort_vorschlag, szenarien_fuer_stichwort
from app.services.planspiel_ort_service import GebietFehlt, OrtNichtGefunden
from app.services.planspiel_run_service import (
    EntwurfDaten,
    ZustandsFehler,
    entwurf_aktualisieren,
    entwurf_anlegen,
    entwurf_verwerfen,
    nach_start_benachrichtigen,
    starten,
    vorschau,
)

router = APIRouter(prefix="/planspiele", tags=["planspiele"])

_STATUS_GRUPPEN = {
    "entwuerfe": ("Entwürfe", ("entwurf", "bereit")),
    "laufend": ("Laufend", ("laeuft", "pausiert", "generierung_gestoppt")),
    "beendet": ("Beendet", ("beendet", "abgebrochen")),
}


def require_planspiel_enabled(request: Request) -> None:
    if not getattr(request.state, "planspiel_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _runs_for_user(db: Session, user: User) -> dict[str, list[PlanspielRun]]:
    if user.org_id is None:
        return {key: [] for key in _STATUS_GRUPPEN}

    query = db.query(PlanspielRun).filter(PlanspielRun.org_id == user.org_id)
    if not has_role(user, *PLANSPIEL_LEITER_ROLES):
        query = query.join(PlanspielMitglied, PlanspielMitglied.run_id == PlanspielRun.id).filter(
            PlanspielMitglied.user_id == user.id
        )
    runs = query.order_by(PlanspielRun.created_at.desc()).all()
    return {
        key: [run for run in runs if run.status in statuses]
        for key, (_, statuses) in _STATUS_GRUPPEN.items()
    }


def _leiter_namen(db: Session, groups: dict[str, list[PlanspielRun]]) -> dict[int, str]:
    ids = {run.leiter_user_id for runs in groups.values() for run in runs if run.leiter_user_id is not None}
    if not ids:
        return {}
    return {
        user.id: user.display_name or user.username
        for user in db.query(User).filter(User.id.in_(ids)).all()
    }


def _org(user: User) -> int:
    if user.org_id is None:
        raise HTTPException(status_code=400, detail="Keine Organisation ausgewählt")
    return user.org_id


def _run(db: Session, user: User, run_id: int) -> PlanspielRun:
    run = db.query(PlanspielRun).filter(PlanspielRun.id == run_id, PlanspielRun.org_id == _org(user)).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    if not has_role(user, *PLANSPIEL_LEITER_ROLES):
        member = db.query(PlanspielMitglied).filter(
            PlanspielMitglied.run_id == run.id,
            PlanspielMitglied.org_id == run.org_id,
            PlanspielMitglied.user_id == user.id,
        ).one_or_none()
        if member is None:
            raise HTTPException(status_code=404, detail="Nicht gefunden")
    return run


def _darf_steuern(db: Session, user: User, run: PlanspielRun) -> bool:
    if has_role(user, *PLANSPIEL_LEITER_ROLES):
        return True
    return db.query(PlanspielMitglied.id).filter(
        PlanspielMitglied.run_id == run.id,
        PlanspielMitglied.org_id == run.org_id,
        PlanspielMitglied.user_id == user.id,
        PlanspielMitglied.rolle.in_(("leiter", "regie")),
    ).first() is not None


def _require_steuern(db: Session, user: User, run: PlanspielRun) -> None:
    if not _darf_steuern(db, user, run):
        raise HTTPException(status_code=403, detail="Regie-Berechtigung erforderlich")


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ist_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _timeline_context(db: Session, user: User, run: PlanspielRun, fehler: str | None = None) -> dict[str, object]:
    events = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.run_id == run.id,
    ).all()
    pending = [event for event in events if event.status in {"geplant", "faellig", "laeuft"}]
    pending.sort(key=lambda event: (event.faellig_sim_s is None, event.faellig_sim_s or 0, event.seq))
    done = sorted((event for event in events if event.status == "ausgefuehrt"),
                  key=lambda event: event.ausgefuehrt_at or datetime.min, reverse=True)
    failed = [event for event in events if event.status in {"uebersprungen", "abgebrochen", "fehlgeschlagen"}]
    return {"run": run, "user": user, "anstehend": pending, "ausgefuehrt": done,
            "fehlgeschlagen": failed, "can_steuern": _darf_steuern(db, user, run), "fehler": fehler}


def _cockpit_context(request: Request, db: Session, user: User, run: PlanspielRun,
                     fehler: str | None = None) -> dict[str, object]:
    observations = db.query(PlanspielBeobachtung).filter(
        PlanspielBeobachtung.org_id == run.org_id,
        PlanspielBeobachtung.run_id == run.id,
    ).order_by(PlanspielBeobachtung.created_at.desc()).all()
    members = db.query(PlanspielMitglied, User).join(User, User.id == PlanspielMitglied.user_id).filter(
        PlanspielMitglied.org_id == run.org_id, PlanspielMitglied.run_id == run.id,
    ).order_by(User.display_name).all()
    users = db.query(User).filter(User.org_id == run.org_id, User.active.is_(True)).order_by(User.display_name).all()
    now = _jetzt()
    return {"user": user, "run": run, "fehler": fehler, "status_labels": PLANSPIEL_STATUS_LABELS,
            "sim_s": sim_sekunden(run, now), "server_jetzt": now.isoformat() + "Z",
            "can_steuern": _darf_steuern(db, user, run), "beobachtungen": observations,
            "mitglieder": members, "org_users": users,
            "alarm_types": db.query(AlarmType).filter(AlarmType.org_id == run.org_id).order_by(AlarmType.code).all()}


def _wizard_context(request: Request, db: Session, user: User, form_data: dict[str, object] | None = None, *,
                    fehler: str | None = None, run: PlanspielRun | None = None) -> dict[str, object]:
    org_id = _org(user)
    scenarios = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == org_id,
        PlanspielSzenario.aktiv.is_(True),
        PlanspielSzenario.archiviert_at.is_(None),
    ).order_by(PlanspielSzenario.code).all()
    grouped: dict[str, list[tuple[PlanspielSzenario, object]]] = {}
    for scenario in scenarios:
        version = aktive_version(db, scenario)
        if version is not None:
            grouped.setdefault(version.kategorie, []).append((scenario, version))
    pool = db.query(PlanspielAdresse).filter(
        PlanspielAdresse.org_id == org_id,
        PlanspielAdresse.aktiv.is_(True),
    ).order_by(PlanspielAdresse.quelle, PlanspielAdresse.strasse).limit(300).all()
    objects = db.query(Objekt).filter(
        Objekt.org_id == org_id,
        Objekt.status == OBJEKT_STATUS_FREIGEGEBEN,
        Objekt.lat.is_not(None),
        Objekt.lng.is_not(None),
    ).order_by(Objekt.name).all()
    return {
        "user": user,
        "form": form_data or (run.konfig if run else {}),
        "fehler": fehler,
        "run": run,
        "szenarien": grouped,
        "pool": pool,
        "objekte": objects,
    }


def _form_data(form: FormData, user: User) -> tuple[EntwurfDaten, dict[str, object]]:
    def text(name: str) -> str | None:
        value = str(form.get(name, "")).strip()
        return value or None

    def number(name: str, cast: object) -> object | None:
        value = text(name)
        return cast(value) if value is not None else None  # type: ignore[operator]

    raw: dict[str, object] = {key: form.get(key, "") for key in form.keys()}
    raw["folgemeldungen_aktiv"] = bool(form.get("folgemeldungen_aktiv"))
    daten = EntwurfDaten(
        titel=text("titel") or "",
        lernziele=text("lernziele"),
        art=cast(Literal["einzel", "gsl"], text("art") or "einzel"),
        erzeugung=cast(Literal["manuell", "teilauto", "zufall"], text("erzeugung") or "manuell"),
        szenario_id=cast(int | None, number("szenario_id", int)),
        seed=cast(int | None, number("seed", int)),
        ort_modus=cast(Literal["zufall", "pool", "objekt", "manuell", "karte"], text("ort_modus") or "zufall"),
        adresse_id=cast(int | None, number("adresse_id", int)),
        objekt_id=cast(int | None, number("objekt_id", int)),
        strasse=text("strasse"), hausnr=text("hausnr"), plz=text("plz"), ort=text("ort"),
        lat=cast(float | None, number("lat", float)),
        lng=cast(float | None, number("lng", float)),
        bezeichnung=text("bezeichnung"),
        stichwort_code=text("stichwort_code"), alarmtext=text("alarmtext"),
        folgemeldungen_aktiv=bool(form.get("folgemeldungen_aktiv")),
        tempo=float(text("tempo") or "1"), leiter_user_id=user.id,
    )
    if daten.art != "einzel":
        raise ValueError("Derzeit ist nur Einzeleinsatz verfügbar")
    if daten.erzeugung not in {"manuell", "teilauto", "zufall"}:
        raise ValueError("Ungültige Erzeugungsart")
    if daten.ort_modus not in {"zufall", "pool", "objekt", "manuell", "karte"}:
        raise ValueError("Ungültiger Ortsmodus")
    return daten, raw


@router.get("", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
    _guard: None = Depends(require_planspiel_enabled),
):
    groups = _runs_for_user(db, user)
    context = {
        "user": user,
        "gruppen": _STATUS_GRUPPEN,
        "runs": groups,
        "status_labels": PLANSPIEL_STATUS_LABELS,
        "leiter_namen": _leiter_namen(db, groups),
        "can_leiten": has_role(user, *PLANSPIEL_LEITER_ROLES),
    }
    template = "planspiel/_liste.html" if request.headers.get("HX-Request") == "true" else "planspiel/index.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/neu", response_class=HTMLResponse)
def neu_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    return templates.TemplateResponse(request, "planspiel/assistent.html", _wizard_context(request, db, user))


@router.get("/neu/stichwort", response_class=HTMLResponse)
def stichwort_partial(
    request: Request,
    text: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    codes = stichwort_vorschlag(db, _org(user), text)
    scenarios: list[PlanspielSzenario] = []
    for code, _score in codes:
        scenarios.extend(szenarien_fuer_stichwort(db, _org(user), code))
    return templates.TemplateResponse(request, "planspiel/_stichwort_vorschlag.html", {
        "codes": codes,
        "szenarien": scenarios,
    })


@router.post("/neu")
async def neu_anlegen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    form = await request.form()
    raw: dict[str, object] = {}
    try:
        daten, raw = _form_data(form, user)
        run = entwurf_anlegen(db, _org(user), user, daten)
        db.commit()
        return RedirectResponse(f"/planspiele/{run.id}", status_code=303)
    except (TypeError, ValueError, ZustandsFehler, OrtNichtGefunden, GebietFehlt) as exc:
        db.rollback()
        if not raw:
            raw = {key: form.get(key, "") for key in form.keys()}
        return templates.TemplateResponse(
            request,
            "planspiel/assistent.html",
            _wizard_context(request, db, user, raw, fehler=str(exc)),
            status_code=400,
        )


@router.get("/{run_id:int}", response_class=HTMLResponse)
def detail(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    if run.status in {"laeuft", "pausiert", "generierung_gestoppt", "beendet", "abgebrochen"}:
        return templates.TemplateResponse(request, "planspiel/regie.html", _cockpit_context(request, db, user, run))
    leader = db.query(User).filter(User.id == run.leiter_user_id).one_or_none() if run.leiter_user_id else None
    events = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.org_id == _org(user), PlanspielRunEreignis.run_id == run.id,
    ).order_by(PlanspielRunEreignis.seq).all()
    return templates.TemplateResponse(request, "planspiel/detail.html", {
        "user": user,
        "run": run,
        "leiter": leader,
        "status_labels": PLANSPIEL_STATUS_LABELS,
        "vorschau": vorschau(db, run) if run.status in {"entwurf", "bereit"} else None,
        "ereignisse": events,
        "can_leiten": has_role(user, *PLANSPIEL_LEITER_ROLES),
    })


@router.get("/{run_id:int}/bearbeiten", response_class=HTMLResponse)
def bearbeiten_form(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    return templates.TemplateResponse(request, "planspiel/assistent.html", _wizard_context(request, db, user, run=run))


@router.post("/{run_id:int}/bearbeiten")
async def bearbeiten(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    form = await request.form()
    raw: dict[str, object] = {}
    try:
        daten, raw = _form_data(form, user)
        entwurf_aktualisieren(db, run, user, daten)
        db.commit()
        return RedirectResponse(f"/planspiele/{run.id}", status_code=303)
    except (TypeError, ValueError, ZustandsFehler, OrtNichtGefunden, GebietFehlt) as exc:
        db.rollback()
        if not raw:
            raw = {key: form.get(key, "") for key in form.keys()}
        return templates.TemplateResponse(
            request, "planspiel/assistent.html", _wizard_context(request, db, user, raw, fehler=str(exc), run=run),
            status_code=400,
        )


@router.post("/{run_id:int}/start")
async def start(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    try:
        incident = starten(db, run, user)
        db.commit()
        await nach_start_benachrichtigen(run, incident)
        return RedirectResponse(f"/planspiele/{run.id}", status_code=303)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return templates.TemplateResponse(request, "planspiel/detail.html", {
            "user": user, "run": run, "fehler": str(exc), "status_labels": PLANSPIEL_STATUS_LABELS,
            "vorschau": vorschau(db, run), "ereignisse": [], "can_leiten": True,
        }, status_code=400)


@router.get("/{run_id:int}/timeline", response_class=HTMLResponse)
def timeline(
    run_id: int, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    return templates.TemplateResponse(request, "planspiel/_regie_timeline.html", _timeline_context(db, user, run))


async def _broadcast(run: PlanspielRun) -> None:
    if run.org_id is not None:
        await broadcast_org(run.org_id, {"type": "planspiel_changed", "run_id": run.id})


async def _tick_now(run: PlanspielRun, jetzt: datetime) -> None:
    if run.org_id is None:
        return
    result = await asyncio.to_thread(tick, SessionLocal, run.id, run.org_id, jetzt)
    for item in result.ausgefuehrt:
        await nach_ausfuehrung_benachrichtigen(run.org_id, run.id, item)


def _action_response(request: Request, db: Session, user: User, run: PlanspielRun,
                     fehler: str | None = None) -> HTMLResponse | RedirectResponse:
    # Die Sofort-Ausfuehrung laeuft in eigenen Sessions; ohne expire_all zeigte
    # das Partial noch den alten Stand ("geplant").
    db.expire_all()
    if _ist_htmx(request):
        return templates.TemplateResponse(request, "planspiel/_regie_timeline.html",
                                          _timeline_context(db, user, run, fehler), status_code=400 if fehler else 200)
    if fehler:
        context = _cockpit_context(request, db, user, run, fehler)
        return templates.TemplateResponse(request, "planspiel/regie.html", context, status_code=400)
    return RedirectResponse(f"/planspiele/{run.id}", status_code=303)


@router.post("/{run_id:int}/pause")
async def pause(run_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    try:
        pausieren(db, run, user, _jetzt())
        db.commit()
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/fortsetzen")
async def resume(run_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                 _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    try:
        fortsetzen(db, run, user, _jetzt())
        db.commit()
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/folgemeldungen")
async def folgemeldungen(run_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                          _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    form = await request.form()
    try:
        folgemeldungen_setzen(db, run, user, str(form.get("aktiv", "0")) == "1")
        db.commit()
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/beenden")
async def ende(run_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
               _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    try:
        beenden(db, run, user, _jetzt())
        db.commit()
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


async def _event_action(run_id: int, event_id: int, request: Request, db: Session, user: User,
                        action: str):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    form = await request.form()
    jetzt = _jetzt()
    try:
        uuid = str(form.get("client_uuid") or "") or None
        if action == "jetzt":
            ereignis_jetzt(db, run, event_id, user, jetzt, uuid)
        elif action == "ueberspringen":
            ereignis_ueberspringen(db, run, event_id, user)
        else:
            ereignis_wiederholen(db, run, event_id, user, jetzt, uuid)
        db.commit()
        if action != "ueberspringen":
            await _tick_now(run, jetzt)
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/ereignis/{event_id:int}/jetzt")
async def event_jetzt(run_id: int, event_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                      _guard: None = Depends(require_planspiel_enabled)):
    return await _event_action(run_id, event_id, request, db, user, "jetzt")


@router.post("/{run_id:int}/ereignis/{event_id:int}/ueberspringen")
async def event_skip(run_id: int, event_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                     _guard: None = Depends(require_planspiel_enabled)):
    return await _event_action(run_id, event_id, request, db, user, "ueberspringen")


@router.post("/{run_id:int}/ereignis/{event_id:int}/wiederholen")
async def event_repeat(run_id: int, event_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                       _guard: None = Depends(require_planspiel_enabled)):
    return await _event_action(run_id, event_id, request, db, user, "wiederholen")


@router.post("/{run_id:int}/meldung")
async def meldung(run_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                  _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    form = await request.form()
    jetzt = _jetzt()
    try:
        freie_meldung(db, run, user, jetzt, text=str(form.get("text", "")).strip(),
                      ereignis_typ=str(form.get("ereignis_typ", "lageweit")),
                      sichtbarkeit=str(form.get("sichtbarkeit", "teilnehmer")),
                      client_uuid=str(form.get("client_uuid") or "") or None)
        db.commit()
        await _tick_now(run, jetzt)
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/stichwort")
async def stichwort(run_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                    _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    form = await request.form()
    try:
        stichwort_aendern(db, run, user, str(form.get("code", "")))
        db.commit()
        await _broadcast(run)
        return _action_response(request, db, user, run)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return _action_response(request, db, user, run, str(exc))


@router.post("/{run_id:int}/beobachtung")
async def beobachtung(run_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                      _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    form = await request.form()
    text = str(form.get("text", "")).strip()
    rating = str(form.get("bewertung", "")).strip()
    if not 1 <= len(text) <= 2000 or (rating and rating not in {"1", "2", "3", "4", "5"}):
        raise HTTPException(status_code=400, detail="Ungültige Beobachtung")
    row = PlanspielBeobachtung(org_id=run.org_id, run_id=run.id, user_id=user.id,
                               sim_s=sim_sekunden(run, _jetzt()), text=text,
                               kategorie=str(form.get("kategorie", "")).strip() or None,
                               lernziel=str(form.get("lernziel", "")).strip() or None,
                               bewertung=int(rating) if rating else None)
    db.add(row)
    db.commit()
    await _broadcast(run)
    context = _cockpit_context(request, db, user, run)
    if _ist_htmx(request):
        return templates.TemplateResponse(request, "planspiel/_beobachtungen.html", context)
    return RedirectResponse(f"/planspiele/{run.id}", status_code=303)


@router.post("/{run_id:int}/mitglied")
async def mitglied_hinzufuegen(run_id: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                               _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    form = await request.form()
    user_id = int(str(form.get("user_id", "0")))
    candidate = db.query(User).filter(User.id == user_id, User.org_id == run.org_id,
                                      User.active.is_(True)).one_or_none()
    role = str(form.get("rolle", ""))
    if candidate is None or role not in {"regie", "beobachter"}:
        raise HTTPException(status_code=400, detail="Ungültiges Mitglied")
    exists = db.query(PlanspielMitglied.id).filter(PlanspielMitglied.run_id == run.id,
                                                    PlanspielMitglied.user_id == candidate.id).first()
    if exists is None:
        db.add(PlanspielMitglied(org_id=run.org_id, run_id=run.id, user_id=candidate.id, rolle=role))
        db.commit()
        await _broadcast(run)
    return RedirectResponse(f"/planspiele/{run.id}", status_code=303)


@router.post("/{run_id:int}/mitglied/{member_id:int}/entfernen")
async def mitglied_entfernen(run_id: int, member_id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
                             _guard: None = Depends(require_planspiel_enabled)):
    run = _run(db, user, run_id)
    _require_steuern(db, user, run)
    member = db.query(PlanspielMitglied).filter(PlanspielMitglied.id == member_id,
                                                PlanspielMitglied.run_id == run.id,
                                                PlanspielMitglied.org_id == run.org_id).one_or_none()
    if member is None or member.rolle == "leiter":
        raise HTTPException(status_code=400, detail="Mitglied kann nicht entfernt werden")
    db.delete(member)
    db.commit()
    await _broadcast(run)
    return RedirectResponse(f"/planspiele/{run.id}", status_code=303)


@router.post("/{run_id:int}/verwerfen")
def verwerfen(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LEITER_ROLES)),
    _guard: None = Depends(require_planspiel_enabled),
):
    run = _run(db, user, run_id)
    try:
        entwurf_verwerfen(db, run, user)
        db.commit()
        return RedirectResponse("/planspiele", status_code=303)
    except (ValueError, ZustandsFehler) as exc:
        db.rollback()
        return templates.TemplateResponse(request, "planspiel/detail.html", {
            "user": user, "run": run, "fehler": str(exc), "status_labels": PLANSPIEL_STATUS_LABELS,
            "vorschau": vorschau(db, run), "ereignisse": [], "can_leiten": True,
        }, status_code=400)
