"""Atemschutzüberwachung – Rückzugsdruck-Berechnung und Status-Maschine."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.audit import write_incident_change
from app.models.breathing import TROOP_STATUSES, BreathingTroop, PressureLog, TroopMember


def _now() -> datetime:
    return datetime.now(UTC)


def calc_withdraw_pressure(start_press: float, factor: float = 0.5, reserve: int = 10) -> float:
    return round(start_press * factor + reserve, 1)


def create_troop(
    db: Session,
    incident_id: int,
    name: str,
    members_data: list[dict],
    task_text: str | None = None,
    vehicle_id: int | None = None,
    user_id: int | None = None,
) -> BreathingTroop:
    """
    members_data: [{"member_id": int|None, "free_text_name": str|None,
                    "role": "truppfuehrer"|"truppmann", "start_press": float}]
    """
    troop = BreathingTroop(
        incident_id=incident_id,
        name=name,
        status="bereit",
        task_text=task_text,
        vehicle_id=vehicle_id,
    )
    db.add(troop)
    db.flush()

    for md in members_data:
        m = TroopMember(
            troop_id=troop.id,
            member_id=md.get("member_id"),
            free_text_name=md.get("free_text_name"),
            role=md.get("role", "truppmann"),
            start_press=md.get("start_press"),
        )
        db.add(m)
    db.flush()

    write_incident_change(
        db, incident_id, "troop.created", "breathing_troop", troop.id,
        before=None, after={"name": name, "members": len(members_data)},
        user_id=user_id,
    )
    return troop


def start_troop(
    db: Session,
    troop: BreathingTroop,
    user_id: int | None = None,
) -> BreathingTroop:
    before = {"status": troop.status}

    # Calculate average start pressure from members
    pressures = [m.start_press for m in troop.members if m.start_press]
    if pressures:
        avg = sum(pressures) / len(pressures)
        troop.start_press_avg = avg

        # Get dept settings for withdraw calculation
        from app.models.incident import Incident
        from app.models.master import FireDept
        incident = db.get(Incident, troop.incident_id)
        dept = db.query(FireDept).filter(FireDept.slug == "wolfurt").first()
        factor = dept.withdraw_press_factor if dept else 0.5
        reserve = dept.withdraw_press_reserve if dept else 10
        troop.withdraw_press_calc = calc_withdraw_pressure(avg, factor, reserve)

        # Set individual withdraw pressures
        for m in troop.members:
            if m.start_press:
                m.withdraw_press = calc_withdraw_pressure(m.start_press, factor, reserve)

    troop.status = "im_einsatz"
    troop.entry_at = _now()
    db.flush()

    write_incident_change(
        db, troop.incident_id, "troop.started", "breathing_troop", troop.id,
        before=before,
        after={"status": "im_einsatz", "entry_at": troop.entry_at.isoformat(),
               "withdraw_press_calc": troop.withdraw_press_calc},
        user_id=user_id,
    )
    return troop


def update_troop_status(
    db: Session,
    troop: BreathingTroop,
    new_status: str,
    user_id: int | None = None,
) -> BreathingTroop:
    assert new_status in TROOP_STATUSES
    before = {"status": troop.status}
    troop.status = new_status
    if new_status == "rueckzug":
        troop.withdraw_at = _now()
    elif new_status == "zurueck":
        troop.back_at = _now()
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.status_changed", "breathing_troop", troop.id,
        before=before, after={"status": new_status},
        user_id=user_id,
    )
    return troop


def log_pressure(
    db: Session,
    troop: BreathingTroop,
    member_id: int | None,
    pressure_bar: float,
    recorded_by_user_id: int | None = None,
) -> PressureLog:
    log = PressureLog(
        troop_id=troop.id,
        member_id=member_id,
        pressure_bar=pressure_bar,
        recorded_by_user_id=recorded_by_user_id,
    )
    db.add(log)
    db.flush()
    return log


def get_warning_level(troop: BreathingTroop) -> str:
    """Returns 'ok', 'yellow' (75%), or 'red' (at/below withdraw pressure)."""
    low = troop.lowest_current_pressure
    if low is None or troop.start_press_avg is None:
        return "ok"
    if troop.withdraw_press_calc and low <= troop.withdraw_press_calc:
        return "red"
    if troop.start_press_avg and low <= troop.start_press_avg * 0.75:
        return "yellow"
    return "ok"
