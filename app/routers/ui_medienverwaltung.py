"""Medienverwaltung (Admin): org-weite Uebersicht aller hochgeladenen Medien
(Einsatz + Grossschadenslage) mit Groesse/Alter/Typ-Sortierung und -Filterung.

Ergaenzt die bestehende /medien-Galerie (nur TaskMedia, ohne Groessen-/Sortier-
Spalten) um eine Verwaltungssicht ueber alle sechs Medientypen hinweg, analog
zur reinen Aggregat-Uebersicht /admin/system/quotas (dort nur Summen je Org).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.services import annotation_service as ann_svc

router = APIRouter()

_PAGE_SIZE = 50

# (typ, kind-Wert je Medienzeile) -> deutsches Label
_TYP_LABELS = {
    "task": "Auftrag", "message": "Meldung", "person": "Person",
    "site": "Lage-Stelle", "cross_marker": "Übergreifend", "lage_journal": "Lage-Journal",
}
_KIND_LABELS = {"image": "Bild", "pdf": "PDF", "video": "Video"}


@dataclass
class _Row:
    id: int
    typ: str
    original_filename: str
    kind: str
    bytes: int
    created_at: datetime
    uploader_name: str
    annotated: bool
    annotated_at: int | None
    context_label: str
    context_url: str | None
    deletable: bool
    thumb_url: str | None
    file_url: str


def _target_org_id(user, org_param: int | None) -> int | None:
    if has_role(user, "system_admin") and org_param and org_param != 0:
        return org_param
    return user.org_id


def _gather_task_message_person(db: Session, org_id: int) -> list[_Row]:
    from app.models.incident import Incident, MessageMedia, PersonMedia, TaskMedia
    from app.models.user import User

    rows: list[_Row] = []
    incidents = {i.id: i for i in db.query(Incident).filter(Incident.primary_org_id == org_id).all()}
    if not incidents:
        return rows
    incident_ids = list(incidents)

    def _label(inc) -> str:
        ort = inc.address_city or ""
        return f"Einsatz {inc.alarm_type_code} · {ort}".strip(" ·") or f"Einsatz #{inc.id}"

    def _uploaders(ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        return {u.id: u.display_name for u in db.query(User).filter(User.id.in_(ids)).all()}

    task_media = db.query(TaskMedia).filter(TaskMedia.incident_id.in_(incident_ids)).all()
    msg_media = db.query(MessageMedia).filter(MessageMedia.incident_id.in_(incident_ids)).all()
    person_media = db.query(PersonMedia).filter(PersonMedia.incident_id.in_(incident_ids)).all()

    uploader_ids = {
        m.uploaded_by_user_id for m in (task_media + msg_media + person_media) if m.uploaded_by_user_id
    }
    uploaders = _uploaders(uploader_ids)

    # URL-Segment je Typ weicht vom media_typ ab (message -> "meldung" in der Route)
    _url_segment = {"task": "", "message": "/meldung", "person": "/person"}

    for typ, items in (("task", task_media), ("message", msg_media), ("person", person_media)):
        image_ids = [m.id for m in items if m.kind == "image"]
        ann_versions = ann_svc.annotated_versions(db, typ, image_ids)
        seg = _url_segment[typ]
        for m in items:
            inc = incidents.get(m.incident_id)
            rows.append(_Row(
                id=m.id, typ=typ, original_filename=m.original_filename, kind=m.kind,
                bytes=m.bytes or 0, created_at=m.created_at,
                uploader_name=uploaders.get(m.uploaded_by_user_id, "–"),
                annotated=m.id in ann_versions, annotated_at=ann_versions.get(m.id),
                context_label=_label(inc) if inc else "–",
                context_url=f"/einsatz/{m.incident_id}" if inc else None,
                deletable=True,
                thumb_url=f"/medien{seg}/thumb/{m.id}" if m.kind == "image" else None,
                file_url=f"/medien{seg}/datei/{m.id}",
            ))
    return rows


def _gather_gsl(db: Session, org_id: int) -> list[_Row]:
    from app.models.major_incident import (
        CrossMarkerMedia,
        CrossSiteMarker,
        IncidentSite,
        LageJournalEntry,
        LageJournalMedia,
        SiteMedia,
    )
    from app.models.user import User

    rows: list[_Row] = []

    def _uploaders(ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        return {u.id: u.display_name for u in db.query(User).filter(User.id.in_(ids)).all()}

    # Site-Medien
    site_media = db.query(SiteMedia).filter(SiteMedia.org_id == org_id).all()
    site_ids = {m.incident_site_id for m in site_media}
    sites = {s.id: s for s in db.query(IncidentSite).filter(IncidentSite.id.in_(site_ids)).all()} if site_ids else {}
    uploaders = _uploaders({m.uploaded_by for m in site_media if m.uploaded_by})
    ann_versions = ann_svc.annotated_versions(db, "site", [m.id for m in site_media])
    for m in site_media:
        site = sites.get(m.incident_site_id)
        rows.append(_Row(
            id=m.id, typ="site", original_filename=m.original_filename, kind=m.media_type,
            bytes=m.bytes or 0, created_at=m.uploaded_at,
            uploader_name=m.author_name or uploaders.get(m.uploaded_by, "–"),
            annotated=m.id in ann_versions, annotated_at=ann_versions.get(m.id),
            context_label=f"Lage-Stelle: {site.bezeichnung}" if site else "–",
            context_url=f"/lage/{site.major_incident_id}" if site else None,
            deletable=False,
            thumb_url=f"/lage-medien/thumb/{m.id}" if m.media_type == "image" else None,
            file_url=f"/lage-medien/{m.id}",
        ))

    # Cross-Marker-Medien
    cross_media = db.query(CrossMarkerMedia).filter(CrossMarkerMedia.org_id == org_id).all()
    marker_ids = {m.marker_id for m in cross_media}
    markers = (
        {mk.id: mk for mk in db.query(CrossSiteMarker).filter(CrossSiteMarker.id.in_(marker_ids)).all()}
        if marker_ids else {}
    )
    uploaders = _uploaders({m.uploaded_by for m in cross_media if m.uploaded_by})
    ann_versions = ann_svc.annotated_versions(db, "cross_marker", [m.id for m in cross_media])
    for m in cross_media:
        marker = markers.get(m.marker_id)
        base = (
            f"/lage/{marker.major_incident_id}/uebergreifend/{marker.id}/medien/{m.id}/bild"
            if marker else None
        )
        rows.append(_Row(
            id=m.id, typ="cross_marker", original_filename=m.original_filename, kind=m.media_type,
            bytes=m.bytes or 0, created_at=m.uploaded_at,
            uploader_name=m.author_name or uploaders.get(m.uploaded_by, "–"),
            annotated=m.id in ann_versions, annotated_at=ann_versions.get(m.id),
            context_label=f"Übergreifend: {marker.title}" if marker else "–",
            context_url=f"/lage/{marker.major_incident_id}" if marker else None,
            deletable=False,
            thumb_url=(f"{base}?thumb=true" if base and m.media_type == "image" else None),
            file_url=base or "#",
        ))

    # Lage-Journal-Medien
    journal_media = db.query(LageJournalMedia).filter(LageJournalMedia.org_id == org_id).all()
    entry_ids = {m.journal_entry_id for m in journal_media}
    entries = (
        {e.id: e for e in db.query(LageJournalEntry).filter(LageJournalEntry.id.in_(entry_ids)).all()}
        if entry_ids else {}
    )
    uploaders = _uploaders({m.uploaded_by for m in journal_media if m.uploaded_by})
    ann_versions = ann_svc.annotated_versions(db, "lage_journal", [m.id for m in journal_media])
    for m in journal_media:
        entry = entries.get(m.journal_entry_id)
        base = (
            f"/lage/{entry.major_incident_id}/journal/{m.journal_entry_id}/medien/{m.id}/bild"
            if entry else None
        )
        rows.append(_Row(
            id=m.id, typ="lage_journal", original_filename=m.original_filename, kind=m.media_type,
            bytes=m.bytes or 0, created_at=m.uploaded_at,
            uploader_name=m.author_name or uploaders.get(m.uploaded_by, "–"),
            annotated=m.id in ann_versions, annotated_at=ann_versions.get(m.id),
            context_label="Lage-Journal" if entry else "–",
            context_url=f"/lage/{entry.major_incident_id}" if entry else None,
            deletable=False,
            thumb_url=(f"{base}?thumb=true" if base and m.media_type == "image" else None),
            file_url=base or "#",
        ))
    return rows


def _apply_filters(rows: list[_Row], *, typ: str, q: str, nur_bearbeitet: bool,
                    von: date | None, bis: date | None) -> list[_Row]:
    if typ:
        rows = [r for r in rows if r.kind == typ]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.original_filename.lower()]
    if nur_bearbeitet:
        rows = [r for r in rows if r.annotated]
    if von:
        rows = [r for r in rows if r.created_at.date() >= von]
    if bis:
        rows = [r for r in rows if r.created_at.date() <= bis]
    return rows


def _apply_sort(rows: list[_Row], *, sort: str, direction: str) -> list[_Row]:
    key_fn = {
        "size": lambda r: r.bytes,
        "age": lambda r: r.created_at,
        "name": lambda r: r.original_filename.lower(),
    }.get(sort, lambda r: r.bytes)
    return sorted(rows, key=key_fn, reverse=(direction != "asc"))


@router.get("/admin/medien", response_class=HTMLResponse)
async def medienverwaltung(
    request: Request,
    db: Session = Depends(get_db),
    org: int | None = Query(None),
    sort: str = Query("size"),
    dir: str = Query("desc"),
    typ: str = Query(""),
    q: str = Query(""),
    nur_bearbeitet: int = Query(0),
    von: str | None = Query(None),
    bis: str | None = Query(None),
    page: int = Query(1, ge=1),
    _user=Depends(require_role("admin")),
):
    from app.models.master import FireDept
    from app.services.storage_service import get_org_storage_info

    user = request.state.user
    org_id = _target_org_id(user, org)

    orgs_for_switch = (
        db.query(FireDept).filter(FireDept.deleted_at.is_(None)).order_by(FireDept.name).all()
        if has_role(user, "system_admin") else []
    )

    if not org_id:
        return templates.TemplateResponse(request, "admin/medienverwaltung.html", {
            "user": user, "is_sysadmin": has_role(user, "system_admin"),
            "orgs_for_switch": orgs_for_switch, "current_org_id": None,
            "rows": [], "page": 1, "total_pages": 1, "total": 0,
            "storage": {"used_bytes": 0, "quota_bytes": None},
            "sort": sort, "dir": dir, "typ": typ, "q": q, "nur_bearbeitet": nur_bearbeitet,
            "von": von or "", "bis": bis or "",
            "typ_labels": _TYP_LABELS, "kind_labels": _KIND_LABELS,
        })

    von_d = bis_d = None
    try:
        von_d = date.fromisoformat(von) if von else None
    except ValueError:
        von = None
    try:
        bis_d = date.fromisoformat(bis) if bis else None
    except ValueError:
        bis = None

    rows = _gather_task_message_person(db, org_id) + _gather_gsl(db, org_id)
    rows = _apply_filters(
        rows, typ=typ, q=q.strip(), nur_bearbeitet=bool(nur_bearbeitet), von=von_d, bis=bis_d,
    )
    rows = _apply_sort(rows, sort=sort, direction=dir)

    total = len(rows)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(page, total_pages)
    page_rows = rows[(page - 1) * _PAGE_SIZE: page * _PAGE_SIZE]

    storage = get_org_storage_info(db, org_id)

    return templates.TemplateResponse(request, "admin/medienverwaltung.html", {
        "user": user, "is_sysadmin": has_role(user, "system_admin"),
        "orgs_for_switch": orgs_for_switch, "current_org_id": org_id,
        "rows": page_rows, "page": page, "total_pages": total_pages, "total": total,
        "storage": storage,
        "sort": sort, "dir": dir, "typ": typ, "q": q, "nur_bearbeitet": nur_bearbeitet,
        "von": von or "", "bis": bis or "",
        "typ_labels": _TYP_LABELS, "kind_labels": _KIND_LABELS,
    })


@router.post("/admin/medien/{typ}/{media_id}/loeschen")
async def medien_loeschen(
    typ: str, media_id: int, request: Request, db: Session = Depends(get_db),
    _user=Depends(require_role("admin")),
):
    user = request.state.user
    _loesche_eines(db, user, typ, media_id)
    db.commit()
    return _redirect_zurueck(request)


@router.post("/admin/medien/bulk-loeschen")
async def medien_bulk_loeschen(
    request: Request, db: Session = Depends(get_db),
    keys: list[str] = Form(default_factory=list),
    _user=Depends(require_role("admin")),
):
    user = request.state.user
    for key in keys:
        if ":" not in key:
            continue
        typ, _, raw_id = key.partition(":")
        try:
            media_id = int(raw_id)
        except ValueError:
            continue
        _loesche_eines(db, user, typ, media_id)
    db.commit()
    return _redirect_zurueck(request)


def _loesche_eines(db: Session, user, typ: str, media_id: int) -> None:
    """Loescht eine Medienzeile — nur fuer die drei Typen mit fertiger, quota-
    korrekter Loeschfunktion (Task/Message/Person). Fuer GSL-Medientypen existiert
    dafuer heute keine generische Funktion (siehe annotation_service-Recherche);
    diese bleiben schreibgeschuetzt und werden hier ignoriert."""
    from app.models.incident import Incident, MessageMedia, PersonMedia, TaskMedia
    from app.services.media_service import delete_media

    model = {"task": TaskMedia, "message": MessageMedia, "person": PersonMedia}.get(typ)
    if model is None:
        return
    media = db.get(model, media_id)
    if media is None:
        return
    incident = db.get(Incident, media.incident_id)
    if not incident:
        return
    if not (has_role(user, "system_admin") or (user.org_id and incident.primary_org_id == user.org_id)):
        return
    delete_media(media, db)


def _redirect_zurueck(request: Request) -> RedirectResponse:
    raw_referer = request.headers.get("referer", "")
    from urllib.parse import urlparse
    p = urlparse(raw_referer)
    safe_redirect = raw_referer if (not p.scheme and not p.netloc) else "/admin/medien"
    return RedirectResponse(safe_redirect, status_code=303)
