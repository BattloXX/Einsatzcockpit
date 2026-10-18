"""Einsatzkontakte mit eigener, vom zentralen Adressbuch getrennter Anzeige."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import (
    can_view_kontakt_directory_request,
    has_role,
    require_kontakt_directory,
    require_role,
    require_role_or_device,
)
from app.core.templating import templates
from app.db import get_db
from app.models.kontakt import EinsatzKontakt
from app.models.user import User
from app.routers.ui_kontakt import require_kontakte_enabled
from app.services import kontakt_service
from app.services.kontakt_access_service import contact_summary, incident_for_contacts

router = APIRouter(
    prefix="/einsatz/{incident_id}/kontakte", tags=["kontakte"], dependencies=[Depends(require_kontakte_enabled)]
)
_READ = ("readonly", "recorder", "breathing_supervisor", "incident_leader", "kontakt_verwalter", "objekt_verwalter")
_WRITE = ("recorder", "incident_leader", "kontakt_verwalter")


def _panel(request: Request, db: Session, incident_id: int, q: str = ""):
    incident = incident_for_contacts(db, request, incident_id)
    full = can_view_kontakt_directory_request(request)
    user = request.state.user
    can_edit = full and has_role(user, *_WRITE)
    mappings = db.query(EinsatzKontakt).filter(EinsatzKontakt.incident_id == incident.id).all()
    contacts = [
        contact_summary(m.kontakt, full=full) | {"hinweis": m.hinweis}
        for m in mappings
        if m.kontakt and (full or (m.kontakt.aktiv and not m.kontakt.archiviert))
    ]
    candidates = kontakt_service.list_kontakte(db, q=q)[0] if can_edit and q.strip() else []
    selected = {m.kontakt_id for m in mappings}
    return templates.TemplateResponse(
        request,
        "incident/_kontakte.html",
        {
            "user": user,
            "incident": incident,
            "einsatzkontakte": contacts,
            "kontakt_kandidaten": [c for c in candidates if c.id not in selected],
            "kontakte_bearbeiten": can_edit,
            "kontakte_vollzugriff": full,
            "q": q,
        },
    )


@router.get("")
def panel(
    incident_id: int,
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_role_or_device(*_READ)),
):
    return _panel(request, db, incident_id, q)


@router.post("/zuordnen", dependencies=[Depends(require_kontakt_directory)])
def zuordnen(
    incident_id: int,
    request: Request,
    kontakt_id: int = Form(...),
    hinweis: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_WRITE)),
):
    incident_for_contacts(db, request, incident_id)
    kontakt = kontakt_service.get_kontakt(db, kontakt_id)
    if kontakt is None:
        raise HTTPException(404, "Kontakt nicht gefunden")
    mapping = db.query(EinsatzKontakt).filter_by(incident_id=incident_id, kontakt_id=kontakt_id).first()
    if mapping is None:
        mapping = EinsatzKontakt(org_id=kontakt.org_id, incident_id=incident_id, kontakt_id=kontakt_id)
        db.add(mapping)
    mapping.hinweis = hinweis.strip()[:500] or None
    write_audit(
        db,
        "kontakt.einsatz_zugeordnet",
        org_id=kontakt.org_id,
        user_id=user.id,
        incident_id=incident_id,
        entity_type="kontakt",
        entity_id=kontakt_id,
    )
    db.commit()
    return _panel(request, db, incident_id)


@router.post("/{kontakt_id}/entfernen", dependencies=[Depends(require_kontakt_directory)])
def entfernen(
    incident_id: int,
    kontakt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_WRITE)),
):
    incident_for_contacts(db, request, incident_id)
    mapping = db.query(EinsatzKontakt).filter_by(incident_id=incident_id, kontakt_id=kontakt_id).first()
    if mapping is None:
        raise HTTPException(404, "Kontaktzuordnung nicht gefunden")
    write_audit(
        db,
        "kontakt.einsatz_entfernt",
        org_id=mapping.org_id,
        user_id=user.id,
        incident_id=incident_id,
        entity_type="kontakt",
        entity_id=kontakt_id,
    )
    db.delete(mapping)
    db.commit()
    return _panel(request, db, incident_id)
