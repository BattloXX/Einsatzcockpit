"""Uebersicht der Planspiel-Laeufe."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.permissions import PLANSPIEL_LEITER_ROLES, PLANSPIEL_LESE_ROLLEN, has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.planspiel import PLANSPIEL_STATUS_LABELS, PlanspielMitglied, PlanspielRun
from app.models.user import User

router = APIRouter(prefix="/planspiele", tags=["planspiele"])

_STATUS_GRUPPEN = {
    "entwuerfe": ("Entwürfe", ("entwurf", "bereit")),
    "laufend": ("Laufend", ("laeuft", "pausiert", "generierung_gestoppt")),
    "beendet": ("Beendet", ("beendet", "abgebrochen")),
}


def require_planspiel_enabled(request: Request) -> None:
    if not getattr(request.state, "planspiel_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _runs_for_user(db: Session, user: User) -> dict[str, list[PlanspielRun]]:
    if user.org_id is None:
        return {key: [] for key in _STATUS_GRUPPEN}

    query = db.query(PlanspielRun).filter(PlanspielRun.org_id == user.org_id)
    if not has_role(user, *PLANSPIEL_LEITER_ROLES):
        query = query.join(PlanspielMitglied, PlanspielMitglied.run_id == PlanspielRun.id).filter(
            PlanspielMitglied.user_id == user.id
        )
    runs = query.order_by(PlanspielRun.created_at.desc()).all()
    return {
        key: [run for run in runs if run.status in statuses]
        for key, (_, statuses) in _STATUS_GRUPPEN.items()
    }


def _leiter_namen(db: Session, groups: dict[str, list[PlanspielRun]]) -> dict[int, str]:
    ids = {run.leiter_user_id for runs in groups.values() for run in runs if run.leiter_user_id is not None}
    if not ids:
        return {}
    return {
        user.id: user.display_name or user.username
        for user in db.query(User).filter(User.id.in_(ids)).all()
    }


@router.get("", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*PLANSPIEL_LESE_ROLLEN)),
    _guard: None = Depends(require_planspiel_enabled),
):
    groups = _runs_for_user(db, user)
    context = {
        "user": user,
        "gruppen": _STATUS_GRUPPEN,
        "runs": groups,
        "status_labels": PLANSPIEL_STATUS_LABELS,
        "leiter_namen": _leiter_namen(db, groups),
        "can_leiten": has_role(user, *PLANSPIEL_LEITER_ROLES),
    }
    template = "planspiel/_liste.html" if request.headers.get("HX-Request") == "true" else "planspiel/index.html"
    return templates.TemplateResponse(request, template, context)
