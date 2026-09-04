"""Probe-CRUD und Vorbereitung im Probenplanungsmodul."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.dependencies import CurrentOrgId
from app.core.permissions import can_edit_proben, is_proben_admin
from app.core.templating import templates
from app.core.timezones import local_input_to_utc
from app.db import get_db
from app.models.master import Member
from app.models.probenplanung import Probeart, ProbeCheckliste, TerminStatus
from app.models.teilnahme import Termin
from app.models.user import User
from app.routers.ui_probenplanung_admin import require_probenplanung_enabled
from app.services.probe_checklist_service import fortschritt, snapshot_erzeugen, statuswechsel, uebersteuern
from app.services.probe_history import write_probe_change

router = APIRouter(prefix="/probenplanung", tags=["probenplanung"])


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
    db: Session = Depends(get_db),
    _guard: None = Depends(require_probenplanung_enabled),
    _: CurrentOrgId = None,
):
    user = _require_login(request)
    termin = _termin_or_404(db, user.org_id, termin_id)
    checkliste = db.query(ProbeCheckliste).filter(ProbeCheckliste.termin_id == termin.id).first()
    return templates.TemplateResponse(
        request,
        "probenplanung/probe_detail.html",
        {
            "user": user,
            "termin": termin,
            "checkliste": checkliste,
            "fortschritt": fortschritt(checkliste, user.org) if checkliste else None,
            "can_edit": can_edit_proben(user),
            "is_admin": is_proben_admin(user),
            "statuswechsel": sorted(ERLAUBTE_STATUSWECHSEL.get(termin.status, frozenset())),
        },
    )


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
    clone = Termin(
        org_id=source.org_id,
        typ=source.typ,
        titel=source.titel,
        thema=source.thema,
        beschreibung=source.beschreibung,
        ort=source.ort,
        objekt=source.objekt,
        objekt_id=source.objekt_id,
        beginn=source.beginn + timedelta(days=7),
        ende=source.ende + timedelta(days=7) if source.ende else None,
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
