"""Probe-CRUD und Vorbereitung im Probenplanungsmodul."""

from __future__ import annotations

import calendar
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, func, or_, update
from sqlalchemy.orm import Session, joinedload

from app.core.dependencies import CurrentOrgId
from app.core.permissions import can_edit_proben, is_proben_admin
from app.core.templating import templates
from app.core.timezones import local_date_to_utc, local_input_to_utc, now_local, to_org_tz
from app.db import get_db
from app.models.master import Member
from app.models.probenplanung import (
    ChecklistItemTyp,
    Probeart,
    ProbeChange,
    ProbeCheckliste,
    ProbeChecklistItem,
    ProbeChecklistSection,
    ProbeMedia,
    TerminStatus,
)
from app.models.teilnahme import Termin
from app.models.user import User
from app.routers.ui_probenplanung_admin import require_probenplanung_enabled
from app.services.probe_checklist_service import fortschritt, snapshot_erzeugen, statuswechsel, uebersteuern
from app.services.probe_history import write_probe_change

router = APIRouter(prefix="/probenplanung", tags=["probenplanung"])

_ZEITRAEUME = {"zukuenftig", "vergangen", "alle"}


def _jahr_grenzen(jahr: int, org: object) -> tuple[datetime, datetime]:
    start = local_date_to_utc(f"{jahr:04d}-01-01", org=org)
    ende = local_date_to_utc(f"{jahr + 1:04d}-01-01", org=org)
    if start is None or ende is None:
        raise HTTPException(422, "Ungültiges Jahr")
    return start, ende


def _filter_query(
    query,
    *,
    org: object,
    jahr: int,
    probeart_id: int | None,
    status: str | None,
    verantwortlich_id: int | None,
    von: date | None,
    bis: date | None,
    q: str | None,
    zeitraum: str,
):
    start, ende = _jahr_grenzen(jahr, org)
    query = query.filter(Termin.beginn >= start, Termin.beginn < ende, Termin.archiviert_am.is_(None))
    if probeart_id is not None:
        query = query.filter(Termin.probeart_id == probeart_id)
    if status:
        query = query.filter(Termin.status == status)
    if verantwortlich_id is not None:
        query = query.filter(Termin.verantwortlich_member_id == verantwortlich_id)
    if von:
        von_utc = local_date_to_utc(von.isoformat(), org=org)
        if von_utc:
            query = query.filter(Termin.beginn >= von_utc)
    if bis:
        bis_utc = local_date_to_utc(bis.isoformat(), end=True, org=org)
        if bis_utc:
            query = query.filter(Termin.beginn <= bis_utc)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Termin.titel.ilike(pattern),
                Termin.thema.ilike(pattern),
                Termin.objekt.ilike(pattern),
                Termin.ort.ilike(pattern),
            )
        )
    jetzt = now_local(org).astimezone(UTC).replace(tzinfo=None)
    if zeitraum == "zukuenftig":
        query = query.filter(Termin.beginn >= jetzt)
    elif zeitraum == "vergangen":
        query = query.filter(Termin.beginn < jetzt)
    return query


def _listen_context(db: Session, user: User, termine: list[Termin]) -> dict:
    termin_ids = [termin.id for termin in termine]
    fortschritte: dict[int, dict[str, int]] = {}
    if termin_ids:
        rows = (
            db.query(
                ProbeCheckliste.termin_id,
                func.sum(case((ProbeChecklistItem.zustand != "nicht_relevant", 1), else_=0)),
                func.sum(case((ProbeChecklistItem.zustand == "erledigt", 1), else_=0)),
            )
            .outerjoin(ProbeChecklistItem, ProbeChecklistItem.checkliste_id == ProbeCheckliste.id)
            .filter(ProbeCheckliste.termin_id.in_(termin_ids))
            .group_by(ProbeCheckliste.termin_id)
            .all()
        )
        fortschritte = {
            termin_id: {"gesamt": int(gesamt or 0), "erledigt": int(erledigt or 0)}
            for termin_id, gesamt, erledigt in rows
        }
    member_ids = {
        member_id
        for termin in termine
        for member_id in (termin.verantwortlich_member_id, termin.unterstuetzung_member_id)
        if member_id is not None
    }
    members = db.query(Member).filter(Member.id.in_(member_ids)).all() if member_ids else []
    return {
        "user": user,
        "termine": termine,
        "fortschritte": fortschritte,
        "member_namen": {member.id: member.full_name for member in members},
        "can_edit": can_edit_proben(user),
    }


