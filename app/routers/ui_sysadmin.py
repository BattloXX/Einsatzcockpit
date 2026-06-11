"""PR 11 – System-Admin-Konsole.

Route:
  GET /admin/system/orgs  – Per-Org KPI-Tabelle (system_admin only)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.master import FireDept, Member
from app.models.user import ApiKey, User

router = APIRouter(prefix="/admin")


def _org_stats(db: Session) -> list[dict]:
    """Aggregate per-org KPIs for the system-admin console."""
    orgs = db.query(FireDept).order_by(FireDept.name).all()

    active_inc = {
        r.primary_org_id: r.cnt
        for r in db.query(Incident.primary_org_id, func.count().label("cnt"))
        .filter(Incident.status == "active")
        .group_by(Incident.primary_org_id)
        .all()
    }
    total_inc = {
        r.primary_org_id: r.cnt
        for r in db.query(Incident.primary_org_id, func.count().label("cnt"))
        .group_by(Incident.primary_org_id)
        .all()
    }
    last_inc = {
        r.primary_org_id: r.last
        for r in db.query(
            Incident.primary_org_id, func.max(Incident.started_at).label("last")
        )
        .group_by(Incident.primary_org_id)
        .all()
    }
    user_cnt = {
        r.org_id: r.cnt
        for r in db.query(User.org_id, func.count().label("cnt"))
        .filter(User.active == True, User.org_id.isnot(None))  # noqa: E712
        .group_by(User.org_id)
        .all()
    }
    member_cnt = {
        r.org_id: r.cnt
        for r in db.query(Member.org_id, func.count().label("cnt"))
        .filter(Member.active == True)  # noqa: E712
        .group_by(Member.org_id)
        .all()
    }
    apikey_cnt = {
        r.org_id: r.cnt
        for r in db.query(ApiKey.org_id, func.count().label("cnt"))
        .filter(ApiKey.revoked_at.is_(None))
        .group_by(ApiKey.org_id)
        .all()
    }

    return [
        {
            "org": org,
            "active_incidents": active_inc.get(org.id, 0),
            "total_incidents": total_inc.get(org.id, 0),
            "last_incident_at": last_inc.get(org.id),
            "users": user_cnt.get(org.id, 0),
            "members": member_cnt.get(org.id, 0),
            "api_keys": apikey_cnt.get(org.id, 0),
        }
        for org in orgs
    ]


@router.get("/system/orgs", response_class=HTMLResponse)
async def sysadmin_orgs(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    return templates.TemplateResponse(
        request,
        "admin/sysadmin_orgs.html",
        {
            "user": request.state.user,
            "is_sysadmin": True,
            "rows": _org_stats(db),
        },
    )
