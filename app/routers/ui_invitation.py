"""UI-Router: Org-übergreifende Einladungen für Einsätze."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentOrg
from app.models.invitation import OrgInvitation, OrgPartner
from app.models.master import FireDept

router = APIRouter()


def _inv_or_404(inv_id: int, db: Session) -> OrgInvitation:
    inv = db.get(OrgInvitation, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    return inv


# ── Einladung erstellen ───────────────────────────────────────────────────────

@router.post("/einsatz/{incident_id}/einladen", response_class=HTMLResponse)
async def invite_org(
    request: Request,
    incident_id: int,
    invited_org_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin", "incident_leader")),
):
    user = request.state.user
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    if not same_org_or_system_admin(user, incident.primary_org_id):
        raise HTTPException(403, "Nur die führende Org darf Einladungen versenden")

    invited_org = db.get(FireDept, invited_org_id)
    if not invited_org:
        raise HTTPException(404, "Ziel-Organisation nicht gefunden")
    if invited_org_id == incident.primary_org_id:
        raise HTTPException(400, "Die eigene Organisation kann nicht eingeladen werden")

    # Prüfe ob bereits eingeladen (und nicht revoked/declined)
    existing = db.query(OrgInvitation).filter(
        OrgInvitation.incident_id == incident_id,
        OrgInvitation.invited_org_id == invited_org_id,
        OrgInvitation.status.in_(["pending", "accepted"]),
    ).first()
    if existing:
        raise HTTPException(400, "Diese Organisation ist bereits eingeladen oder beteiligt")

    inv = OrgInvitation(
        incident_id=incident_id,
        inviting_org_id=incident.primary_org_id,
        invited_org_id=invited_org_id,
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(inv)
    db.commit()

    # WebSocket-Benachrichtigung an die eingeladene Org
    from app.services.broadcast import broadcast_org
    await broadcast_org(invited_org_id, {
        "type": "invitation_received",
        "invitation_id": inv.id,
        "incident_id": incident_id,
        "inviting_org": user.org.name if user.org else str(incident.primary_org_id),
    })

    invitations = _load_invitations(db, incident_id)
    all_orgs = db.query(FireDept).filter(
        FireDept.is_active == True,  # noqa: E712
        FireDept.id != incident.primary_org_id,
    ).all()
    return templates.TemplateResponse(request, "incident/_invitations.html", {
        "user": user,
        "incident": incident,
        "invitations": invitations,
        "all_orgs": all_orgs,
        "can_invite": True,
    })


# ── Einladung annehmen ────────────────────────────────────────────────────────

@router.post("/einladungen/{inv_id}/annehmen", response_class=HTMLResponse)
async def accept_invitation(
    request: Request,
    inv_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    inv = _inv_or_404(inv_id, db)

    if not same_org_or_system_admin(user, inv.invited_org_id):
        raise HTTPException(403, "Nur die eingeladene Org kann annehmen")
    if inv.status != "pending":
        raise HTTPException(400, f"Einladung hat Status '{inv.status}', nicht 'pending'")

    inv.status = "accepted"

    # IncidentOrg-Eintrag anlegen wenn noch nicht vorhanden
    exists = db.query(IncidentOrg).filter(
        IncidentOrg.incident_id == inv.incident_id,
        IncidentOrg.org_id == inv.invited_org_id,
    ).first()
    if not exists:
        db.add(IncidentOrg(
            incident_id=inv.incident_id,
            org_id=inv.invited_org_id,
            role="collaborator",
            added_by_user_id=user.id,
        ))

    db.commit()

    # Benachrichtigung an die einladende Org
    from app.services.broadcast import broadcast_org
    await broadcast_org(inv.inviting_org_id, {
        "type": "invitation_accepted",
        "invitation_id": inv_id,
        "incident_id": inv.incident_id,
        "accepted_by_org": inv.invited_org_id,
    })

    return _pending_invitations_partial(request, db, user)


# ── Einladung ablehnen ────────────────────────────────────────────────────────

@router.post("/einladungen/{inv_id}/ablehnen", response_class=HTMLResponse)
async def decline_invitation(
    request: Request,
    inv_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    inv = _inv_or_404(inv_id, db)

    if not same_org_or_system_admin(user, inv.invited_org_id):
        raise HTTPException(403, "Nur die eingeladene Org kann ablehnen")
    if inv.status != "pending":
        raise HTTPException(400, f"Einladung hat Status '{inv.status}', nicht 'pending'")

    inv.status = "declined"
    db.commit()

    return _pending_invitations_partial(request, db, user)


# ── Einladung widerrufen ──────────────────────────────────────────────────────

@router.post("/einladungen/{inv_id}/widerrufen", response_class=HTMLResponse)
async def revoke_invitation(
    request: Request,
    inv_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin", "incident_leader")),
):
    user = request.state.user
    inv = _inv_or_404(inv_id, db)

    if not same_org_or_system_admin(user, inv.inviting_org_id):
        raise HTTPException(403, "Nur die einladende Org kann widerrufen")
    if inv.status == "revoked":
        raise HTTPException(400, "Einladung ist bereits widerrufen")

    was_accepted = inv.status == "accepted"
    inv.status = "revoked"

    # Wenn bereits angenommen: IncidentOrg-Eintrag entfernen
    if was_accepted:
        db.query(IncidentOrg).filter(
            IncidentOrg.incident_id == inv.incident_id,
            IncidentOrg.org_id == inv.invited_org_id,
        ).delete()

    db.commit()

    invitations = _load_invitations(db, inv.incident_id)
    incident = db.get(Incident, inv.incident_id)
    all_orgs = db.query(FireDept).filter(
        FireDept.is_active == True,  # noqa: E712
        FireDept.id != (incident.primary_org_id if incident else 0),
    ).all()
    return templates.TemplateResponse(request, "incident/_invitations.html", {
        "user": user,
        "incident": incident,
        "invitations": invitations,
        "all_orgs": all_orgs,
        "can_invite": same_org_or_system_admin(user, incident.primary_org_id) if incident else False,
    })


# ── Offene Einladungen für eigene Org ────────────────────────────────────────

@router.get("/einladungen/offen", response_class=HTMLResponse)
async def pending_invitations(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    return _pending_invitations_partial(request, db, user)


# ── Partner-Org-Verwaltung ────────────────────────────────────────────────────

@router.post("/einstellungen/partner-orgs", response_class=HTMLResponse)
async def add_partner_org(
    request: Request,
    partner_org_id: int = Form(...),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    if user.org_id is None:
        raise HTTPException(403)
    if partner_org_id == user.org_id:
        raise HTTPException(400, "Eigene Org kann nicht Partner sein")

    existing = db.query(OrgPartner).filter(
        OrgPartner.org_id == user.org_id,
        OrgPartner.partner_org_id == partner_org_id,
    ).first()
    if not existing:
        db.add(OrgPartner(org_id=user.org_id, partner_org_id=partner_org_id))
        db.commit()

    return _partner_orgs_partial(request, db, user)


@router.delete("/einstellungen/partner-orgs/{partner_org_id}", response_class=HTMLResponse)
async def remove_partner_org(
    request: Request,
    partner_org_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin", "org_admin")),
):
    user = request.state.user
    if user.org_id is None:
        raise HTTPException(403)

    db.query(OrgPartner).filter(
        OrgPartner.org_id == user.org_id,
        OrgPartner.partner_org_id == partner_org_id,
    ).delete()
    db.commit()

    return _partner_orgs_partial(request, db, user)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_invitations(db: Session, incident_id: int) -> list[OrgInvitation]:
    return (
        db.query(OrgInvitation)
        .filter(OrgInvitation.incident_id == incident_id)
        .order_by(OrgInvitation.created_at.desc())
        .all()
    )


def _pending_invitations_partial(request: Request, db: Session, user) -> HTMLResponse:
    if user.org_id is None:
        invitations = []
    else:
        invitations = (
            db.query(OrgInvitation)
            .filter(
                OrgInvitation.invited_org_id == user.org_id,
                OrgInvitation.status == "pending",
            )
            .order_by(OrgInvitation.created_at.desc())
            .all()
        )
    return templates.TemplateResponse(request, "incident/_pending_invitations.html", {
        "user": user,
        "invitations": invitations,
    })


def _partner_orgs_partial(request: Request, db: Session, user) -> HTMLResponse:
    if user.org_id is None:
        partners = []
        all_orgs = []
    else:
        partners = (
            db.query(OrgPartner)
            .filter(OrgPartner.org_id == user.org_id)
            .all()
        )
        partner_ids = {p.partner_org_id for p in partners}
        all_orgs = db.query(FireDept).filter(
            FireDept.is_active == True,  # noqa: E712
            FireDept.id != user.org_id,
            ~FireDept.id.in_(partner_ids),
        ).all()
    return templates.TemplateResponse(request, "settings/_partner_orgs.html", {
        "user": user,
        "partners": partners,
        "available_orgs": all_orgs,
    })