@router.get("", response_class=HTMLResponse)
def probenplan_liste(
    request: Request,
    jahr: int | None = Query(None, ge=1900, le=9998),
    probeart_id: int | None = None,
    status: str | None = None,
    verantwortlich_id: int | None = None,
    von: date | None = None,
    bis: date | None = None,
    q: str | None = None,
    zeitraum: str = "alle",
    uebernommen: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    if zeitraum not in _ZEITRAEUME:
        raise HTTPException(422, "Ungültiger Zeitraum")
    selected_year = jahr or now_local(user.org).year
    query = db.query(Termin).options(joinedload(Termin.probeart))
    termine = _filter_query(
        query,
        org=user.org,
        jahr=selected_year,
        probeart_id=probeart_id,
        status=status,
        verantwortlich_id=verantwortlich_id,
        von=von,
        bis=bis,
        q=q,
        zeitraum=zeitraum,
    ).order_by(Termin.beginn).all()
    context: dict[str, Any] = {
        **_listen_context(db, user, termine),
        "jahr": selected_year,
        "probearten": db.query(Probeart).filter(Probeart.aktiv.is_(True)).order_by(Probeart.sortierung).all(),
        "members": db.query(Member).filter(Member.active.is_(True)).order_by(Member.lastname, Member.firstname).all(),
        "statuswerte": list(TerminStatus),
        "filter": {
            "probeart_id": probeart_id,
            "status": status or "",
            "verantwortlich_id": verantwortlich_id,
            "von": von,
            "bis": bis,
            "q": q or "",
            "zeitraum": zeitraum,
        },
        "uebernommen": uebernommen,
    }
    template = (
        "probenplanung/_plan_tabelle.html"
        if request.headers.get("HX-Request") == "true"
        else "probenplanung/plan.html"
    )
    return templates.TemplateResponse(request, template, context)


@router.get("/kalender", response_class=HTMLResponse)
def probenplan_kalender(
    request: Request,
    jahr: int | None = Query(None, ge=1900, le=9998),
    monat: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    heute = now_local(user.org)
    selected_year = jahr or heute.year
    selected_month = monat or heute.month
    first = date(selected_year, selected_month, 1)
    next_month = date(selected_year + (selected_month == 12), 1 if selected_month == 12 else selected_month + 1, 1)
    start = local_date_to_utc(first.isoformat(), org=user.org)
    ende = local_date_to_utc(next_month.isoformat(), org=user.org)
    assert start is not None and ende is not None
    termine = (
        db.query(Termin)
        .options(joinedload(Termin.probeart))
        .filter(Termin.beginn >= start, Termin.beginn < ende, Termin.archiviert_am.is_(None))
        .order_by(Termin.beginn)
        .all()
    )
    tage: dict[date, list[Termin]] = {}
    for termin in termine:
        local = to_org_tz(termin.beginn, user.org)
        assert local is not None
        tage.setdefault(local.date(), []).append(termin)
    return templates.TemplateResponse(request, "probenplanung/kalender.html", {
        "user": user,
        "jahr": selected_year,
        "monat": selected_month,
        "monatsname": calendar.month_name[selected_month],
        "wochen": calendar.Calendar(firstweekday=0).monthdatescalendar(selected_year, selected_month),
        "tage": tage,
        "can_edit": can_edit_proben(user),
    })


def _require_login(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    return user


def _require_edit(user: User) -> None:
    if not can_edit_proben(user):
        raise HTTPException(403, "Keine Berechtigung")


def _termin_or_404(db: Session, org_id: int | None, termin_id: int) -> Termin:
    termin = db.query(Termin).filter(Termin.id == termin_id, Termin.org_id == org_id).first()
    if not termin:
        raise HTTPException(404, "Probe nicht gefunden")
    return termin


def _probeart_or_422(db: Session, org_id: int | None, probeart_id: int) -> Probeart:
    probeart = db.query(Probeart).filter(Probeart.id == probeart_id, Probeart.org_id == org_id).first()
    if not probeart:
        raise HTTPException(422, "Ungültige Probeart")
    return probeart


def _checkliste_or_404(db: Session, org_id: int | None, termin_id: int) -> tuple[Termin, ProbeCheckliste]:
    termin = _termin_or_404(db, org_id, termin_id)
    checkliste = (
        db.query(ProbeCheckliste)
        .filter(ProbeCheckliste.termin_id == termin.id, ProbeCheckliste.org_id == org_id)
        .first()
    )
    if not checkliste:
        raise HTTPException(404, "Keine Checkliste vorhanden")
    return termin, checkliste


def _checklist_item_or_404(
    db: Session, org_id: int | None, checkliste_id: int, item_id: int
) -> ProbeChecklistItem:
    item = (
        db.query(ProbeChecklistItem)
        .filter(
            ProbeChecklistItem.id == item_id,
            ProbeChecklistItem.checkliste_id == checkliste_id,
            ProbeChecklistItem.org_id == org_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(404, "Checklistenpunkt nicht gefunden")
    return item


def _optionen(item: ProbeChecklistItem) -> list[str]:
    if not item.optionen:
        return []
    try:
        result = json.loads(item.optionen)
    except json.JSONDecodeError:
        return []
    return [value for value in result if isinstance(value, str)] if isinstance(result, list) else []


def _checkliste_context(db: Session, user: User, termin: Termin, checkliste: ProbeCheckliste) -> dict:
    sections = (
        db.query(ProbeChecklistSection)
        .filter(ProbeChecklistSection.checkliste_id == checkliste.id)
        .order_by(ProbeChecklistSection.sortierung, ProbeChecklistSection.id)
        .all()
    )
    return {
        "user": user,
        "termin": termin,
        "checkliste": checkliste,
        "sections": sections,
        "members": db.query(Member).filter(Member.active.is_(True)).order_by(Member.lastname, Member.firstname).all(),
        "can_edit": can_edit_proben(user),
        "optionen": _optionen,
        "fortschritt": fortschritt(checkliste, user.org),
    }


def _item_response(
    request: Request,
    db: Session,
    user: User,
    termin: Termin,
    checkliste: ProbeCheckliste,
    item: ProbeChecklistItem,
    *,
    status_code: int = 200,
    konflikt: bool = False,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "probenplanung/_checklist_item.html",
        {
            **_checkliste_context(db, user, termin, checkliste),
            "item": item,
            "konflikt": konflikt,
            "gespeichert": status_code == 200,
        },
        status_code=status_code,
    )
    response.headers["HX-Reswap"] = "outerHTML"
    if status_code == 200:
        response.headers["HX-Trigger"] = "probe-fortschritt"
    return response


def _id_or_none(value: str) -> int | None:
    try:
        return int(value) if value.strip() else None
    except ValueError as exc:
        raise HTTPException(422, "Ungültige Auswahl") from exc


def _form_context(db: Session, user: User, termin: Termin | None = None) -> dict:
    return {
        "user": user,
        "termin": termin,
        "probearten": db.query(Probeart).filter(Probeart.aktiv.is_(True)).order_by(Probeart.sortierung).all(),
        "members": db.query(Member).filter(Member.active.is_(True)).order_by(Member.lastname, Member.firstname).all(),
    }


def _form_anwenden(
    termin: Termin,
    probeart: Probeart,
    *,
    titel: str,
    thema: str,
    beschreibung: str,
    ort: str,
    objekt: str,
    beginn: datetime,
    ende: datetime | None,
    ganztaegig: str,
    info: str,
    interne_bemerkung: str,
    verantwortlich_member_id: str,
    unterstuetzung_member_id: str,
    alarmtext: str,
    besondere_gefahren: str,
    besondere_hinweise: str,
    public_sichtbar: str,
    public_ort_sichtbar: str,
    public_info_sichtbar: str,
) -> None:
    if not titel.strip():
        raise HTTPException(422, "Titel ist erforderlich")
    if ende is not None and ende < beginn:
        raise HTTPException(422, "Das Ende darf nicht vor dem Beginn liegen")
    termin.probeart_id = probeart.id
    termin.typ = probeart.termin_typ
    termin.titel = titel.strip()[:200]
    termin.thema = thema.strip()[:200] or None
    termin.beschreibung = beschreibung.strip() or None
    termin.ort = ort.strip()[:200] or None
    termin.objekt = objekt.strip()[:200] or None
    termin.beginn = beginn
    termin.ende = ende
    termin.ganztaegig = bool(ganztaegig)
    termin.info = info.strip() or None
    termin.interne_bemerkung = interne_bemerkung.strip() or None
    termin.verantwortlich_member_id = _id_or_none(verantwortlich_member_id)
    termin.unterstuetzung_member_id = _id_or_none(unterstuetzung_member_id)
    termin.alarmtext = alarmtext.strip() or None
    termin.besondere_gefahren = besondere_gefahren.strip() or None
    termin.besondere_hinweise = besondere_hinweise.strip() or None
    termin.public_sichtbar = bool(public_sichtbar)
    termin.public_ort_sichtbar = bool(public_ort_sichtbar)
    termin.public_info_sichtbar = bool(public_info_sichtbar)


def _ziel_datum_wochentagsgleich(source: date, zieljahr: int) -> date:
    ordinal = (source.day - 1) // 7 + 1
    erster = date(zieljahr, source.month, 1)
    erster_treffer = 1 + (source.weekday() - erster.weekday()) % 7
    tag = erster_treffer + (ordinal - 1) * 7
    letzter_tag = calendar.monthrange(zieljahr, source.month)[1]
    if tag > letzter_tag:
        tag -= 7
    return date(zieljahr, source.month, tag)


def _termin_kopieren(
    db: Session,
    source: Termin,
    user: User,
    *,
    beginn: datetime,
    ende: datetime | None,
) -> Termin:
    clone = Termin(
        org_id=source.org_id,
        typ=source.typ,
        titel=source.titel,
        thema=source.thema,
        beschreibung=source.beschreibung,
        ort=source.ort,
        objekt=source.objekt,
        objekt_id=source.objekt_id,
        beginn=beginn,
        ende=ende,
        ganztaegig=source.ganztaegig,
        status=TerminStatus.entwurf,
        erstellt_von=user.id,
        probeart_id=source.probeart_id,
        verantwortlich_member_id=source.verantwortlich_member_id,
        unterstuetzung_member_id=source.unterstuetzung_member_id,
    )
    db.add(clone)
    db.flush()
    snapshot_erzeugen(db, clone, user.org)
    return clone


@router.post("/jahr-uebernehmen")
def probenplan_jahr_uebernehmen(
    request: Request,
    quelljahr: int = Form(..., ge=1900, le=9998),
    zieljahr: int = Form(..., ge=1900, le=9998),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    if quelljahr == zieljahr:
        raise HTTPException(422, "Quell- und Zieljahr müssen verschieden sein")
    start, ende = _jahr_grenzen(quelljahr, user.org)
    sources = (
        db.query(Termin)
        .filter(Termin.beginn >= start, Termin.beginn < ende, Termin.archiviert_am.is_(None))
        .order_by(Termin.beginn)
        .all()
    )
    for source in sources:
        source_local = to_org_tz(source.beginn, user.org)
        assert source_local is not None
        ziel_datum = _ziel_datum_wochentagsgleich(source_local.date(), zieljahr)
        ziel_local = source_local.replace(
            year=ziel_datum.year, month=ziel_datum.month, day=ziel_datum.day
        )
        ziel_beginn = ziel_local.astimezone(UTC).replace(tzinfo=None)
        ziel_ende = ziel_beginn + (source.ende - source.beginn) if source.ende else None
        clone = _termin_kopieren(db, source, user, beginn=ziel_beginn, ende=ziel_ende)
        write_probe_change(
            db,
            clone.id,
            "probe.jahresuebernahme",
            "probe",
            None,
            None,
            {"quelle_termin_id": source.id, "quelljahr": quelljahr, "zieljahr": zieljahr},
            user_id=user.id,
            ip=request.client.host if request.client else None,
        )
    db.commit()
    return RedirectResponse(f"/probenplanung?jahr={zieljahr}&uebernommen={len(sources)}", status_code=303)


@router.get("/neu", response_class=HTMLResponse)
def probe_neu(
    request: Request,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    return templates.TemplateResponse(request, "probenplanung/probe_formular.html", _form_context(db, user))


@router.post("/neu")
def probe_anlegen(
    request: Request,
    probeart_id: int = Form(...),
    titel: str = Form(...),
    thema: str = Form(""),
    beschreibung: str = Form(""),
    ort: str = Form(""),
    objekt: str = Form(""),
    beginn: str = Form(...),
    ende: str = Form(""),
    ganztaegig: str = Form(""),
    info: str = Form(""),
    interne_bemerkung: str = Form(""),
    verantwortlich_member_id: str = Form(""),
    unterstuetzung_member_id: str = Form(""),
    alarmtext: str = Form(""),
    besondere_gefahren: str = Form(""),
    besondere_hinweise: str = Form(""),
    public_sichtbar: str = Form(""),
    public_ort_sichtbar: str = Form(""),
    public_info_sichtbar: str = Form(""),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    probeart = _probeart_or_422(db, user.org_id, probeart_id)
    beginn_dt = local_input_to_utc(beginn, user.org)
    ende_dt = local_input_to_utc(ende, user.org) if ende else None
    if beginn_dt is None or (ende and ende_dt is None):
        raise HTTPException(422, "Ungültiges Datum")
    termin = Termin(
        org_id=user.org_id,
        typ=probeart.termin_typ,
        titel="",
        beginn=beginn_dt,
        status=TerminStatus.entwurf,
        erstellt_von=user.id,
    )
    _form_anwenden(
        termin,
        probeart,
        titel=titel,
        thema=thema,
        beschreibung=beschreibung,
        ort=ort,
        objekt=objekt,
        beginn=beginn_dt,
        ende=ende_dt,
        ganztaegig=ganztaegig,
        info=info,
        interne_bemerkung=interne_bemerkung,
        verantwortlich_member_id=verantwortlich_member_id,
        unterstuetzung_member_id=unterstuetzung_member_id,
        alarmtext=alarmtext,
        besondere_gefahren=besondere_gefahren,
        besondere_hinweise=besondere_hinweise,
        public_sichtbar=public_sichtbar,
        public_ort_sichtbar=public_ort_sichtbar,
        public_info_sichtbar=public_info_sichtbar,
    )
    db.add(termin)
    db.flush()
    snapshot_erzeugen(db, termin, user.org)
    write_probe_change(
        db,
        termin.id,
        "probe.angelegt",
        "probe",
        None,
        None,
        {"titel": termin.titel},
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(f"/probenplanung/{termin.id}", status_code=303)


@router.get("/{termin_id}", response_class=HTMLResponse)
def probe_detail(
    request: Request,
    termin_id: int,
    tab: str = "uebersicht",
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    erlaubte_tabs = {
        "uebersicht", "vorbereitung", "historie", "skizze", "dokumente",
        "uebungseinsatz", "teilnehmer", "nachbereitung",
    }
    if tab not in erlaubte_tabs:
        raise HTTPException(404, "Unbekannter Tab")
    termin = _termin_or_404(db, user.org_id, termin_id)
    checkliste = db.query(ProbeCheckliste).filter(ProbeCheckliste.termin_id == termin.id).first()
    termin_lokal = to_org_tz(termin.beginn, user.org)
    assert termin_lokal is not None
    verantwortliche_ids = {
        member_id
        for member_id in (termin.verantwortlich_member_id, termin.unterstuetzung_member_id)
        if member_id is not None
    }
    verantwortliche = (
        db.query(Member).filter(Member.id.in_(verantwortliche_ids)).all() if verantwortliche_ids else []
    )
    heute = now_local(user.org).date()
    items = list(checkliste.items) if checkliste else []
    erledigt = [item for item in items if item.zustand in {"erledigt", "nicht_relevant"}]
    demnaechst = [
        item
        for item in items
        if item.zustand == "offen" and item.faellig_am is not None and item.faellig_am <= heute + timedelta(days=7)
    ]
    noch_offen = [item for item in items if item.zustand == "offen" and item not in demnaechst]
    context: dict[str, Any] = {
        "user": user,
        "termin": termin,
        "checkliste": checkliste,
        "fortschritt": fortschritt(checkliste, user.org) if checkliste else None,
        "can_edit": can_edit_proben(user),
        "is_admin": is_proben_admin(user),
        "statuswechsel": sorted(ERLAUBTE_STATUSWECHSEL.get(termin.status, frozenset())),
        "active_tab": tab,
        "noch_offen": noch_offen,
        "demnaechst": demnaechst,
        "erledigt": erledigt,
        "historie": (
            db.query(ProbeChange).filter(ProbeChange.termin_id == termin.id).order_by(ProbeChange.ts.desc()).all()
            if tab == "historie"
            else []
        ),
        "wochentag": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][
            termin_lokal.weekday()
        ],
        "verantwortliche": {member.id: member.full_name for member in verantwortliche},
    }
    if tab in {"skizze", "dokumente"}:
        medien = (
            db.query(ProbeMedia)
            .filter(ProbeMedia.termin_id == termin.id, ProbeMedia.org_id == user.org_id)
            .order_by(ProbeMedia.hochgeladen_am.desc(), ProbeMedia.id.desc())
            .all()
        )
        uploader_ids = {medium.hochgeladen_von for medium in medien if medium.hochgeladen_von is not None}
        uploaders = db.query(User).filter(User.id.in_(uploader_ids)).all() if uploader_ids else []
        context.update({
            "skizzen": [medium for medium in medien if medium.kind == "image"],
            "dokumente": [medium for medium in medien if medium.art == "dokument"],
            "uploader_namen": {uploader.id: uploader.display_name for uploader in uploaders},
        })
    if checkliste:
        context.update(_checkliste_context(db, user, termin, checkliste))
    return templates.TemplateResponse(
        request,
        "probenplanung/probe_detail.html",
        context,
    )


@router.post("/{termin_id}/medien")
async def probe_medium_hochladen(
    request: Request,
    termin_id: int,
    datei: UploadFile = File(...),
    art: str = Form("dokument"),
    name: str = Form(""),
    typ: str | None = Form(None),
    beschreibung: str | None = Form(None),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin = _termin_or_404(db, user.org_id, termin_id)
    if user.org_id is None:
        raise HTTPException(403, "Keine Organisation zugeordnet")
    from app.services.probe_media_service import upload_probe_media

    await upload_probe_media(
        datei,
        termin_id=termin.id,
        org_id=user.org_id,
        user_id=user.id,
        art=art,
        name=name,
        typ=typ,
        beschreibung=beschreibung,
        db=db,
    )
    db.commit()
    tab = "skizze" if art in {"skizze", "bild"} else "dokumente"
    return RedirectResponse(f"/probenplanung/{termin.id}?tab={tab}", status_code=303)


@router.delete("/{termin_id}/medien/{media_id}")
def probe_medium_loeschen(
    request: Request,
    termin_id: int,
    media_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    _termin_or_404(db, user.org_id, termin_id)
    media = (
        db.query(ProbeMedia)
        .filter(
            ProbeMedia.id == media_id,
            ProbeMedia.termin_id == termin_id,
            ProbeMedia.org_id == user.org_id,
        )
        .first()
    )
    if media is None:
        raise HTTPException(404, "Medium nicht gefunden")
    from app.services.probe_media_service import delete_probe_media

    tab = "skizze" if media.art in {"skizze", "bild"} else "dokumente"
    delete_probe_media(db, media)
    db.commit()
    return RedirectResponse(f"/probenplanung/{termin_id}?tab={tab}", status_code=303)


def _probe_medium_fuer_org(db: Session, media_id: int, org_id: int | None) -> ProbeMedia | None:
    return db.query(ProbeMedia).filter(ProbeMedia.id == media_id, ProbeMedia.org_id == org_id).first()


@router.get("/medien/{media_id}")
def probe_medium_ausliefern(
    request: Request,
    media_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    media = _probe_medium_fuer_org(db, media_id, user.org_id)
    if media is None:
        return Response(status_code=404)
    from app.services.probe_media_service import probe_media_path

    path = probe_media_path(media)
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(
        path,
        media_type=media.mime_type,
        filename=media.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/medien/{media_id}/thumb")
def probe_medium_thumbnail(
    request: Request,
    media_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    media = _probe_medium_fuer_org(db, media_id, user.org_id)
    if media is None:
        return Response(status_code=404)
    from app.services.probe_media_service import probe_media_path, probe_thumb_path

    path = probe_thumb_path(media)
    if path is None or not path.exists():
        path = probe_media_path(media) if media.kind == "image" else None
    if path is None or not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/{termin_id}/skizze")
def probe_skizze_bearbeiten(
    request: Request,
    termin_id: int,
    media_id: int | None = Query(None),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    _termin_or_404(db, user.org_id, termin_id)
    query = db.query(ProbeMedia).filter(
        ProbeMedia.termin_id == termin_id,
        ProbeMedia.org_id == user.org_id,
        ProbeMedia.kind == "image",
    )
    media = (
        query.filter(ProbeMedia.id == media_id).first()
        if media_id
        else query.order_by(ProbeMedia.id.desc()).first()
    )
    if media is None:
        raise HTTPException(404, "Kein Skizzenbild vorhanden")
    return RedirectResponse(f"/annotieren/probe/{media.id}", status_code=303)


from app.services.probe_checklist_service import ERLAUBTE_STATUSWECHSEL  # noqa: E402


@router.get("/{termin_id}/bearbeiten", response_class=HTMLResponse)
def probe_bearbeiten(
    request: Request,
    termin_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    return templates.TemplateResponse(
        request,
        "probenplanung/probe_formular.html",
        _form_context(db, user, _termin_or_404(db, user.org_id, termin_id)),
    )


@router.post("/{termin_id}/bearbeiten")
def probe_speichern(
    request: Request,
    termin_id: int,
    probeart_id: int = Form(...),
    titel: str = Form(...),
    thema: str = Form(""),
    beschreibung: str = Form(""),
    ort: str = Form(""),
    objekt: str = Form(""),
    beginn: str = Form(...),
    ende: str = Form(""),
    ganztaegig: str = Form(""),
    info: str = Form(""),
    interne_bemerkung: str = Form(""),
    verantwortlich_member_id: str = Form(""),
    unterstuetzung_member_id: str = Form(""),
    alarmtext: str = Form(""),
    besondere_gefahren: str = Form(""),
    besondere_hinweise: str = Form(""),
    public_sichtbar: str = Form(""),
    public_ort_sichtbar: str = Form(""),
    public_info_sichtbar: str = Form(""),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin = _termin_or_404(db, user.org_id, termin_id)
    probeart = _probeart_or_422(db, user.org_id, probeart_id)
    beginn_dt = local_input_to_utc(beginn, user.org)
    ende_dt = local_input_to_utc(ende, user.org) if ende else None
    if beginn_dt is None or (ende and ende_dt is None):
        raise HTTPException(422, "Ungültiges Datum")
    before = {"titel": termin.titel, "beginn": termin.beginn}
    _form_anwenden(
        termin,
        probeart,
        titel=titel,
        thema=thema,
        beschreibung=beschreibung,
        ort=ort,
        objekt=objekt,
        beginn=beginn_dt,
        ende=ende_dt,
        ganztaegig=ganztaegig,
        info=info,
        interne_bemerkung=interne_bemerkung,
        verantwortlich_member_id=verantwortlich_member_id,
        unterstuetzung_member_id=unterstuetzung_member_id,
        alarmtext=alarmtext,
        besondere_gefahren=besondere_gefahren,
        besondere_hinweise=besondere_hinweise,
        public_sichtbar=public_sichtbar,
        public_ort_sichtbar=public_ort_sichtbar,
        public_info_sichtbar=public_info_sichtbar,
    )
    termin.ics_sequence = (termin.ics_sequence or 0) + 1
    write_probe_change(
        db,
        termin.id,
        "probe.bearbeitet",
        "probe",
        None,
        before,
        {"titel": termin.titel, "beginn": termin.beginn},
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(f"/probenplanung/{termin.id}", status_code=303)


@router.post("/{termin_id}/status")
def probe_status(
    request: Request,
    termin_id: int,
    neuer_status: str = Form(...),
    grund: str = Form(""),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin = _termin_or_404(db, user.org_id, termin_id)
    if neuer_status == TerminStatus.vorbereitung_abgeschlossen and grund.strip():
        try:
            uebersteuern(db, termin, user, grund, ip=request.client.host if request.client else None)
        except ValueError as exc:
            raise HTTPException(403, str(exc)) from exc
    try:
        statuswechsel(db, termin, neuer_status, user, ip=request.client.host if request.client else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return RedirectResponse(f"/probenplanung/{termin.id}", status_code=303)


@router.post("/{termin_id}/duplizieren")
def probe_duplizieren(
    request: Request,
    termin_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    source = _termin_or_404(db, user.org_id, termin_id)
    clone = _termin_kopieren(
        db,
        source,
        user,
        beginn=source.beginn + timedelta(days=7),
        ende=source.ende + timedelta(days=7) if source.ende else None,
    )
    write_probe_change(
        db,
        clone.id,
        "probe.dupliziert",
        "probe",
        None,
        None,
        {"quelle_termin_id": source.id},
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(f"/probenplanung/{clone.id}", status_code=303)


@router.post("/{termin_id}/archivieren")
def probe_archivieren(
    request: Request,
    termin_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin = _termin_or_404(db, user.org_id, termin_id)
    vorher = termin.archiviert_am
    termin.archiviert_am = datetime.now(UTC).replace(tzinfo=None)
    write_probe_change(
        db,
        termin.id,
        "probe.archiviert",
        "probe",
        "archiviert_am",
        {"archiviert_am": vorher},
        {"archiviert_am": termin.archiviert_am},
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(f"/probenplanung/{termin.id}", status_code=303)


@router.get("/{termin_id}/checkliste", response_class=HTMLResponse)
def probe_checkliste(
    request: Request,
    termin_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    return templates.TemplateResponse(
        request,
        "probenplanung/_checkliste.html",
        _checkliste_context(db, user, termin, checkliste),
    )


@router.get("/{termin_id}/checkliste/fortschritt", response_class=HTMLResponse)
def probe_checkliste_fortschritt(
    request: Request,
    termin_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    return templates.TemplateResponse(
        request,
        "probenplanung/_fortschritt.html",
        {"user": user, "termin": termin, "fortschritt": fortschritt(checkliste, user.org)},
    )


def _patch_values(
    db: Session, user: User, item: ProbeChecklistItem, feld: str, wert: str
) -> dict[str, object]:
    now = datetime.now(UTC).replace(tzinfo=None)
    values: dict[str, object] = {"aktualisiert_von": user.id, "aktualisiert_am": now}
    if feld == "zustand":
        if wert not in {"offen", "erledigt"}:
            raise HTTPException(422, "Ungültiger Zustand")
        values["zustand"] = wert
        values["begruendung"] = None
        values["erledigt_von"] = user.id if wert == "erledigt" else None
        values["erledigt_am"] = now if wert == "erledigt" else None
    elif feld == "wert_text":
        text_value = wert.strip()
        if item.typ == ChecklistItemTyp.datum and text_value:
            try:
                date.fromisoformat(text_value)
            except ValueError as exc:
                raise HTTPException(422, "Ungültiges Datum") from exc
        if item.typ == ChecklistItemTyp.auswahl and text_value and text_value not in _optionen(item):
            raise HTTPException(422, "Ungültige Auswahl")
        if item.typ == ChecklistItemTyp.mehrfachauswahl:
            try:
                selected = json.loads(text_value)
            except json.JSONDecodeError as exc:
                raise HTTPException(422, "Ungültige Mehrfachauswahl") from exc
            if not isinstance(selected, list) or any(value not in _optionen(item) for value in selected):
                raise HTTPException(422, "Ungültige Mehrfachauswahl")
            text_value = json.dumps(selected, ensure_ascii=False)
        values["wert_text"] = text_value or None
    elif feld in {"wert_member_id", "verantwortlich_member_id"}:
        member_id = _id_or_none(wert)
        if member_id is not None and not (
            db.query(Member)
            .filter(Member.id == member_id, Member.org_id == user.org_id, Member.active.is_(True))
            .first()
        ):
            raise HTTPException(422, "Ungültige Person")
        values[feld] = member_id
    elif feld == "faellig_am":
        try:
            values["faellig_am"] = date.fromisoformat(wert) if wert else None
        except ValueError as exc:
            raise HTTPException(422, "Ungültiges Fälligkeitsdatum") from exc
    else:
        raise HTTPException(422, "Unbekanntes Feld")
    return values


@router.patch("/{termin_id}/checkliste/punkt/{item_id}", response_class=HTMLResponse)
def probe_checkliste_punkt_speichern(
    request: Request,
    termin_id: int,
    item_id: int,
    feld: str = Form(...),
    wert: str = Form(""),
    version: int = Form(...),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    item = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
    values = _patch_values(db, user, item, feld, wert)
    values["version"] = ProbeChecklistItem.version + 1
    result = db.execute(
        update(ProbeChecklistItem)
        .where(
            ProbeChecklistItem.id == item.id,
            ProbeChecklistItem.checkliste_id == checkliste.id,
            ProbeChecklistItem.org_id == user.org_id,
            ProbeChecklistItem.version == version,
        )
        .values(**values)
    )
    if getattr(result, "rowcount", 0) != 1:
        db.rollback()
        current = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
        return _item_response(request, db, user, termin, checkliste, current, status_code=409, konflikt=True)
    db.commit()
    current = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
    return _item_response(request, db, user, termin, checkliste, current)


@router.post("/{termin_id}/checkliste/punkt", response_class=HTMLResponse)
def probe_checkliste_punkt_anlegen(
    request: Request,
    termin_id: int,
    titel: str = Form(...),
    typ: str = Form(ChecklistItemTyp.checkbox.value),
    pflicht: str = Form(""),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    if not titel.strip():
        raise HTTPException(422, "Titel ist erforderlich")
    if typ not in {item_type.value for item_type in ChecklistItemTyp}:
        raise HTTPException(422, "Ungültiger Punkt-Typ")
    max_sort = max((item.sortierung for item in checkliste.items), default=0)
    item = ProbeChecklistItem(
        org_id=user.org_id,
        checkliste_id=checkliste.id,
        quelle="individuell",
        titel=titel.strip()[:200],
        typ=typ,
        pflicht=bool(pflicht),
        sortierung=max_sort + 10,
        aktualisiert_von=user.id,
        aktualisiert_am=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    response = _item_response(request, db, user, termin, checkliste, item)
    response.headers["HX-Reswap"] = "beforeend"
    return response


@router.delete("/{termin_id}/checkliste/punkt/{item_id}", response_class=HTMLResponse)
def probe_checkliste_punkt_loeschen(
    request: Request,
    termin_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    _termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    item = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
    if item.quelle != "individuell":
        raise HTTPException(409, "Vorlagenpunkte können nicht gelöscht werden")
    db.delete(item)
    db.commit()
    return HTMLResponse("", headers={"HX-Trigger": "probe-fortschritt"})


@router.post("/{termin_id}/checkliste/punkt/{item_id}/nicht-relevant", response_class=HTMLResponse)
def probe_checkliste_punkt_nicht_relevant(
    request: Request,
    termin_id: int,
    item_id: int,
    begruendung: str = Form(...),
    version: int = Form(...),
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    _require_edit(user)
    termin, checkliste = _checkliste_or_404(db, user.org_id, termin_id)
    item = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
    grund = begruendung.strip()
    if not grund:
        raise HTTPException(422, "Eine Begründung ist erforderlich")
    now = datetime.now(UTC).replace(tzinfo=None)
    result = db.execute(
        update(ProbeChecklistItem)
        .where(
            ProbeChecklistItem.id == item.id,
            ProbeChecklistItem.checkliste_id == checkliste.id,
            ProbeChecklistItem.org_id == user.org_id,
            ProbeChecklistItem.version == version,
        )
        .values(
            zustand="nicht_relevant",
            begruendung=grund,
            erledigt_von=None,
            erledigt_am=None,
            aktualisiert_von=user.id,
            aktualisiert_am=now,
            version=ProbeChecklistItem.version + 1,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        db.rollback()
        current = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
        return _item_response(request, db, user, termin, checkliste, current, status_code=409, konflikt=True)
    db.commit()
    current = _checklist_item_or_404(db, user.org_id, checkliste.id, item_id)
    return _item_response(request, db, user, termin, checkliste, current)
