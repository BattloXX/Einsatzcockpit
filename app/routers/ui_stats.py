"""Statistik-Dashboard."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.incident import Incident

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/statistik", response_class=HTMLResponse)
async def stats(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse("/login", status_code=302)

    # Total incidents (excluding exercises)
    total = db.query(func.count(Incident.id)).filter(Incident.is_exercise == False).scalar()  # noqa: E712
    total_exercises = db.query(func.count(Incident.id)).filter(Incident.is_exercise == True).scalar()  # noqa: E712

    # Per alarm type
    by_alarm = (
        db.query(Incident.alarm_type_code, func.count(Incident.id))
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by(Incident.alarm_type_code)
        .all()
    )

    # Per month (last 12 months)
    by_month = (
        db.query(
            func.date_format(Incident.started_at, "%Y-%m").label("month"),
            func.count(Incident.id).label("count"),
        )
        .filter(Incident.is_exercise == False)  # noqa: E712
        .group_by("month")
        .order_by("month")
        .limit(12)
        .all()
    )

    return templates.TemplateResponse(request, "stats/dashboard.html", {
        "user": user,
        "total": total, "total_exercises": total_exercises,
        "by_alarm": by_alarm, "by_month": by_month,
    })
