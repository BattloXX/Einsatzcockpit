"""UI Router – Medien-Galerie und geschützte Datei-Auslieferung.

Alle Mediendateien liegen außerhalb von app/static und werden ausschließlich
über diese geschützten Routen ausgeliefert (Org-Check, Auth erforderlich).
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, joinedload

from app.core.permissions import has_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, MessageMedia, PersonMedia, Task, TaskMedia
from app.models.user import User
from app.services.media_service import absolute_path, absolute_thumb_path

router = APIRouter()

_PAGE_SIZE = 24


def _user_may_access_incident(user: User, incident: Incident) -> bool:
    if has_role(user, "system_admin"):
        return True
    if user.org_id and incident.primary_org_id == user.org_id:
        return True
    for io in (incident.collaborating_orgs or []):
        if io.org_id == user.org_id:
            return True
    return False


@router.get("/medien", response_class=HTMLResponse)
async def media_gallery(
    request: Request,
    db: Session = Depends(get_db),
    incident_id: int | None = Query(None),
    von: str | None = Query(None),
    bis: str | None = Query(None),
    kind: str | None = Query(None),
    page: int = Query(1, ge=1),
):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    q = (
        db.query(TaskMedia)
        .join(Incident, TaskMedia.incident_id == Incident.id)
        .options(joinedload(TaskMedia.task).joinedload(Task.incident))
    )

    if not has_role(user, "system_admin"):
        if not user.org_id:
            items: list[TaskMedia] = []
            return templates.TemplateResponse(request, "media/gallery.html", {
                "user": user, "items": items, "page": 1, "total_pages": 1,
                "total": 0, "filter_incident_id": None,
                "filter_von": "", "filter_bis": "", "filter_kind": "",
                "uploaders": {},
            })
        q = q.filter(Incident.primary_org_id == user.org_id)

    if incident_id:
        q = q.filter(TaskMedia.incident_id == incident_id)
    if kind and kind in ("image", "pdf", "video"):
        q = q.filter(TaskMedia.kind == kind)
    if von:
        try:
            d = date.fromisoformat(von)
            q = q.filter(TaskMedia.created_at >= datetime(d.year, d.month, d.day, tzinfo=timezone.utc))
        except ValueError:
            von = None
    if bis:
        try:
            d = date.fromisoformat(bis)
            q = q.filter(
                TaskMedia.created_at < datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
            )
        except ValueError:
            bis = None

    total: int = q.count()
    items = q.order_by(TaskMedia.created_at.desc()).offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE).all()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    uploader_ids = {m.uploaded_by_user_id for m in items if m.uploaded_by_user_id}
    uploaders: dict[int, str] = {}
    if uploader_ids:
        uploaders = {
            u.id: u.display_name
            for u in db.query(User).filter(User.id.in_(uploader_ids)).all()
        }

    return templates.TemplateResponse(request, "media/gallery.html", {
        "user": user,
        "items": items,
        "uploaders": uploaders,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "filter_incident_id": incident_id,
        "filter_von": von or "",
        "filter_bis": bis or "",
        "filter_kind": kind or "",
    })


def _serve_file(user, media, db, download: bool = False):
    if not media:
        return Response(status_code=404)
    incident = db.get(Incident, media.incident_id)
    if not incident or not _user_may_access_incident(user, incident):
        return Response(status_code=403)
    path = absolute_path(media)
    if not path.exists():
        return Response(status_code=404)
    # Default ist "inline" damit der In-App-Viewer PDFs/Videos im <iframe>/<video>
    # rendern kann. Mit ?download=1 wird "attachment" zurückgegeben → Browser
    # speichert die Datei direkt ab.
    return FileResponse(
        path,
        media_type=media.mime_type,
        filename=media.original_filename,
        content_disposition_type="attachment" if download else "inline",
    )


def _serve_thumb(user, media, db):
    if not media:
        return Response(status_code=404)
    incident = db.get(Incident, media.incident_id)
    if not incident or not _user_may_access_incident(user, incident):
        return Response(status_code=403)
    thumb = absolute_thumb_path(media)
    if thumb and thumb.exists():
        return FileResponse(thumb, media_type="image/jpeg")
    if media.kind == "image":
        path = absolute_path(media)
        if path.exists():
            return FileResponse(path, media_type=media.mime_type)
    return Response(status_code=404)


@router.get("/medien/datei/{media_id}")
async def serve_media_file(media_id: int, request: Request, db: Session = Depends(get_db),
                           download: int = Query(0)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_file(user, db.get(TaskMedia, media_id), db, download=bool(download))


@router.get("/medien/thumb/{media_id}")
async def serve_media_thumb(media_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_thumb(user, db.get(TaskMedia, media_id), db)


@router.get("/medien/meldung/datei/{media_id}")
async def serve_message_media_file(media_id: int, request: Request, db: Session = Depends(get_db),
                                   download: int = Query(0)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_file(user, db.get(MessageMedia, media_id), db, download=bool(download))


@router.get("/medien/meldung/thumb/{media_id}")
async def serve_message_media_thumb(media_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_thumb(user, db.get(MessageMedia, media_id), db)


@router.get("/medien/person/datei/{media_id}")
async def serve_person_media_file(media_id: int, request: Request, db: Session = Depends(get_db),
                                  download: int = Query(0)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_file(user, db.get(PersonMedia, media_id), db, download=bool(download))


@router.get("/medien/person/thumb/{media_id}")
async def serve_person_media_thumb(media_id: int, request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return Response(status_code=401)
    return _serve_thumb(user, db.get(PersonMedia, media_id), db)
