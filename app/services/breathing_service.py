"""Atemschutzüberwachung – Rückzugsdruck-Berechnung und Status-Maschine."""
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.core.audit import write_incident_change
from app.models.breathing import (
    BOTTLE_PRESET_NOMINAL_BAR,
    TROOP_STATUSES,
    BreathingTroop,
    PressureLog,
    TroopMember,
)


def _now() -> datetime:
    return datetime.now(UTC)


def calc_withdraw_pressure(start_press: float, factor: float = 0.5, reserve: int = 10) -> float:
    return round(start_press * factor + reserve, 1)


class PressureWarning(NamedTuple):
    """Ungünstigster Druckstatus samt auslösendem Truppmitglied."""

    level: str
    member: TroopMember | None


class ReadinessWarning(Exception):
    """Einsatzbereitschaft ist eingeschränkt, kann aber begründet übersteuert werden."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def _withdraw_settings(db: Session, troop: BreathingTroop) -> tuple[float, int]:
    """Lädt die vorläufige Rückzugsdruck-Konfiguration der primären Organisation."""
    from app.models.incident import Incident
    from app.models.master import FireDept

    incident = db.get(Incident, troop.incident_id)
    dept = db.get(FireDept, incident.primary_org_id) if (incident and incident.primary_org_id) else None
    return (
        dept.withdraw_press_factor if dept else 0.5,
        dept.withdraw_press_reserve if dept else 10,
    )


def check_start_readiness(db: Session, troop: BreathingTroop) -> list[str]:
    """Prüft Sicherheitstrupp und Mindestfülldruck vor dem Einsetzen nach FwDV 7."""
    issues: list[str] = []
    if not troop.is_sicherheitstrupp:
        standby = (
            db.query(BreathingTroop)
            .filter(
                BreathingTroop.incident_id == troop.incident_id,
                BreathingTroop.id != troop.id,
                BreathingTroop.is_sicherheitstrupp.is_(True),
                BreathingTroop.status.in_(("bereit", "im_einsatz")),
            )
            .first()
        )
        if standby is None:
            issues.append("Kein bereiter Sicherheitstrupp für diesen Einsatz vorhanden.")

    nominal_bar = BOTTLE_PRESET_NOMINAL_BAR.get(troop.bottle_preset or "")
    if nominal_bar is not None:
        minimum = nominal_bar * 0.9
        for member in troop.members:
            if member.start_press is not None and member.start_press < minimum:
                issues.append(
                    f"{member.display_name}: Anfangsdruck {member.start_press:g} bar liegt unter "
                    f"90 % des Nominalfülldrucks ({minimum:g} bar)."
                )
    return issues


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
    is_sicherheitstrupp: bool = False,
    user_id: int | None = None,
) -> BreathingTroop:
    """
    members_data: [{"member_id": int|None, "free_text_name": str|None,
                    "role": "truppfuehrer"|"truppmann", "start_press": float}]
    """
    filled_members = [
        md for md in members_data
        if md.get("member_id") or str(md.get("free_text_name") or "").strip()
    ]
    if len(filled_members) < 2:
        raise ValueError("Ein Atemschutztrupp benötigt mindestens 2 Mitglieder.")

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
        is_sicherheitstrupp=is_sicherheitstrupp,
    )
    db.add(troop)
    db.flush()

    for md in filled_members:
        m = TroopMember(
            troop_id=troop.id,
            member_id=md.get("member_id"),
            free_text_name=md.get("free_text_name"),
            role=md.get("role", "truppmann"),
            start_press=md.get("start_press"),
            current_press=md.get("start_press"),
        )
        db.add(m)
    db.flush()

    write_incident_change(
        db, incident_id, "troop.created", "breathing_troop", troop.id,
        before=None, after={"name": name, "members": len(filled_members)},
        user_id=user_id,
    )
    return troop


def start_troop(
    db: Session,
    troop: BreathingTroop,
    user_id: int | None = None,
    override_reason: str | None = None,
    override_user_id: int | None = None,
) -> BreathingTroop:
    if len(troop.members) < 2:
        raise ValueError("Ein Atemschutztrupp benötigt mindestens 2 Mitglieder.")
    issues = check_start_readiness(db, troop)
    reason = (override_reason or "").strip()
    if issues and not reason:
        raise ReadinessWarning(issues)
    before = {"status": troop.status}

    if issues:
        troop.readiness_override_reason = reason
        troop.readiness_override_by_user_id = override_user_id
        troop.readiness_override_at = _now()
        write_incident_change(
            db, troop.incident_id, "troop.readiness_overridden", "breathing_troop", troop.id,
            before=None, after={"issues": issues, "reason": reason},
            user_id=override_user_id,
        )

    # Calculate average start pressure from members
    pressures = [m.start_press for m in troop.members if m.start_press]
    if pressures:
        avg = sum(pressures) / len(pressures)
        troop.start_press_avg = avg

        factor, reserve = _withdraw_settings(db, troop)
        troop.withdraw_press_calc = calc_withdraw_pressure(avg, factor, reserve)

        # Set individual withdraw pressures
        for m in troop.members:
            if m.start_press:
                m.withdraw_press = calc_withdraw_pressure(m.start_press, factor, reserve)
                m.current_press = m.start_press

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
    if new_status not in ("im_einsatz", "rueckzug"):
        troop.escalated_at = None
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
    troop_member_id: int,
    pressure_bar: float,
    note: str | None = None,
    recorded_by_user_id: int | None = None,
) -> PressureLog:
    member = next((m for m in troop.members if m.id == troop_member_id), None)
    if member is None:
        raise ValueError("Das Truppmitglied gehört nicht zu diesem Atemschutztrupp.")
    now = _now()
    log = PressureLog(
        troop_id=troop.id,
        troop_member_id=member.id,
        member_id=member.member_id,
        pressure_bar=pressure_bar,
        note=note or None,
        recorded_by_user_id=recorded_by_user_id,
    )
    db.add(log)
    member.current_press = pressure_bar
    # Jede Druckmeldung gilt als Lagemeldung (Leitfaden: "Lage- und Flaschendruck-meldung")
    troop.last_meldung_at = now
    if note:
        troop.last_meldung_text = note
    db.flush()
    return log


def report_objective_reached(
    db: Session,
    troop: BreathingTroop,
    troop_member_id: int,
    pressure_bar: float,
    recorded_by_user_id: int | None = None,
) -> TroopMember:
    """Erfasst das Einsatzziel und berechnet den Rückzugsdruck nach FwDV 7.

    Für den Rückweg wird die doppelte beim Vormarsch verbrauchte Luftmenge
    angesetzt; die konfigurierte Mindestreserve darf nicht unterschritten werden.
    """
    member = next((m for m in troop.members if m.id == troop_member_id), None)
    if member is None or member.start_press is None:
        raise ValueError("Das Truppmitglied oder sein Anfangsdruck fehlt.")
    _, reserve = _withdraw_settings(db, troop)
    now = _now()
    member.objective_press = pressure_bar
    member.objective_press_at = now
    member.current_press = pressure_bar
    member.withdraw_press = round(max(float(reserve), 2 * pressure_bar - member.start_press), 1)
    db.add(PressureLog(
        troop_id=troop.id,
        troop_member_id=member.id,
        member_id=member.member_id,
        pressure_bar=pressure_bar,
        note="Einsatzziel erreicht",
        recorded_by_user_id=recorded_by_user_id,
        ts=now,
    ))
    troop.last_meldung_at = now
    troop.last_meldung_text = "Einsatzziel erreicht"
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.objective_reached", "troop_member", member.id,
        before=None,
        after={"pressure_bar": pressure_bar, "withdraw_press": member.withdraw_press},
        user_id=recorded_by_user_id,
    )
    return member


def report_back_pressure(
    db: Session,
    troop: BreathingTroop,
    troop_member_id: int,
    pressure_bar: float,
    recorded_by_user_id: int | None = None,
) -> TroopMember:
    """Erfasst den Enddruck eines Truppmitglieds nach der Rückkehr."""
    member = next((m for m in troop.members if m.id == troop_member_id), None)
    if member is None:
        raise ValueError("Das Truppmitglied gehört nicht zu diesem Atemschutztrupp.")
    member.back_press = pressure_bar
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.back_pressure_reported", "troop_member", member.id,
        before=None, after={"pressure_bar": pressure_bar},
        user_id=recorded_by_user_id,
    )
    return member


def update_meldung(
    db: Session,
    troop: BreathingTroop,
    text: str | None,
    user_id: int | None = None,
) -> None:
    """Setzt letzte Lagemeldung (Zeitpunkt + Text) ohne Druckprotokoll."""
    troop.last_meldung_at = _now()
    troop.last_meldung_text = text or None
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
    """Quittiert den aktuellen Warnzustand nach ASÜW-Leitfaden/FwDV 7."""
    now = _now()
    if kind == "one_third":
        troop.warn_one_third_acked_at = now
    elif kind == "two_third":
        troop.warn_two_third_acked_at = now
    elif kind == "max_time":
        troop.warn_max_time_acked_at = now
        troop.escalated_at = None
    elif kind == "withdraw":
        troop.warn_withdraw_acked_at = now
        warning = get_warning_level(troop)
        troop.warn_withdraw_acked_press = (
            warning.member.current_press if warning.member is not None else None
        )
    db.flush()
    write_incident_change(
        db, troop.incident_id, f"troop.warn_acked.{kind}", "breathing_troop", troop.id,
        before=None, after={"kind": kind},
        user_id=user_id,
    )


def get_warning_level(troop: BreathingTroop) -> PressureWarning:
    """Prüft jedes Mitglied einzeln; der ungünstigste Status bestimmt den Trupp."""
    worst = PressureWarning("ok", None)
    rank = {"ok": 0, "yellow": 1, "red": 2}
    for member in troop.members:
        current = member.current_press
        if current is None or member.start_press is None:
            continue
        if member.withdraw_press is not None and current <= member.withdraw_press:
            candidate = "red"
        elif current <= member.start_press * 0.75:
            candidate = "yellow"
        else:
            candidate = "ok"
        if rank[candidate] > rank[worst.level]:
            worst = PressureWarning(candidate, member)
    return worst


def get_time_warnings(troop: BreathingTroop) -> list[str]:
    """Gibt alle gleichzeitig aktiven Zeitwarnungen nach ASÜW-Leitfaden/FwDV 7 zurück.

    Trigger-Regeln (Leitfaden ASÜW / FwDV 7):
      one_third_due / two_third_due: jeweilige Schwelle verstrichen UND keine
                     Meldung nach genau dieser Schwelle eingegangen.
      max_exceeded:  Maximale Einsatzzeit überschritten UND nicht quittiert.
    """
    if troop.entry_at is None or troop.status not in ("im_einsatz", "rueckzug"):
        return []

    now = _now()
    entry = troop.entry_at if troop.entry_at.tzinfo else troop.entry_at.replace(tzinfo=UTC)
    elapsed = (now - entry).total_seconds()

    warnings: list[str] = []
    if troop.max_seconds and elapsed >= troop.max_seconds:
        if troop.warn_max_time_acked_at is None:
            warnings.append("max_exceeded")

    last = troop.last_meldung_at
    last_utc = (last if last.tzinfo else last.replace(tzinfo=UTC)) if last else None
    stages = (
        (troop.two_third_seconds, "two_third_due", troop.warn_two_third_acked_at),
        (troop.one_third_seconds, "one_third_due", troop.warn_one_third_acked_at),
    )
    for due_seconds, warning, acked_at in stages:
        if due_seconds and elapsed >= due_seconds:
            due_at = entry + timedelta(seconds=due_seconds)
            if (last_utc is None or last_utc < due_at) and acked_at is None:
                warnings.append(warning)

    return warnings


def get_time_warning(troop: BreathingTroop) -> str:
    """Gibt die schwerwiegendste aktive Zeitwarnung oder ``ok`` zurück."""
    warnings = get_time_warnings(troop)
    if warnings:
        return warnings[0]

    return "ok"


def check_troop_warnings(troop: BreathingTroop) -> list[str]:
    """Gibt alle aktuell aktiven Warnstufen zurück (für Watchdog-Task)."""
    warnings = []
    pressure_warn = get_warning_level(troop)
    ack_press = troop.warn_withdraw_acked_press
    trigger_press = pressure_warn.member.current_press if pressure_warn.member else None
    if pressure_warn.level == "red" and (
        troop.warn_withdraw_acked_at is None
        or (trigger_press is not None and ack_press is not None and trigger_press < ack_press)
    ):
        warnings.append("withdraw")
    warnings.extend(get_time_warnings(troop))
    return warnings


def check_and_escalate_troop(
    db: Session,
    incident,
    troop: BreathingTroop,
    grace_min: int,
    *,
    now: datetime | None = None,
    notifier: Callable | None = None,
) -> bool:
    """Eskaliert eine unquittierte Max-Zeit-Überschreitung einmalig nach ÖBFV M302."""
    if (
        troop.status not in ("im_einsatz", "rueckzug")
        or troop.entry_at is None
        or troop.max_seconds is None
        or troop.warn_max_time_acked_at is not None
        or troop.escalated_at is not None
        or incident.incident_leader_user_id is None
    ):
        return False

    current = now or _now()
    current = current if current.tzinfo else current.replace(tzinfo=UTC)
    entry = troop.entry_at if troop.entry_at.tzinfo else troop.entry_at.replace(tzinfo=UTC)
    escalation_due = entry + timedelta(seconds=troop.max_seconds, minutes=max(0, grace_min))
    if current < escalation_due:
        return False

    if notifier is None:
        from app.services.push_service import notify_user
        notifier = notify_user
    notifier(
        db,
        incident.incident_leader_user_id,
        "Atemschutz: keine Rueckmeldung",
        f"{troop.name} ueberschreitet die Einsatzzeit ohne Rueckmeldung",
        url=f"/einsatz/{incident.id}/atemschutz",
        source="breathing_watchdog",
    )
    troop.escalated_at = current
    db.flush()
    return True


async def _breathing_watchdog_loop() -> None:
    """Prüft alle 5 Sekunden alle laufenden Trupps und broadcastet Warnungen."""
    import asyncio

    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.models.master import FireDept
    from app.services.broadcast import manager

    # Tracking bereits gesendeter Warns (troop_id → set[kind])
    # verhindert dauerhaftes Re-Senden ohne Zustandsänderung
    _sent: dict[int, set[str]] = {}

    def _collect_new_warnings() -> list[tuple[int, int, str]]:
        """DB-Scan im Threadpool (Audit B2); liefert (incident_id, troop_id, kind).

        Aktualisiert _sent direkt — unkritisch, da der Loop die Aufrufe strikt
        sequenziell absetzt (kein paralleler Zugriff auf das Dict).
        """
        from app.core.tenant import set_tenant_context
        events: list[tuple[int, int, str]] = []
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            escalation_changed = False
            active_incidents = db.query(Incident).filter(Incident.status == "active").all()
            for incident in active_incidents:
                db.refresh(incident, ["breathing_troops"])
                dept = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
                grace_min = dept.escalation_grace_min if dept else 3
                for troop in incident.breathing_troops:
                    if troop.status not in ("im_einsatz", "rueckzug"):
                        _sent.pop(troop.id, None)
                        continue
                    active_warnings = set(check_troop_warnings(troop))
                    prev_warnings = _sent.get(troop.id, set())
                    for kind in active_warnings - prev_warnings:
                        events.append((incident.id, troop.id, kind))
                    _sent[troop.id] = active_warnings
                    if check_and_escalate_troop(db, incident, troop, grace_min):
                        escalation_changed = True
            if escalation_changed:
                db.commit()
        finally:
            db.close()
        return events

    while True:
        try:
            await asyncio.sleep(5)
            for incident_id, troop_id, kind in await asyncio.to_thread(_collect_new_warnings):
                await manager.broadcast(incident_id, {
                    "type": "troop_warning",
                    "troop_id": troop_id,
                    "kind": kind,
                })
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Watchdog darf nie crashen
