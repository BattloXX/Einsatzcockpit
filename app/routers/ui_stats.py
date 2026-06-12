"""Statistik-Dashboard."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.permissions import has_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident, IncidentOrg

router = APIRouter()


def _apply_org_scope(q, user, db: Session):
    """Fügt expliziten Org-Filter zu Aggregate-Queries hinzu.

    with_loader_criteria greift nicht bei COUNT/GROUP-BY-Queries (Kategorie C),
    daher muss der Filter hier manuell gesetzt werden.
    """
    if has_role(user, "system_admin"):
        return q
    if not user.org_id:
        return q.filter(False)
    collab_subq = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == user.org_id)
    return q.filter(
        or_(
            Incident.primary_org_id == user.org_id,
            Incident.id.in_(collab_subq),
        )
    )


@router.get("/statistik", response_class=HTMLResponse)
async def stats(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    base = lambda q: _apply_org_scope(q, user, db)  # noqa: E731

    # Total incidents (excluding exercises)
    total = base(
        db.query(func.count(Incident.id)).filter(Incident.is_exercise == False)  # noqa: E712
    ).scalar()
    total_exercises = base(
        db.query(func.count(Incident.id)).filter(Incident.is_exercise == True)  # noqa: E712
    ).scalar()

    # Per alarm type
    by_alarm = base(
        db.query(Incident.alarm_type_code, func.count(Incident.id))
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by(Incident.alarm_type_code)
    ).all()

    # Per month (last 12 months)
    by_month = base(
        db.query(
            func.date_format(Incident.started_at, "%Y-%m").label("month"),
            func.count(Incident.id).label("count"),
        )
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by("month")
        .order_by("month")
        .limit(12)
    ).all()

    return templates.TemplateResponse(request, "stats/dashboard.html", {
        "user": user,
        "total": total, "total_exercises": total_exercises,
        "by_alarm": by_alarm, "by_month": by_month,
    })
