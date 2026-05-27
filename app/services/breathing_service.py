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
    unit_name: str | None = None,
    location_text: str | None = None,
    planned_duration_min: int | None = None,
    bottle_preset: str | None = None,
    user_id: int | None = None,
) -> BreathingTroop:
    """
    members_data: [{"member_id": int|None, "free_text_name": str|None,
                    "role": "truppfuehrer"|"truppmann", "start_press": float}]
    """
    troop = BreathingTroop(
        incident_id=incident_id,
        name=name,
        unit_name=unit_name,
        status="bereit",
        task_text=task_text,
        vehicle_id=vehicle_id,
        location_text=location_text,
        planned_duration_min=planned_duration_min,
        bottle_preset=bottle_preset,
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

        # Get dept settings for withdraw calculation via primary_org_id
        from app.models.incident import Incident
        from app.models.master import FireDept
        incident = db.get(Incident, troop.incident_id)
        dept = db.get(FireDept, incident.primary_org_id) if (incident and incident.primary_org_id) else None
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
    note: str | None = None,
    recorded_by_user_id: int | None = None,
) -> PressureLog:
    now = _now()
    log = PressureLog(
        troop_id=troop.id,
        member_id=member_id,
        pressure_bar=pressure_bar,
        note=note or None,
        recorded_by_user_id=recorded_by_user_id,
    )
    db.add(log)
    # Jede Druckmeldung gilt als Lagemeldung (Leitfaden: "Lage- und Flaschendruck-meldung")
    troop.last_meldung_at = now
    if note:
        troop.last_meldung_text = note
    # 1/3-Warnung nach neuer Meldung zurücksetzen, damit sie bei Bedarf erneut ausgelöst wird
    troop.warn_one_third_acked_at = None
    db.flush()
    return log


def update_meldung(
    db: Session,
    troop: BreathingTroop,
    text: str | None,
    user_id: int | None = None,
) -> None:
    """Setzt letzte Lagemeldung (Zeitpunkt + Text) ohne Druckprotokoll."""
    troop.last_meldung_at = _now()
    troop.last_meldung_text = text or None
    # 1/3-Warnung zurücksetzen
    troop.warn_one_third_acked_at = None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.meldung", "breathing_troop", troop.id,
        before=None, after={"text": text},
        user_id=user_id,
    )


def ack_warning(
    db: Session,
    troop: BreathingTroop,
    kind: str,
    user_id: int | None = None,
) -> None:
    """Quittiert eine Warnung. kind ∈ {"one_third", "max_time", "withdraw"}."""
    now = _now()
    if kind == "one_third":
        troop.warn_one_third_acked_at = now
    elif kind == "max_time":
        troop.warn_max_time_acked_at = now
    elif kind == "withdraw":
        troop.warn_withdraw_acked_at = now
    db.flush()
    write_incident_change(
        db, troop.incident_id, f"troop.warn_acked.{kind}", "breathing_troop", troop.id,
        before=None, after={"kind": kind},
        user_id=user_id,
    )


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


def get_time_warning(troop: BreathingTroop) -> str:
    """
    Returns 'ok' | 'one_third_due' | 'max_exceeded'.

    Trigger-Regeln (Leitfaden ASÜW / FwDV 7):
      one_third_due: 1/3 der Einsatzzeit verstrichen UND seit Einsatzbeginn
                     keine Lagemeldung eingegangen UND nicht quittiert.
      max_exceeded:  Maximale Einsatzzeit überschritten UND nicht quittiert.
    """
    if troop.entry_at is None or troop.status not in ("im_einsatz", "rueckzug"):
        return "ok"

    now = _now()
    entry = troop.entry_at if troop.entry_at.tzinfo else troop.entry_at.replace(tzinfo=UTC)
    elapsed = (now - entry).total_seconds()

    # Max-Zeit zuerst prüfen (schwerwiegender)
    if troop.max_seconds and elapsed >= troop.max_seconds:
        if troop.warn_max_time_acked_at is None:
            return "max_exceeded"

    # 1/3-Warnung: keine Meldung seit Einsatzbeginn
    if troop.one_third_seconds and elapsed >= troop.one_third_seconds:
        last = troop.last_meldung_at
        if last:
            last = last if last.tzinfo else last.replace(tzinfo=UTC)
            meldung_after_start = last >= entry
        else:
            meldung_after_start = False

        if not meldung_after_start and troop.warn_one_third_acked_at is None:
            return "one_third_due"

    return "ok"


def check_troop_warnings(troop: BreathingTroop) -> list[str]:
    """Gibt alle aktuell aktiven Warnstufen zurück (für Watchdog-Task)."""
    warnings = []
    pressure_warn = get_warning_level(troop)
    if pressure_warn == "red" and troop.warn_withdraw_acked_at is None:
        warnings.append("withdraw")
    time_warn = get_time_warning(troop)
    if time_warn != "ok":
        warnings.append(time_warn)
    return warnings


async def _breathing_watchdog_loop() -> None:
    """Prüft alle 5 Sekunden alle laufenden Trupps und broadcastet Warnungen."""
    import asyncio
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.services.broadcast import manager

    # Tracking bereits gesendeter Warns (troop_id → set[kind])
    # verhindert dauerhaftes Re-Senden ohne Zustandsänderung
    _sent: dict[int, set[str]] = {}

    while True:
        try:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                active_incidents = db.query(Incident).filter(Incident.status == "active").all()
                for incident in active_incidents:
                    db.refresh(incident, ["breathing_troops"])
                    for troop in incident.breathing_troops:
                        if troop.status not in ("im_einsatz", "rueckzug"):
                            _sent.pop(troop.id, None)
                            continue
                        active_warnings = set(check_troop_warnings(troop))
                        prev_warnings = _sent.get(troop.id, set())
                        new_warnings = active_warnings - prev_warnings
                        cleared = prev_warnings - active_warnings
                        for kind in new_warnings:
                            await manager.broadcast(incident.id, {
                                "type": "troop_warning",
                                "troop_id": troop.id,
                                "kind": kind,
                            })
                        _sent[troop.id] = active_warnings
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Watchdog darf nie crashen
