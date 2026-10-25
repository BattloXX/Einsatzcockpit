"""Übersicht auf bestehenden, rechtegeprüften Plänen und Freigaben."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.katplan import KatplanNutzung, KatplanPlan, _utcnow
from app.models.user import User


def merke_zugriff(db: Session, user: User, plan: KatplanPlan) -> None:
    if user.org_id != plan.org_id:
        raise ValueError("Plan nicht verfügbar.")
    query = db.query(KatplanNutzung).filter_by(org_id=user.org_id, user_id=user.id, plan_id=plan.id)
    jetzt = _utcnow()
    if not query.update({"zuletzt_am": jetzt}):
        try:
            with db.begin_nested():
                db.add(KatplanNutzung(org_id=user.org_id, user_id=user.id, plan_id=plan.id, zuletzt_am=jetzt))
                db.flush()
        except IntegrityError:
            # Zwei gleichzeitige Tabs dürfen denselben Zugriff anlegen.
            query.update({"zuletzt_am": jetzt})


def zuletzt_verwendet(db: Session, user: User, sichtbar: list[dict]) -> list[dict]:
    """Titel und Berechtigungen bei jedem Abruf neu aus der sichtbaren Fassung beziehen."""
    nach_id = {p["id"]: p for p in sichtbar}
    if not nach_id:
        return []
    nutzungen = db.query(KatplanNutzung).filter(
        KatplanNutzung.org_id == user.org_id, KatplanNutzung.user_id == user.id,
        KatplanNutzung.plan_id.in_(nach_id),
    ).order_by(KatplanNutzung.zuletzt_am.desc(), KatplanNutzung.id.desc()).limit(8)
    return [nach_id[n.plan_id] for n in nutzungen]
