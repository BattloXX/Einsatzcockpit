"""Zugriffsregeln fuer den Allein-Betrieb in Planspielen.

Bewusst ohne Rechteausweitung: Wer simulierte Einheiten bedienen will, braucht
weiterhin Einsatzleiter-Rechte (bzw. Admin). Zusaetzlich zur Rolle wird hier nur
geprueft, dass die Einheit zu einer laufenden Planspiel-Uebungslage gehoert, in
der der Benutzer Leiter oder Regie ist.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.permissions import PLANSPIEL_LEITER_ROLES, has_role
from app.models.major_incident import LageEinheit, MajorIncident
from app.models.planspiel import PlanspielMitglied, PlanspielRun

_AKTIV = ("laeuft", "pausiert", "generierung_gestoppt")
EINSATZ_ROLLEN = ("incident_leader", "admin", "org_admin")


def hat_einsatzrechte(user: Any) -> bool:
    """Board, Disposition und Einheiten-Simulation verlangen Einsatzleiter- oder Admin-Rechte."""
    return has_role(user, *EINSATZ_ROLLEN)


def ist_regie(db: Session, user: Any, run: PlanspielRun) -> bool:
    if has_role(user, *PLANSPIEL_LEITER_ROLES):
        return True
    return db.query(PlanspielMitglied.id).filter(
        PlanspielMitglied.run_id == run.id,
        PlanspielMitglied.org_id == run.org_id,
        PlanspielMitglied.user_id == user.id,
        PlanspielMitglied.rolle.in_(("leiter", "regie")),
    ).first() is not None


def simulation_erlaubt(db: Session, user: Any, einheit_id: int) -> bool:
    """Einsatzleiter darf Einheiten seiner laufenden Planspiel-Uebungslage simulieren."""
    if not has_role(user, "incident_leader"):
        return False
    einheit = db.get(LageEinheit, einheit_id)
    if einheit is None:
        return False
    lage = db.get(MajorIncident, einheit.lage_id)
    if lage is None or not lage.is_exercise or lage.planspiel_run_id is None or lage.org_id != user.org_id:
        return False
    run = db.query(PlanspielRun).filter(
        PlanspielRun.id == lage.planspiel_run_id, PlanspielRun.org_id == lage.org_id,
    ).one_or_none()
    if run is None or run.status not in _AKTIV:
        return False
    return ist_regie(db, user, run)
