"""Lagedokument: gemeinsam bearbeitbares Dokument je Lage (GSL/Stab).

Eigenstaendig vom Einsatzjournal (das bleibt Append-only) UND vom bestehenden
"KI-Lagebericht" (KI-generierte Einmal-Zusammenfassung, siehe
ui_major_incident.py::lage_ki_bericht) -- daher bewusst "Lagedokument" statt
"Lagebericht" genannt, keine Namens-/Routenkollision. Ein dauerhaftes,
fortlaufend bearbeitbares Textdokument je Lage, gedacht als zusammenfassende
Lagedarstellung (SKKM-Lagemeldung, Uebergabeprotokoll o.Ae.).

PR 1: klassisches Speichern (kein Realtime-Sync). Live-Kollaboration (Yjs)
folgt in einem separaten PR, siehe Plan.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import LageDokument, MajorIncident

router = APIRouter()

_EDIT_ROLLEN = ("incident_leader", "admin", "org_admin", "recorder")
_LESE_ROLLEN = (*_EDIT_ROLLEN, "readonly")


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org_access(user, lage: MajorIncident) -> None:
    if not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Lage")


def _get_or_create_dokument(db: Session, lage: MajorIncident) -> LageDokument:
    dokument = db.query(LageDokument).filter(LageDokument.major_incident_id == lage.id).first()
    if dokument is None:
        dokument = LageDokument(major_incident_id=lage.id, org_id=lage.org_id,
                                updated_at=datetime.now(UTC))
        db.add(dokument)
        db.flush()
    return dokument


@router.get("/lage/{lage_id}/lagedokument", response_class=HTMLResponse)
async def lagedokument_view(
    lage_id: int,
    request: Request,
    db: Session = Depends(get_db),
    gespeichert: int = 0,
    _=Depends(require_role(*_LESE_ROLLEN)),
):
    from app.routers.ui_major_incident import _get_mi_features

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    dokument = _get_or_create_dokument(db, lage)
    db.commit()
    return templates.TemplateResponse(request, "incident_major/lagedokument.html", {
        "user": user,
        "lage": lage,
        "dokument": dokument,
        "can_edit": has_role(user, *_EDIT_ROLLEN),
        "gespeichert": bool(gespeichert),
        "mi_features": _get_mi_features(db, lage.org_id),
    })


@router.post("/lage/{lage_id}/lagedokument", response_class=HTMLResponse)
async def lagedokument_save(
    lage_id: int,
    request: Request,
    db: Session = Depends(get_db),
    content_html: str = Form(""),
    _=Depends(require_role(*_EDIT_ROLLEN)),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    dokument = _get_or_create_dokument(db, lage)
    dokument.content_html = content_html  # Sanitizing laeuft im @validates-Hook des Modells
    dokument.updated_at = datetime.now(UTC)
    dokument.updated_by_user_id = user.id
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/lagedokument?gespeichert=1", status_code=303)
