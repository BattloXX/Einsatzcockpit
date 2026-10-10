"""Detailkarte einer Lage-Einheit."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.security import get_author_name
from app.core.templating import templates
from app.db import get_db
from app.models.atemschutz_pruefung import AtemschutzGeraet
from app.models.major_incident import (
    AUSSTATTUNG_STATUS,
    EINHEIT_STATUS_COLOR,
    EINHEIT_STATUS_LABEL,
    GSL_AUSSTATTUNG_KATALOG,
    PERSON_FUNKTIONEN,
    SITE_PRIORITY_COLOR,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
)
from app.models.master import Member
from app.models.verleih import VerleihArtikel
from app.routers.ui_major_incident import _can_edit, _check_org_access, _get_mi_features, _lage_or_404, _nav_counts
from app.services import gk_zugang_service, resource_service, ressource_karte_service, ressource_pflege_service
from app.services.broadcast import broadcast_lage

router = APIRouter(prefix="/lage/{lage_id}/einheiten/{einheit_id}")
_READ = ("incident_leader", "admin", "org_admin", "recorder", "readonly")
_WRITE = ("incident_leader", "admin", "org_admin", "recorder")


def _context(request: Request, lage_id: int, einheit_id: int, db: Session):
    lage = _lage_or_404(lage_id, db)
    try:
        _check_org_access(request.state.user, lage)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(404, "Lage nicht gefunden") from exc
        raise
    einheit = db.get(LageEinheit, einheit_id)
    if not einheit or einheit.lage_id != lage.id:
        raise HTTPException(404, "Einheit nicht gefunden")
    return lage, einheit


def _zugang_erlaubt(request: Request, user) -> bool:
    return not (
        not _can_edit(user)
        or getattr(request.state, "is_device", False)
        or getattr(request.state, "qr_lage_id", None) is not None
        or getattr(request.state, "qr_incident_id", None) is not None
        or getattr(user, "gsl_nur_lesen", False)
    )


def _darf_zugang_verwalten(request: Request, user) -> None:
    if not _zugang_erlaubt(request, user):
        raise HTTPException(403, "Zugangsverwaltung nicht erlaubt")


def _gk_name(db: Session, einheit) -> str | None:
    leader = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    return leader.display_name if leader else None


def _zugang(request: Request, lage, einheit, db: Session, ergebnis=None):
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_zugang.html",
        {
            "lage": lage,
            "einheit": einheit,
            "zugang": gk_zugang_service.zugang_status(db, einheit),
            "karte_gk_name": _gk_name(db, einheit),
            "ergebnis": ergebnis,
        },
    )


def _overview(request: Request, lage, einheit, db: Session):
    members = (
        db.query(Member)
        .filter(Member.org_id == lage.org_id, Member.active.is_(True))
        .order_by(  # noqa: E712
            Member.ist_gruppenkommandant.desc(), Member.lastname, Member.firstname
        )
        .all()
    )
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_uebersicht.html",
        {
            "lage": lage,
            "einheit": einheit,
            "karte": ressource_karte_service.karte(db, lage, einheit),
            "can_edit": _can_edit(request.state.user),
            "members": members,
            "ausstattung_warnungen": ressource_pflege_service.ausstattung_warnungen(db, einheit),
        },
    )


def _einsaetze(request: Request, lage, einheit, db: Session):
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_einsaetze.html",
        {
            "lage": lage,
            "einheit": einheit,
            "einsaetze": ressource_karte_service.einsaetze(db, lage, einheit),
            "prio_color": SITE_PRIORITY_COLOR,
            "einheit_status_label": EINHEIT_STATUS_LABEL,
            "einheit_status_color": EINHEIT_STATUS_COLOR,
        },
    )


def _andere_einheiten(db: Session, lage, einheit) -> list[LageEinheit]:
    return (
        db.query(LageEinheit)
        .filter(LageEinheit.lage_id == lage.id, LageEinheit.id != einheit.id, LageEinheit.status != "abgerueckt")
        .order_by(LageEinheit.label, LageEinheit.id)
        .all()
    )


def _personal(request: Request, lage, einheit, db: Session):
    from app.models.major_incident import LageEinheitPerson

    personen = (
        db.query(LageEinheitPerson)
        .filter(
            LageEinheitPerson.lage_id == lage.id,
            LageEinheitPerson.einheit_id == einheit.id,
            LageEinheitPerson.bis_at.is_(None),
        )
        .order_by(LageEinheitPerson.von_at, LageEinheitPerson.id)
        .all()
    )
    members = (
        db.query(Member)
        .filter(Member.org_id == lage.org_id, Member.active.is_(True))
        .order_by(Member.lastname, Member.firstname)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_personal.html",
        {
            "lage": lage,
            "einheit": einheit,
            "personen": personen,
            "members": members,
            "andere_einheiten": _andere_einheiten(db, lage, einheit),
            "can_edit": _can_edit(request.state.user),
            "person_funktionen": PERSON_FUNKTIONEN,
        },
    )


def _ausstattung(request: Request, lage, einheit, db: Session):
    atemschutz = (
        db.query(AtemschutzGeraet)
        .filter(AtemschutzGeraet.org_id == lage.org_id, AtemschutzGeraet.aktiv.is_(True))
        .all()
    )
    verleih = (
        db.query(VerleihArtikel).filter(VerleihArtikel.org_id == lage.org_id, VerleihArtikel.aktiv.is_(True)).all()
    )
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_ausstattung.html",
        {
            "lage": lage,
            "einheit": einheit,
            "zeilen": ressource_pflege_service.ausstattung_liste(db, einheit),
            "warnungen": ressource_pflege_service.ausstattung_warnungen(db, einheit),
            "andere_einheiten": _andere_einheiten(db, lage, einheit),
            "can_edit": _can_edit(request.state.user),
            "katalog": GSL_AUSSTATTUNG_KATALOG,
            "statuswerte": AUSSTATTUNG_STATUS,
            "atemschutz": atemschutz,
            "verleih": verleih,
        },
    )


_JOURNAL_FILTER = {
    "status": {"status", "angelegt", "abgerueckt"},
    "einsaetze": {"disponiert", "uebernommen", "begonnen", "abgeschlossen", "zurueckgezogen"},
    "fuehrung": {"gk_zugewiesen", "gk_gewechselt", "gk_telefon"},
    "meldungen": {"lagemeldung", "massnahmen", "meldung", "medien"},
    "manuell": {"manuell"},
}


def _journal_typen(typen: str | None) -> tuple[set[str] | None, str]:
    keys = {wert.strip().lower() for wert in (typen or "").split(",") if wert.strip()}
    if not keys or "alle" in keys:
        return None, "alle"
    result: set[str] = set()
    for key in keys:
        result.update(_JOURNAL_FILTER.get(key, set()))
    return result, ",".join(sorted(keys))


def _journal_vor_ts(vor_ts: str | None) -> datetime | None:
    if not vor_ts:
        return None
    try:
        return datetime.fromisoformat(vor_ts.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "Ungültiger Zeitstempel") from exc


def _journal(
    request: Request,
    lage,
    einheit,
    db: Session,
    *,
    typen: str | None,
    site_id: int | None,
    vor_ts: str | None,
    limit: int,
):
    if site_id is not None:
        site = db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id:
            raise HTTPException(404, "Einsatzstelle nicht gefunden")
    parsed_typen, active_filter = _journal_typen(typen)
    parsed_vor_ts = _journal_vor_ts(vor_ts)
    limit = max(1, min(limit, 200))
    rows = ressource_karte_service.journal(
        db, lage, einheit, typen=parsed_typen, site_id=site_id, vor_ts=parsed_vor_ts, limit=limit + 1
    )
    visible, has_more = rows[:limit], len(rows) > limit
    sites = (
        db.query(IncidentSite)
        .filter(IncidentSite.major_incident_id == lage.id)
        .order_by(IncidentSite.bezeichnung)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte_journal.html",
        {
            "lage": lage,
            "einheit": einheit,
            "entries": visible,
            "sites": sites,
            "can_edit": _can_edit(request.state.user),
            "typen": active_filter,
            "site_id": site_id,
            "limit": limit,
            "has_more": has_more,
            "next_vor_ts": (visible[-1].ts.isoformat() + "Z") if visible and has_more else None,
        },
    )


@router.get("/karte", response_class=HTMLResponse)
def karte(
    request: Request, lage_id: int, einheit_id: int, db: Session = Depends(get_db), _=Depends(require_role(*_READ))
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return templates.TemplateResponse(
        request,
        "incident_major/_ressource_karte.html",
        {
            "lage": lage,
            "einheit": einheit,
            "karte": ressource_karte_service.karte(db, lage, einheit),
            "can_edit": _can_edit(request.state.user),
            "can_zugang": _zugang_erlaubt(request, request.state.user),
            "mi_features": _get_mi_features(db, lage.org_id),
            **_nav_counts(lage_id, lage, db),
        },
    )


@router.get("/karte/{tab}", response_class=HTMLResponse)
def karte_tab(
    request: Request,
    lage_id: int,
    einheit_id: int,
    tab: str,
    typen: str | None = None,
    site_id: int | None = None,
    vor_ts: str | None = None,
    limit: int = Query(50),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_READ)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    if tab == "uebersicht":
        return _overview(request, lage, einheit, db)
    if tab == "einsaetze":
        return _einsaetze(request, lage, einheit, db)
    if tab == "journal":
        return _journal(request, lage, einheit, db, typen=typen, site_id=site_id, vor_ts=vor_ts, limit=limit)
    if tab == "personal":
        return _personal(request, lage, einheit, db)
    if tab == "ausstattung":
        return _ausstattung(request, lage, einheit, db)
    if tab == "zugang":
        _darf_zugang_verwalten(request, request.state.user)
        return _zugang(request, lage, einheit, db)
    raise HTTPException(404, "Unbekannter Tab")


@router.post("/zugang/link")
def zugang_link(
    request: Request,
    lage_id: int,
    einheit_id: int,
    modus: str = Form(...),
    bestehender_link: str | None = Form(None),
    bestaetigt: bool = Form(False),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    _darf_zugang_verwalten(request, request.state.user)
    try:
        result = gk_zugang_service.kopie_ausstellen(
            db,
            lage,
            einheit,
            user_id=request.state.user.id,
            modus=modus,
            bestehender_link=bestehender_link,
            bestaetigt=bestaetigt,
        )
    except gk_zugang_service.SitzungAktiv as exc:
        return JSONResponse(
            {"code": "sitzung_aktiv", "zuletzt_aktiv": exc.zuletzt_aktiv.isoformat() + "Z"}, status_code=409
        )
    response = JSONResponse(
        {
            "link": result.link,
            "text": result.text,
            "laeuft_ab_at": result.laeuft_ab_at.isoformat() + "Z",
            "generation": result.generation,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/zugang/senden", response_class=HTMLResponse)
async def zugang_senden(
    request: Request,
    lage_id: int,
    einheit_id: int,
    bestehender_link: str | None = Form(None),
    bestaetigt: bool = Form(False),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    _darf_zugang_verwalten(request, request.state.user)
    try:
        result = await gk_zugang_service.sende_zugangs_sms(
            db,
            lage,
            einheit,
            user_id=request.state.user.id,
            ausloeser="manuell",
            bestehender_link=bestehender_link,
            bestaetigt=bestaetigt,
        )
    except gk_zugang_service.SitzungAktiv as exc:
        return JSONResponse(
            {"code": "sitzung_aktiv", "zuletzt_aktiv": exc.zuletzt_aktiv.isoformat() + "Z"}, status_code=409
        )
    except ValueError as exc:
        return HTMLResponse(str(exc), status_code=422)
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    response = _zugang(request, lage, einheit, db, result)
    response.headers["HX-Retarget"] = "#ressourceKarteBody"
    return response


@router.post("/zugang/widerrufen", response_class=HTMLResponse)
async def zugang_widerrufen(
    request: Request, lage_id: int, einheit_id: int, db: Session = Depends(get_db), _=Depends(require_role(*_WRITE))
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    _darf_zugang_verwalten(request, request.state.user)
    gk_zugang_service.widerrufe(db, einheit.id, grund="manuell", user_id=request.state.user.id)
    db.commit()
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    return _zugang(request, lage, einheit, db)


@router.post("/zugang/verlaengern", response_class=HTMLResponse)
async def zugang_verlaengern(
    request: Request, lage_id: int, einheit_id: int, db: Session = Depends(get_db), _=Depends(require_role(*_WRITE))
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    _darf_zugang_verwalten(request, request.state.user)
    gk_zugang_service.verlaengere(db, einheit.id, request.state.user.id)
    db.commit()
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    return _zugang(request, lage, einheit, db)


@router.post("/zugang/sitzung/{session_id}/beenden", response_class=HTMLResponse)
async def zugang_sitzung_beenden(
    request: Request,
    lage_id: int,
    einheit_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    _darf_zugang_verwalten(request, request.state.user)
    session = db.get(gk_zugang_service.LageEinheitZugangSession, session_id)
    zugang = (
        db.query(gk_zugang_service.LageEinheitZugang)
        .filter(gk_zugang_service.LageEinheitZugang.einheit_id == einheit.id)
        .first()
    )
    if not session or not zugang or session.zugang_id != zugang.id or session.org_id != lage.org_id:
        raise HTTPException(404, "Sitzung nicht gefunden")
    session.revoked_at, session.revoke_grund = datetime.utcnow(), "manuell"
    write_audit(
        db,
        "gsl.zugang.sitzung_beendet",
        org_id=lage.org_id,
        user_id=request.state.user.id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"session_id": session.id},
    )
    db.commit()
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    return _zugang(request, lage, einheit, db)


def _error(text: str) -> HTMLResponse:
    return HTMLResponse(text, status_code=422, headers={"HX-Retarget": "#gkFehler", "HX-Reswap": "innerHTML"})


def _journal_error(text: str) -> HTMLResponse:
    return HTMLResponse(text, status_code=422, headers={"HX-Retarget": "#journalFehler", "HX-Reswap": "innerHTML"})


def _pflege_error(text: str, bereich: str) -> HTMLResponse:
    return HTMLResponse(
        text,
        status_code=422,
        headers={"HX-Retarget": f"#{bereich}Fehler", "HX-Reswap": "innerHTML", "data-no-error-toast": "true"},
    )


async def _pflege_save(
    request: Request, lage, einheit, db: Session, bereich: str, action: str, callback, betroffen=None
):
    if not _can_edit(request.state.user):
        raise HTTPException(403, "Keine Bearbeitungsberechtigung")
    try:
        callback()
    except ValueError as exc:
        return _pflege_error(str(exc), bereich)
    einheiten = betroffen or [einheit]
    write_audit(
        db,
        action,
        org_id=lage.org_id,
        user_id=request.state.user.id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_ids": [item.id for item in einheiten]},
    )
    db.commit()
    for item in einheiten:
        await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": item.id})
    response = (
        _personal(request, lage, einheit, db) if bereich == "personal" else _ausstattung(request, lage, einheit, db)
    )
    response.headers["HX-Retarget"] = "#ressourceKarteBody"
    response.headers["HX-Reswap"] = "innerHTML"
    response.headers["HX-Trigger"] = "ressourceChanged"
    return response


def _ziel_einheit_or_404(db: Session, lage, einheit_id: int) -> LageEinheit:
    ziel = db.get(LageEinheit, einheit_id)
    if ziel is None or ziel.lage_id != lage.id:
        raise HTTPException(404, "Zieleinheit nicht gefunden")
    return ziel


@router.post("/personal/setzen", response_class=HTMLResponse)
async def personal_setzen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    gesamt: int = Form(...),
    fuehrung: int = Form(0),
    agt: int = Form(0),
    sanitaeter: int = Form(0),
    bemerkung: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.personal_setzen",
        lambda: ressource_pflege_service.personal_setzen(
            db,
            lage,
            einheit,
            gesamt=gesamt,
            fuehrung=fuehrung,
            agt=agt,
            sanitaeter=sanitaeter,
            bemerkung=bemerkung,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/personal/modus", response_class=HTMLResponse)
async def personal_modus_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    modus: str = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.personal_modus",
        lambda: ressource_pflege_service.modus_wechseln(
            db, lage, einheit, modus, user_id=request.state.user.id, author_name=get_author_name(request)
        ),
    )


@router.post("/personal/hinzufuegen", response_class=HTMLResponse)
async def person_hinzufuegen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    member_id: int | None = Form(None),
    name: str | None = Form(None),
    funktion: str = Form("mannschaft"),
    qualifikationen: str | None = Form(None),
    bemerkung: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.person_hinzufuegen",
        lambda: ressource_pflege_service.person_hinzufuegen(
            db,
            lage,
            einheit,
            member_id=member_id,
            name=name,
            funktion=funktion,
            qualifikationen=qualifikationen,
            bemerkung=bemerkung,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/personal/{person_id}/entfernen", response_class=HTMLResponse)
async def person_entfernen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    person_id: int,
    grund: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.person_entfernen",
        lambda: ressource_pflege_service.person_entfernen(
            db,
            lage,
            einheit,
            person_id,
            grund=grund,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/personal/verstaerken", response_class=HTMLResponse)
async def personal_verstaerken_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    anzahl: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.personal_verstaerken",
        lambda: ressource_pflege_service.verstaerken(
            db, lage, einheit, anzahl=anzahl, user_id=request.state.user.id, author_name=get_author_name(request)
        ),
    )


@router.post("/personal/{person_id}/abloesen", response_class=HTMLResponse)
async def person_abloesen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    person_id: int,
    member_id: int | None = Form(None),
    name: str | None = Form(None),
    funktion: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.person_abloesen",
        lambda: ressource_pflege_service.abloesen(
            db,
            lage,
            einheit,
            person_id,
            member_id=member_id,
            name=name,
            funktion=funktion,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/personal/umbuchen", response_class=HTMLResponse)
async def personal_umbuchen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    nach_einheit_id: int = Form(...),
    anzahl: int | None = Form(None),
    person_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    ziel = _ziel_einheit_or_404(db, lage, nach_einheit_id)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "personal",
        "gsl.ressource.personal_umbuchen",
        lambda: ressource_pflege_service.umbuchen(
            db,
            lage,
            einheit,
            ziel,
            anzahl=anzahl,
            person_ids=person_ids or None,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
        [einheit, ziel],
    )


@router.post("/ausstattung/hinzufuegen", response_class=HTMLResponse)
async def ausstattung_hinzufuegen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    kategorie: str = Form(...),
    bezeichnung: str | None = Form(None),
    menge: int = Form(1),
    status: str = Form("einsatzbereit"),
    bemerkung: str | None = Form(None),
    stamm_ref_typ: str | None = Form(None),
    stamm_ref_id: int | None = Form(None),
    atemschutz_geraet_id: int | None = Form(None),
    verleih_artikel_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    if atemschutz_geraet_id and verleih_artikel_id:
        return _pflege_error("Bitte nur eine Stammdatenreferenz wählen", "ausstattung")
    if atemschutz_geraet_id:
        stamm_ref_typ, stamm_ref_id = "atemschutz_geraet", atemschutz_geraet_id
    elif verleih_artikel_id:
        stamm_ref_typ, stamm_ref_id = "verleih_artikel", verleih_artikel_id
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "ausstattung",
        "gsl.ressource.ausstattung_hinzufuegen",
        lambda: ressource_pflege_service.ausstattung_hinzufuegen(
            db,
            lage,
            einheit,
            kategorie=kategorie,
            bezeichnung=bezeichnung,
            menge=menge,
            status=status,
            bemerkung=bemerkung,
            stamm_ref_typ=stamm_ref_typ or None,
            stamm_ref_id=stamm_ref_id,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/ausstattung/{zeile_id}/aendern", response_class=HTMLResponse)
async def ausstattung_aendern_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    zeile_id: int,
    menge: int | None = Form(None),
    status: str | None = Form(None),
    bemerkung: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "ausstattung",
        "gsl.ressource.ausstattung_aendern",
        lambda: ressource_pflege_service.ausstattung_aendern(
            db,
            lage,
            einheit,
            zeile_id,
            menge=menge,
            status=status,
            bemerkung=bemerkung,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/ausstattung/{zeile_id}/entfernen", response_class=HTMLResponse)
async def ausstattung_entfernen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    zeile_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "ausstattung",
        "gsl.ressource.ausstattung_entfernen",
        lambda: ressource_pflege_service.ausstattung_entfernen(
            db, lage, einheit, zeile_id, user_id=request.state.user.id, author_name=get_author_name(request)
        ),
    )


@router.post("/ausstattung/vorlage", response_class=HTMLResponse)
async def ausstattung_vorlage_route(
    request: Request, lage_id: int, einheit_id: int, db: Session = Depends(get_db), _=Depends(require_role(*_WRITE))
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "ausstattung",
        "gsl.ressource.ausstattung_vorlage",
        lambda: ressource_pflege_service.vorlage_uebernehmen(
            db, lage, einheit, user_id=request.state.user.id, author_name=get_author_name(request)
        ),
    )


@router.post("/ausstattung/{zeile_id}/umbuchen", response_class=HTMLResponse)
async def ausstattung_umbuchen_route(
    request: Request,
    lage_id: int,
    einheit_id: int,
    zeile_id: int,
    nach_einheit_id: int = Form(...),
    menge: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    ziel = _ziel_einheit_or_404(db, lage, nach_einheit_id)
    return await _pflege_save(
        request,
        lage,
        einheit,
        db,
        "ausstattung",
        "gsl.ressource.ausstattung_umbuchen",
        lambda: ressource_pflege_service.ausstattung_umbuchen(
            db,
            lage,
            einheit,
            ziel,
            zeile_id,
            menge,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
        [einheit, ziel],
    )


async def _save(request: Request, lage, einheit, db: Session, action: str, callback, after_commit=None):
    if not _can_edit(request.state.user):
        raise HTTPException(403, "Keine Bearbeitungsberechtigung")
    try:
        result = callback()
    except ValueError as exc:
        return _error(str(exc))
    write_audit(
        db,
        action,
        org_id=lage.org_id,
        user_id=request.state.user.id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id},
    )
    db.commit()
    if after_commit:
        after_commit(result)
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    response = _overview(request, lage, einheit, db)
    response.headers["HX-Retarget"] = "#ressourceKarteBody"
    response.headers["HX-Reswap"] = "innerHTML"
    response.headers["HX-Trigger"] = "ressourceChanged"
    return response


@router.post("/gruppenkommandant", response_class=HTMLResponse)
async def gruppenkommandant(
    request: Request,
    lage_id: int,
    einheit_id: int,
    background_tasks: BackgroundTasks,
    member_id: int | None = Form(None),
    person_name: str | None = Form(None),
    telefon: str | None = Form(None),
    modus: str = Form("auto"),
    note: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _save(
        request,
        lage,
        einheit,
        db,
        "gsl.ressource.gk_gesetzt",
        lambda: resource_service.setze_gruppenkommandant(
            db,
            lage,
            einheit,
            member_id=member_id,
            person_name=person_name,
            telefon=telefon,
            modus=modus,
            note=note,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
        after_commit=lambda ergebnis: (
            background_tasks.add_task(gk_zugang_service.sende_auto_sms, ergebnis.auto_sms)
            if ergebnis.auto_sms
            else None
        ),
    )


@router.post("/gruppenkommandant/entfernen", response_class=HTMLResponse)
async def gk_entfernen(
    request: Request, lage_id: int, einheit_id: int, db: Session = Depends(get_db), _=Depends(require_role(*_WRITE))
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _save(
        request,
        lage,
        einheit,
        db,
        "gsl.ressource.gk_gesetzt",
        lambda: resource_service.entferne_gruppenkommandant(
            db, lage, einheit, user_id=request.state.user.id, author_name=get_author_name(request)
        ),
    )


@router.post("/stellvertreter", response_class=HTMLResponse)
async def stellvertreter(
    request: Request,
    lage_id: int,
    einheit_id: int,
    member_id: int | None = Form(None),
    person_name: str | None = Form(None),
    telefon: str | None = Form(None),
    modus: str = Form("auto"),
    note: str | None = Form(None),
    entfernen: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    callback = (
        (
            lambda: resource_service.entferne_stellvertreter(
                db, lage, einheit, user_id=request.state.user.id, author_name=get_author_name(request)
            )
        )
        if entfernen
        else (
            lambda: resource_service.setze_stellvertreter(
                db,
                lage,
                einheit,
                member_id=member_id,
                person_name=person_name,
                telefon=telefon,
                modus=modus,
                note=note,
                user_id=request.state.user.id,
                author_name=get_author_name(request),
            )
        )
    )
    return await _save(request, lage, einheit, db, "gsl.ressource.gk_gesetzt", callback)


@router.post("/stamm", response_class=HTMLResponse)
async def stamm(
    request: Request,
    lage_id: int,
    einheit_id: int,
    funkrufname: str | None = Form(None),
    org_name: str | None = Form(None),
    bos: str | None = Form(None),
    bereitstellungsraum: str | None = Form(None),
    qty: int | None = Form(None),
    unit: str | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    return await _save(
        request,
        lage,
        einheit,
        db,
        "gsl.ressource.stamm",
        lambda: resource_service.aktualisiere_einheit_stamm(
            db,
            lage,
            einheit,
            funkrufname=funkrufname,
            org_name=org_name,
            bos=bos,
            bereitstellungsraum=bereitstellungsraum,
            qty=qty,
            unit=unit,
            user_id=request.state.user.id,
            author_name=get_author_name(request),
        ),
    )


@router.post("/journal", response_class=HTMLResponse)
async def journal_eintrag(
    request: Request,
    lage_id: int,
    einheit_id: int,
    text: str = Form(""),
    site_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    if not _can_edit(request.state.user):
        raise HTTPException(403, "Keine Bearbeitungsberechtigung")
    try:
        resource_service.journal_eintrag_manuell(
            db,
            lage,
            einheit,
            text=text,
            site_id=site_id,
            user_id=request.state.user.id,
            author_name=get_author_name(request) or "",
        )
    except ValueError as exc:
        return _journal_error(str(exc))
    write_audit(
        db,
        "gsl.ressource.journal_eintrag",
        org_id=lage.org_id,
        user_id=request.state.user.id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id},
    )
    db.commit()
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    response = _journal(request, lage, einheit, db, typen=None, site_id=None, vor_ts=None, limit=50)
    response.headers["HX-Retarget"] = "#ressourceKarteBody"
    response.headers["HX-Reswap"] = "innerHTML"
    response.headers["HX-Trigger"] = "ressourceChanged"
    return response


@router.post("/journal/{entry_id}/storno", response_class=HTMLResponse)
async def journal_storno(
    request: Request,
    lage_id: int,
    einheit_id: int,
    entry_id: int,
    grund: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role(*_WRITE)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    if not _can_edit(request.state.user):
        raise HTTPException(403, "Keine Bearbeitungsberechtigung")
    try:
        resource_service.journal_eintrag_stornieren(
            db,
            lage,
            einheit,
            entry_id,
            grund=grund,
            user_id=request.state.user.id,
            author_name=get_author_name(request) or "",
        )
    except ValueError as exc:
        return _journal_error(str(exc))
    write_audit(
        db,
        "gsl.ressource.journal_storno",
        org_id=lage.org_id,
        user_id=request.state.user.id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id},
    )
    db.commit()
    await broadcast_lage(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})
    response = _journal(request, lage, einheit, db, typen=None, site_id=None, vor_ts=None, limit=50)
    response.headers["HX-Retarget"] = "#ressourceKarteBody"
    response.headers["HX-Reswap"] = "innerHTML"
    response.headers["HX-Trigger"] = "ressourceChanged"
    return response
