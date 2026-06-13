"""GSL-Stab: SKKM-Besetzungstafel, Besetzungs-CRUD, Ablöse, Soll-Check (HTMX-Partials)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import (
    JOURNAL_CATEGORIES,
    GslStaffAssignment,
    LageJournalEntry,
    MajorIncident,
    MajorIncidentStatus,
)
from app.services import gsl_staff_service as svc
from app.services.broadcast import broadcast_lage

router = APIRouter()


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org(user, lage: MajorIncident):
    if user.org_id != lage.org_id:
        raise HTTPException(status_code=403, detail="Kein Zugriff")


def _can_edit(user) -> bool:
    return has_role(user, "incident_leader", "admin", "org_admin", "recorder")


def _parse_datetime(val: str | None) -> datetime:
    """Parst ISO-Datetime-String, fällt auf jetzt zurück."""
    if val:
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass
    return datetime.now(UTC)


async def _broadcast_staff(lage_id: int):
    await broadcast_lage(lage_id, {"type": "staff:changed", "reload_board": False})


# ── Besetzungstafel (HTMX-Partial) ────────────────────────────────────────────

@router.get("/lage/{lage_id}/stab/tafel", response_class=HTMLResponse)
async def stab_tafel(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    check = svc.soll_check(db, lage_id, lage.org_id)
    roles = svc.get_roles(db, lage.org_id)

    return templates.TemplateResponse(request, "incident_major/_stab_tafel.html", {
        "lage": lage,
        "check": check,
        "roles": roles,
        "can_edit": _can_edit(user),
    })


# ── Besetzung anlegen ──────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stab/besetzen")
async def stab_besetzen(
    request: Request,
    lage_id: int,
    role_id: int = Form(...),
    person_name: str = Form(""),
    member_id: int | None = Form(None),
    is_lead: bool = Form(True),
    start_at: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)
    if lage.status != MajorIncidentStatus.active:
        raise HTTPException(status_code=400, detail="Lage nicht aktiv")

    pname = person_name.strip()[:120] or None
    nt = note.strip()[:2000] or None

    try:
        asgn = svc.assign(
            db=db,
            incident_id=lage_id,
            org_id=lage.org_id,
            role_id=role_id,
            person_name=pname,
            member_id=member_id,
            is_lead=is_lead,
            start_at=_parse_datetime(start_at),
            note=nt,
            created_by=user.id,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit(db, action="gsl_staff_assign", org_id=lage.org_id, user_id=user.id,
                entity_type="gsl_staff_assignment", entity_id=asgn.id,
                payload={"role_id": role_id, "person": pname or member_id})
    await _broadcast_staff(lage_id)
    return Response(status_code=204)


# ── Ablöse ─────────────────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stab/{asgn_id}/abloesen")
async def stab_abloesen(
    request: Request,
    lage_id: int,
    asgn_id: int,
    person_name: str = Form(""),
    member_id: int | None = Form(None),
    at: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    pname = person_name.strip()[:120] or None
    nt = note.strip()[:2000] or None

    try:
        new_asgn = svc.replace_assignment(
            db=db,
            old_id=asgn_id,
            incident_id=lage_id,
            org_id=lage.org_id,
            person_name=pname,
            member_id=member_id,
            at=_parse_datetime(at),
            note=nt,
            created_by=user.id,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit(db, action="gsl_staff_replace", org_id=lage.org_id, user_id=user.id,
                entity_type="gsl_staff_assignment", entity_id=new_asgn.id,
                payload={"replaced": asgn_id, "person": pname or member_id})
    await _broadcast_staff(lage_id)
    return Response(status_code=204)


# ── Besetzung beenden ──────────────────────────────────────────────────────────

@router.post("/lage/{lage_id}/stab/{asgn_id}/beenden")
async def stab_beenden(
    request: Request,
    lage_id: int,
    asgn_id: int,
    at: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    try:
        svc.end_assignment(db=db, asgn_id=asgn_id, incident_id=lage_id, at=_parse_datetime(at))
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit(db, action="gsl_staff_end", org_id=lage.org_id, user_id=user.id,
                entity_type="gsl_staff_assignment", entity_id=asgn_id)
    await _broadcast_staff(lage_id)
    return Response(status_code=204)


# ── Notiz/Zeit korrigieren ────────────────────────────────────────────────────

@router.patch("/lage/{lage_id}/stab/{asgn_id}")
async def stab_patch(
    request: Request,
    lage_id: int,
    asgn_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    asgn = db.get(GslStaffAssignment, asgn_id)
    if not asgn or asgn.incident_id != lage_id:
        raise HTTPException(status_code=404)

    data = await request.json()
    if "note" in data:
        asgn.note = (data["note"] or "").strip()[:2000] or None
    if "start_at" in data:
        asgn.start_at = _parse_datetime(data["start_at"])
    if "end_at" in data:
        val = data["end_at"]
        asgn.end_at = _parse_datetime(val) if val else None
    if "person_name" in data:
        asgn.person_name = (data["person_name"] or "").strip()[:120] or None

    db.commit()
    await _broadcast_staff(lage_id)
    return Response(status_code=204)


# ── Einsatzjournal-Partial (HTMX live-reload) ─────────────────────────────────

@router.get("/lage/{lage_id}/stab/einsatzjournal", response_class=HTMLResponse)
async def stab_einsatzjournal(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    entries = (
        db.query(LageJournalEntry)
        .filter(LageJournalEntry.major_incident_id == lage_id)
        .order_by(LageJournalEntry.ts.desc())
        .all()
    )
    return templates.TemplateResponse(request, "incident_major/_stab_einsatzjournal.html", {
        "lage": lage,
        "journal_entries": entries,
        "journal_categories": JOURNAL_CATEGORIES,
        "can_edit": _can_edit(user),
    })


# ── Personenjournal-API ────────────────────────────────────────────────────────

@router.get("/lage/{lage_id}/stab/journal", response_class=HTMLResponse)
async def stab_journal(
    request: Request,
    lage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("incident_leader", "admin", "org_admin", "recorder", "readonly")),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org(user, lage)

    assignments = (
        db.query(GslStaffAssignment)
        .filter(GslStaffAssignment.incident_id == lage_id)
        .order_by(GslStaffAssignment.start_at)
        .all()
    )
    roles = {r.id: r for r in svc.get_roles(db, lage.org_id)}

    return templates.TemplateResponse(request, "incident_major/_stab_journal.html", {
        "lage": lage,
        "assignments": assignments,
        "roles": roles,
        "can_edit": _can_edit(user),
    })
