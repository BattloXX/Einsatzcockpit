"""Bild-Annotation: Editor-Seite, Autosave, geschuetzte Anzeige (media_typ-agnostisch).

Zugriff/Scoping laeuft ueber annotation_service (Host-Entitaet). PR1: nur Bilder
an Auftraegen (media_typ = "task"); weitere Typen folgen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.services import annotation_service as ann_svc

router = APIRouter(tags=["annotation"])


def _lade_media(db: Session, media_typ: str, media_id: int):  # type: ignore[no-untyped-def]
    media = ann_svc.resolve_media(db, media_typ, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Medium nicht gefunden")
    if not ann_svc.is_annotatable(media):
        raise HTTPException(status_code=400, detail="Nur Bilder koennen annotiert werden")
    return media


@router.get("/annotieren/{media_typ}/{media_id}", response_class=HTMLResponse)
def annotate_editor(
    media_typ: str,
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder", "readonly")),
):
    media = _lade_media(db, media_typ, media_id)
    if not ann_svc.can_read(db, user, media_typ, media):
        raise HTTPException(status_code=403, detail="Kein Zugriff")
    ann = ann_svc.get_annotation(db, media_typ, media_id)
    spec = ann_svc.spec_for(media_typ)
    return templates.TemplateResponse(request, "annotate/editor.html", {
        "user": user,
        "media_typ": media_typ,
        "media_id": media_id,
        "bild_url": spec.bild_url(media),
        "annotation_json": (ann.annotation_json if ann else "") or "",
        "dateiname": getattr(media, "original_filename", ""),
        "can_write": ann_svc.can_write(db, user, media_typ, media),
    })


class AnnotationPayload(BaseModel):
    annotation_json: str
    png: str | None = None  # data:image/png;base64,…


@router.put("/api/annotation/{media_typ}/{media_id}")
async def annotate_save(
    media_typ: str,
    media_id: int,
    payload: AnnotationPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder")),
):
    media = _lade_media(db, media_typ, media_id)
    if not ann_svc.can_write(db, user, media_typ, media):
        raise HTTPException(status_code=403, detail="Kein Schreibrecht")
    ann = ann_svc.save_annotation(db, user, media_typ, media, payload.annotation_json, payload.png)
    db.commit()
    return JSONResponse({"ok": True, "annotated": bool(ann.annotated_file)})


@router.get("/annotieren/{media_typ}/{media_id}/bild")
def annotate_display(
    media_typ: str,
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder", "readonly")),
):
    """Liefert die annotierte Version (flaches PNG) falls vorhanden, sonst das Original."""
    media = _lade_media(db, media_typ, media_id)
    if not ann_svc.can_read(db, user, media_typ, media):
        raise HTTPException(status_code=403, detail="Kein Zugriff")
    path = ann_svc.display_abs_path(db, media_typ, media)
    if not path or not path.exists():
        return Response(status_code=404)
    # PNG (annotiert) oder Original-MIME
    mime = "image/png" if path.suffix.lower() == ".png" else getattr(media, "mime_type", "image/jpeg")
    return FileResponse(path, media_type=mime)
