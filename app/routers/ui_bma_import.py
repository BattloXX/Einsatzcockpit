"""Review-Queue fuer Vorschlaege aus hochgeladenen BMA-Datenblaettern."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.bma_import import BMA_SATZ_AKTIV, BMA_ZUORDNUNG_OFFEN, BmaImportSatz
from app.models.objekt import OBJEKT_STATUS_ARCHIVIERT, Objekt
from app.models.user import User

router = APIRouter()


def _objekt_guard(request: Request) -> None:
    from app.routers.ui_objekt import require_objekt_enabled
    require_objekt_enabled(request)


def _queue_context(request: Request, db: Session, user: User) -> dict:
    from app.services.bma_import.bma_sync import baue_diff, ist_offener_vorschlag
    saetze = db.query(BmaImportSatz).filter(BmaImportSatz.org_id == user.org_id).all()
    objekt_ids = {s.objekt_id for s in saetze if s.objekt_id}
    objekte = {o.id: o for o in db.query(Objekt).options(selectinload(Objekt.kontakte)).filter(
        Objekt.id.in_(objekt_ids)).all()} if objekt_ids else {}
    vorschlaege = []
    for satz in saetze:
        objekt = objekte.get(satz.objekt_id) if satz.objekt_id is not None else None
        if ist_offener_vorschlag(satz, objekt):
            vorschlaege.append((satz, objekt, baue_diff(satz, objekt)))
    return {
        "request": request, "user": user, "vorschlaege": vorschlaege,
        "nicht_zugeordnet": [s for s in saetze if s.status == BMA_SATZ_AKTIV and s.zuordnung == BMA_ZUORDNUNG_OFFEN],
        "verschwunden": [],
        "objekte_zur_auswahl": db.query(Objekt)
        .filter(Objekt.status != OBJEKT_STATUS_ARCHIVIERT)
        .order_by(Objekt.name)
        .all(),
    }


@router.get("/objekte/bma-import", response_class=HTMLResponse)
def bma_import_queue_page(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_role("objekt_verwalter")),
                          _guard: None = Depends(_objekt_guard)):
    return templates.TemplateResponse(request, "objekt/bma_import.html", _queue_context(request, db, user))


@router.get("/objekte/bma-import/datenblatt-upload")
def bma_datenblatt_upload_altpfad():
    return RedirectResponse("/objekte/dokument-upload", status_code=301)


def _satz_or_404(db: Session, satz_id: int, user: User) -> BmaImportSatz:
    satz = db.query(BmaImportSatz).filter(BmaImportSatz.id == satz_id, BmaImportSatz.org_id == user.org_id).first()
    if satz is None:
        raise HTTPException(404, "BMA-Importsatz nicht gefunden")
    return satz


def _antwort(request, db, user):
    return templates.TemplateResponse(request, "objekt/_bma_import_content.html", _queue_context(request, db, user))


@router.post("/objekte/bma-import/{satz_id}/uebernehmen", response_class=HTMLResponse)
def bma_import_uebernehmen(satz_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require_role("objekt_verwalter"))):
    from app.services.bma_import.bma_sync import uebernehme_vorschlag
    uebernehme_vorschlag(db, _satz_or_404(db, satz_id, user), user)
    db.commit()
    return _antwort(request, db, user)


@router.post("/objekte/bma-import/{satz_id}/ignorieren", response_class=HTMLResponse)
def bma_import_ignorieren(satz_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_role("objekt_verwalter"))):
    from app.services.bma_import.bma_sync import ignoriere_vorschlag
    ignoriere_vorschlag(db, _satz_or_404(db, satz_id, user), user)
    db.commit()
    return _antwort(request, db, user)


@router.post("/objekte/bma-import/{satz_id}/objekt-anlegen", response_class=HTMLResponse)
def bma_import_objekt_anlegen(satz_id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require_role("objekt_verwalter"))):
    from app.services.bma_import.bma_sync import lege_objekt_fuer_satz_an
    lege_objekt_fuer_satz_an(db, _satz_or_404(db, satz_id, user), user)
    db.commit()
    return _antwort(request, db, user)


@router.post("/objekte/bma-import/{satz_id}/zuordnen", response_class=HTMLResponse)
def bma_import_zuordnen(satz_id: int, request: Request, ziel_objekt_id: int = Form(...),
                        db: Session = Depends(get_db), user: User = Depends(require_role("objekt_verwalter"))):
    from app.services.bma_import.bma_sync import ordne_satz_objekt_zu
    objekt = db.query(Objekt).filter(Objekt.id == ziel_objekt_id).first()
    if objekt is None:
        raise HTTPException(404, "Objekt nicht gefunden")
    ordne_satz_objekt_zu(db, _satz_or_404(db, satz_id, user), objekt, user)
    db.commit()
    return _antwort(request, db, user)
