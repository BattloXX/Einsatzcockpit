"""Bild-Annotation: Editor-Seite, Autosave, geschuetzte Anzeige (media_typ-agnostisch).

Zugriff/Scoping laeuft ueber annotation_service (Host-Entitaet). PR1: nur Bilder
an Auftraegen (media_typ = "task"); weitere Typen folgen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.services import annotation_service as ann_svc

router = APIRouter(tags=["annotation"])


def _lade_media(db: Session, media_typ: str, media_id: int):  # type: ignore[no-untyped-def]
    media = ann_svc.resolve_media(db, media_typ, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Medium nicht gefunden")
    if not ann_svc.is_annotatable(media_typ, media):
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
    can_write = ann_svc.can_write(db, user, media_typ, media)
    lock = {"locked_by_other": False, "name": None}
    if can_write:
        lock = ann_svc.acquire_lock(db, media_typ, media_id, getattr(media, "org_id", None), user)
        db.commit()
    return templates.TemplateResponse(request, "annotate/editor.html", {
        "user": user,
        "media_typ": media_typ,
        "media_id": media_id,
        "bild_url": f"/annotieren/{media_typ}/{media_id}/original",
        "annotation_json": (ann.annotation_json if ann else "") or "",
        "dateiname": getattr(media, "original_filename", ""),
        "can_write": can_write,
        "lock_other": lock["locked_by_other"],
        "lock_name": lock["name"] or "",
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


@router.get("/annotieren/{media_typ}/{media_id}/original")
def annotate_original(
    media_typ: str,
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder", "readonly")),
):
    """Original-Bild (Editor-Hintergrund) — immer das Original, nie die annotierte Version."""
    media = _lade_media(db, media_typ, media_id)
    if not ann_svc.can_read(db, user, media_typ, media):
        raise HTTPException(status_code=403, detail="Kein Zugriff")
    p = ann_svc.original_abs_path(media_typ, media)
    if not p or not p.exists():
        return Response(status_code=404)
    return FileResponse(p, media_type=getattr(media, "mime_type", None) or "image/jpeg")


@router.post("/api/annotation/{media_typ}/{media_id}/lock")
def annotate_lock(
    media_typ: str,
    media_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder")),
):
    media = _lade_media(db, media_typ, media_id)
    if not ann_svc.can_write(db, user, media_typ, media):
        raise HTTPException(status_code=403, detail="Kein Schreibrecht")
    info = ann_svc.acquire_lock(db, media_typ, media_id, getattr(media, "org_id", None), user)
    db.commit()
    return JSONResponse(info)


@router.delete("/api/annotation/{media_typ}/{media_id}/lock")
def annotate_unlock(
    media_typ: str,
    media_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("incident_leader", "admin", "recorder")),
):
    ann_svc.release_lock(db, media_typ, media_id, user)
    db.commit()
    return JSONResponse({"ok": True})


# ── Objektübernahme (Feature B) ──────────────────────────────────────────────

_EDIT_ROLLEN = ("incident_leader", "admin", "recorder")


def _incident_write_or_403(db: Session, user: User, incident) -> None:  # type: ignore[no-untyped-def]
    if not ann_svc._may_access_incident(user, incident) or not has_role(user, *_EDIT_ROLLEN):
        raise HTTPException(status_code=403, detail="Kein Schreibrecht")


@router.get("/api/uebernahme/objekte")
def uebernahme_objekte(
    incident_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_EDIT_ROLLEN)),
):
    """Objektliste fuer den Uebernahme-Dialog — mit dem Einsatz verknuepfte zuerst."""
    from app.models.objekt import Objekt, ObjektEinsatz
    verknuepft = {
        r[0] for r in db.query(ObjektEinsatz.objekt_id)
        .filter(ObjektEinsatz.incident_id == incident_id).all()
    }
    objekte = db.query(Objekt).filter(Objekt.org_id == user.org_id).all()
    objekte.sort(key=lambda o: (0 if o.id in verknuepft else 1, (o.name or "").lower()))
    return {"objekte": [
        {"id": o.id, "name": f"{o.anzeige_nummer} {o.name}", "verknuepft": o.id in verknuepft}
        for o in objekte
    ]}


@router.get("/api/uebernahme/objekt/{objekt_id}/seiten")
def uebernahme_seiten_liste(
    objekt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_EDIT_ROLLEN)),
):
    """Dokumente eines Objekts mit ihren gerenderten Seiten (Thumbnails)."""
    from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
    objekt = db.get(Objekt, objekt_id)
    if objekt is None or (not user.is_system_admin and objekt.org_id != user.org_id):
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden")
    seiten = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.objekt_id == objekt_id, ObjektDokumentSeite.bild_pfad.isnot(None))
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
        .all()
    )
    per_dok: dict[int, list] = {}
    for s in seiten:
        per_dok.setdefault(s.dokument_id, []).append(s)
    dokumente = []
    for d in db.query(ObjektDokument).filter(ObjektDokument.objekt_id == objekt_id).order_by(ObjektDokument.id).all():
        ss = per_dok.get(d.id)
        if not ss:
            continue
        dokumente.append({
            "dokument_id": d.id, "name": d.dateiname_original,
            "seiten": [{"id": s.id, "nr": s.seiten_nr, "thumb": f"/objekt-medien/seite/{s.id}/thumb"} for s in ss],
        })
    return {"dokumente": dokumente}


def _parse_ids(seiten_ids: str) -> list[int]:
    try:
        return [int(x) for x in seiten_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungueltige Seitenauswahl") from None


@router.post("/api/uebernahme/task/{task_id}", response_class=HTMLResponse)
def uebernahme_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_EDIT_ROLLEN)),
    seiten_ids: str = Form(...),
):
    from app.models.incident import Incident, Task
    from app.services.takeover_service import uebernehme_seiten
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    incident = db.get(Incident, task.incident_id)
    _incident_write_or_403(db, user, incident)
    uebernehme_seiten(db, "task", task, _parse_ids(seiten_ids), user, user.org_id)
    db.commit()
    db.refresh(task, ["media"])
    return templates.TemplateResponse(request, "incident/_task_media.html", {
        "user": user, "task": task, "incident": incident,
        "can_edit": has_role(user, *_EDIT_ROLLEN),
        "can_note": has_role(user, *_EDIT_ROLLEN, "readonly"),
        "errors": [],
    })
