"""Detailkarte einer Lage-Einheit."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.security import get_author_name
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import LageEinheit
from app.models.master import Member
from app.routers.ui_major_incident import _can_edit, _check_org_access, _get_mi_features, _lage_or_404, _nav_counts
from app.services import resource_service, ressource_karte_service
from app.services.broadcast import broadcast_lage

router = APIRouter(prefix="/lage/{lage_id}/einheiten/{einheit_id}")
_READ = ("incident_leader", "admin", "org_admin", "recorder", "readonly")
_WRITE = ("incident_leader", "admin", "org_admin", "recorder")


def _context(request: Request, lage_id: int, einheit_id: int, db: Session):
    lage = _lage_or_404(lage_id, db)
    _check_org_access(request.state.user, lage)
    einheit = db.get(LageEinheit, einheit_id)
    if not einheit or einheit.lage_id != lage.id:
        raise HTTPException(404, "Einheit nicht gefunden")
    return lage, einheit


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
    db: Session = Depends(get_db),
    _=Depends(require_role(*_READ)),
):
    lage, einheit = _context(request, lage_id, einheit_id, db)
    if tab == "uebersicht":
        return _overview(request, lage, einheit, db)
    if tab in {"einsaetze", "journal"}:
        return HTMLResponse('<div class="res-section">Wird geladen...</div>')
    raise HTTPException(404, "Unbekannter Tab")


def _error(text: str) -> HTMLResponse:
    return HTMLResponse(text, status_code=422, headers={"HX-Retarget": "#gkFehler", "HX-Reswap": "innerHTML"})


async def _save(request: Request, lage, einheit, db: Session, action: str, callback):
    if not _can_edit(request.state.user):
        raise HTTPException(403, "Keine Bearbeitungsberechtigung")
    try:
        callback()
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
