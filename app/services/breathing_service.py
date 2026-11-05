"""Atemschutzüberwachung – Rückzugsdruck-Berechnung und Status-Maschine."""
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.core.audit import write_incident_change
from app.models.breathing import (
    BOTTLE_PRESET_NOMINAL_BAR,
    TROOP_STATUSES,
    BreathingDeployment,
    BreathingKontrolle,
    BreathingTroop,
    PressureLog,
    TroopMember,
)

WARNEINRICHTUNG_BAR = 60.0
MAX_PRESSURE_BAR = 330
ALLOWED_TROOP_TRANSITIONS = {
    "im_einsatz": {"rueckzug", "zurueck"},
    "rueckzug": {"zurueck"},
    "zurueck": {"erholt"},
}
logger = logging.getLogger("einsatzleiter.breathing")
_watchdog_last_ok: datetime | None = None


class InvalidTroopTransition(ValueError):
    """Der angeforderte Statusübergang ist nicht erlaubt."""


def validate_pressure(value: float) -> float:
    """Prüft einen endlichen Flaschendruck im Bereich 0 bis 330 bar."""
    if not math.isfinite(value) or not 0 <= value <= MAX_PRESSURE_BAR:
        raise ValueError("Der Druck muss zwischen 0 und 330 bar liegen und endlich sein.")
    return value


def get_watchdog_heartbeat() -> datetime | None:
    """UTC-Zeitpunkt des letzten erfolgreichen Watchdog-Durchlaufs."""
    return _watchdog_last_ok


def _now() -> datetime:
    return datetime.now(UTC)


def breathing_system_enabled(db: Session) -> bool:
    """Systemweiter Atemschutz-Flag; ein fehlender Eintrag bedeutet aktiv."""
    from app.models.master import SystemSettings

    row = db.query(SystemSettings).filter(SystemSettings.key == "breathing_enabled").first()
    return row.value != "false" if row is not None else True


def breathing_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Atemschutz ist nur bei aktivem System- und Org-Schalter verfügbar."""
    if org_id is None:
        return False
    if not breathing_system_enabled(db):
        return False
    from app.models.master import OrgSettings

    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return org_s is None or org_s.atemschutz_ueberwachung_modul_aktiv


class AsuewSettings(NamedTuple):
    erwartete_einsatzzeit_min: int
    rueckzug_untergrenze_bar: int
    gelb_prozent: int
    erholzeit_min: int


def get_asuew_settings(db: Session, org_id: int) -> AsuewSettings:
    """Liest die ASUEW-Org-Werte mit Defaults und begrenzten Wertebereichen."""
    from app.models.master import OrgSettings

    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()

    def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
        value = getattr(row, name, None)
        return default if value is None else max(minimum, min(maximum, value))

    return AsuewSettings(
        bounded("asuew_erwartete_einsatzzeit_min", 30, 5, 120),
        bounded("asuew_rueckzug_untergrenze_bar", 60, 30, 100),
        bounded("asuew_gelb_prozent", 75, 50, 95),
        bounded("asuew_erholzeit_min", 30, 0, 120),
    )


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


def _asuew_settings_for_troop(db: Session, troop: BreathingTroop) -> AsuewSettings:
    """Lädt Org-Einstellungen des Einsatzes oder die ASÜW-Defaults."""
    from app.models.incident import Incident

    incident = db.get(Incident, troop.incident_id)
    if incident is not None and incident.primary_org_id is not None:
        return get_asuew_settings(db, incident.primary_org_id)
    return AsuewSettings(30, int(WARNEINRICHTUNG_BAR), 75, 30)


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
                BreathingTroop.status == "bereit",
            )
            .first()
        )
        if standby is None:
            issues.append("Kein bereiter Sicherheitstrupp für diesen Einsatz vorhanden.")

    nominal_bar = BOTTLE_PRESET_NOMINAL_BAR.get(troop.bottle_preset or "")
    if nominal_bar is not None:
        minimum = nominal_bar * 0.9
        for member in troop.active_members:
            if member.start_press is not None and member.start_press < minimum:
                issues.append(
                    f"{member.display_name}: Anfangsdruck {member.start_press:g} bar liegt unter "
                    f"90 % des Nominalfülldrucks ({minimum:g} bar)."
                )
    if troop.deployments and troop.deployments[-1].back_at is not None:
        erholzeit_min = _asuew_settings_for_troop(db, troop).erholzeit_min
        back_at = troop.deployments[-1].back_at
        back_at = back_at if back_at.tzinfo else back_at.replace(tzinfo=UTC)
        remaining = (back_at + timedelta(minutes=erholzeit_min) - _now()).total_seconds()
        if erholzeit_min and remaining > 0:
            issues.append(
                f"Erholzeit nicht eingehalten: noch {math.ceil(remaining / 60)} Min. "
                f"(empfohlen {erholzeit_min} Min. Pause)"
            )

    member_ids = {member.member_id for member in troop.active_members if member.member_id is not None}
    if member_ids:
        other_troops = (
            db.query(BreathingTroop)
            .filter(
                BreathingTroop.incident_id == troop.incident_id,
                BreathingTroop.id != troop.id,
                BreathingTroop.status.in_(("im_einsatz", "rueckzug")),
            )
            .all()
        )
        for other in other_troops:
            other_ids = {member.member_id for member in other.active_members}
            for member in troop.active_members:
                if member.member_id is not None and member.member_id in other_ids:
                    issues.append(f"{member.display_name} ist bereits in Trupp {other.name} im Einsatz.")
    return issues


def _validated_members_data(members_data: list[dict]) -> list[dict]:
    """Filtert leere Mitgliedszeilen und erzwingt die Mindeststärke 0/2/2."""
    filled_members = [
        data for data in members_data
        if data.get("member_id") or str(data.get("free_text_name") or "").strip()
    ]
    if len(filled_members) < 2:
        raise ValueError("Ein Atemschutztrupp benötigt mindestens 2 Mitglieder.")
    for data in filled_members:
        if data.get("start_press") is not None:
            validate_pressure(data["start_press"])
    return filled_members


def _add_live_members(
    db: Session, troop: BreathingTroop, members_data: list[dict]
) -> list[TroopMember]:
    """Legt die validierte Besetzung für den aktuellen Einsatzzyklus an."""
    members = []
    for data in _validated_members_data(members_data):
        member = TroopMember(
            troop=troop,
            deployment_id=None,
            member_id=data.get("member_id"),
            free_text_name=data.get("free_text_name"),
            role=data.get("role", "truppmann"),
            start_press=data.get("start_press"),
            current_press=data.get("start_press"),
        )
        db.add(member)
        members.append(member)
    return members


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
    ueberwacher_user_id: int | None = None,
    ueberwacher_name: str | None = None,
    angriffsweg: str | None = None,
    funkkanal: str | None = None,
) -> BreathingTroop:
    """
    members_data: [{"member_id": int|None, "free_text_name": str|None,
                    "role": "truppfuehrer"|"truppmann", "start_press": float}]
    """
    filled_members = _validated_members_data(members_data)

    troop = BreathingTroop(
        incident_id=incident_id,
        name=name,
        unit_name=unit_name,
        status="bereit",
        ueberwacher_user_id=ueberwacher_user_id,
        ueberwacher_name=ueberwacher_name.strip()[:150] if ueberwacher_name else None,
        angriffsweg=angriffsweg.strip()[:300] if angriffsweg else None,
        funkkanal=funkkanal.strip()[:50] if funkkanal else None,
        task_text=task_text,
        vehicle_id=vehicle_id,
        location_text=location_text,
        planned_duration_min=planned_duration_min,
        bottle_preset=bottle_preset,
        is_sicherheitstrupp=is_sicherheitstrupp,
    )
    db.add(troop)
    db.flush()

    _add_live_members(db, troop, filled_members)
    db.flush()

    write_incident_change(
        db, incident_id, "troop.created", "breathing_troop", troop.id,
        before=None, after={"name": name, "members": len(filled_members)},
        user_id=user_id,
    )
    return troop


def redeploy_troop(
    db: Session,
    troop: BreathingTroop,
    members_data: list[dict],
    task_text: str | None = None,
    location_text: str | None = None,
    planned_duration_min: int | None = None,
    bottle_preset: str | None = None,
    user_id: int | None = None,
) -> BreathingTroop:
    """Archiviert den abgeschlossenen Zyklus und bereitet den Trupp neu vor."""
    if troop.status != "erholt":
        raise ValueError("Nur ein erholter Atemschutztrupp kann erneut eingesetzt werden.")
    filled_members = _validated_members_data(members_data)
    old_members = list(troop.active_members)
    old_kontrollen = list(troop.active_kontrollen)
    deployment = BreathingDeployment(
        troop=troop,
        lfd_nr=len(troop.deployments) + 1,
        entry_at=troop.entry_at,
        withdraw_at=troop.withdraw_at,
        back_at=troop.back_at,
        status=troop.status,
        planned_duration_min=troop.planned_duration_min,
        bottle_preset=troop.bottle_preset,
        task_text=troop.task_text,
        location_text=troop.location_text,
        ueberwacher_name=troop.ueberwacher_name,
        angriffsweg=troop.angriffsweg,
        funkkanal=troop.funkkanal,
        funk_verloren_at=troop.funk_verloren_at,
        notfall_at=troop.notfall_at,
        start_press_avg=troop.start_press_avg,
        withdraw_press_calc=troop.withdraw_press_calc,
        warn_one_third_acked_at=troop.warn_one_third_acked_at,
        warn_two_third_acked_at=troop.warn_two_third_acked_at,
        warn_max_time_acked_at=troop.warn_max_time_acked_at,
        warn_withdraw_acked_at=troop.warn_withdraw_acked_at,
        warn_withdraw_acked_press=troop.warn_withdraw_acked_press,
        readiness_override_reason=troop.readiness_override_reason,
        readiness_override_by_user_id=troop.readiness_override_by_user_id,
        readiness_override_at=troop.readiness_override_at,
        escalated_at=troop.escalated_at,
    )
    db.add(deployment)
    db.flush()

    for member in old_members:
        member.deployment = deployment
        member.deployment_id = deployment.id

    for kontrolle in old_kontrollen:
        kontrolle.deployment_id = deployment.id

    _add_live_members(db, troop, filled_members)

    troop.funk_verloren_at = None
    troop.notfall_at = None
    troop.notfall_von_user_id = None
    troop.status = "bereit"
    troop.entry_at = None
    troop.withdraw_at = None
    troop.back_at = None
    troop.warn_one_third_acked_at = None
    troop.warn_two_third_acked_at = None
    troop.warn_max_time_acked_at = None
    troop.warn_withdraw_acked_at = None
    troop.warn_withdraw_acked_press = None
    troop.readiness_override_reason = None
    troop.readiness_override_by_user_id = None
    troop.readiness_override_at = None
    troop.escalated_at = None
    troop.start_press_avg = None
    troop.withdraw_press_calc = None
    troop.last_meldung_at = None
    troop.last_meldung_text = None
    troop.task_text = task_text if task_text is not None else troop.task_text
    troop.location_text = location_text if location_text is not None else troop.location_text
    troop.planned_duration_min = (
        planned_duration_min if planned_duration_min is not None else troop.planned_duration_min
    )
    troop.bottle_preset = bottle_preset if bottle_preset is not None else troop.bottle_preset
    db.flush()

    write_incident_change(
        db, troop.incident_id, "troop.redeployed", "breathing_troop", troop.id,
        before=None, after={"lfd_nr": deployment.lfd_nr, "members": len(members_data)},
        user_id=user_id,
    )
    return troop


def get_troop_cycles(
    troop: BreathingTroop,
) -> list[BreathingDeployment | BreathingTroop]:
    """Liefert archivierte und aktuellen Einsatzzyklus in zeitlicher Reihenfolge."""
    if troop.entry_at is not None or troop.deployments:
        return [*troop.deployments, troop]
    return [troop]


def start_troop(
    db: Session,
    troop: BreathingTroop,
    user_id: int | None = None,
    override_reason: str | None = None,
    override_user_id: int | None = None,
) -> BreathingTroop:
    if len(troop.active_members) < 2:
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
    pressures = [m.start_press for m in troop.active_members if m.start_press is not None]
    settings = _asuew_settings_for_troop(db, troop)
    troop.withdraw_press_calc = float(settings.rueckzug_untergrenze_bar)
    if pressures:
        avg = sum(pressures) / len(pressures)
        troop.start_press_avg = avg

        # Set individual withdraw pressures
        for m in troop.active_members:
            if m.start_press is not None:
                m.withdraw_press = float(settings.rueckzug_untergrenze_bar)
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
    if new_status not in TROOP_STATUSES:
        raise ValueError(f"Unbekannter Truppstatus: {new_status}")
    if new_status == troop.status:
        return troop
    if new_status not in ALLOWED_TROOP_TRANSITIONS.get(troop.status, set()):
        raise InvalidTroopTransition(f"Statusübergang {troop.status} -> {new_status} ist nicht erlaubt.")
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
    pressure_bar = validate_pressure(pressure_bar)
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Druckmeldungen sind nur im Einsatz oder Rückzug erlaubt.")
    member = next((m for m in troop.active_members if m.id == troop_member_id), None)
    if member is None:
        raise ValueError("Das Truppmitglied gehört nicht zu diesem Atemschutztrupp.")
    now = _now()
    log = PressureLog(
        troop=troop,
        troop_id=troop.id,
        troop_member=member,
        troop_member_id=member.id,
        member_id=member.member_id,
        pressure_bar=pressure_bar,
        note=note or None,
        recorded_by_user_id=recorded_by_user_id,
        ts=now,
    )
    db.add(log)
    member.current_press = pressure_bar
    # Jede Druckmeldung gilt als Lagemeldung (Leitfaden: "Lage- und Flaschendruck-meldung")
    troop.last_meldung_at = now
    if note:
        troop.last_meldung_text = note
    db.flush()
    return log


def record_kontrolle(
    db: Session,
    troop: BreathingTroop,
    stufe: str,
    art: str,
    pressure_bar: float | None = None,
    troop_member_id: int | None = None,
    note: str | None = None,
    user_id: int | None = None,
) -> BreathingKontrolle:
    """Protokolliert eine Druckabfrage oder einen erfolglosen Funkkontakt."""
    if stufe not in ("one_third", "two_third", "frei"):
        raise ValueError("Unbekannte Kontrollstufe.")
    if art not in ("funk", "kein_kontakt"):
        raise ValueError("Unbekannte Kontrollart.")
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Kontrollen sind nur im Einsatz oder Rückzug erlaubt.")
    if art == "funk":
        if pressure_bar is None:
            raise ValueError("Eine Funkkontrolle benötigt den niedrigsten Flaschendruck.")
        pressure_bar = validate_pressure(pressure_bar)
        if troop_member_id is None:
            members = troop.active_members
            if not members:
                raise ValueError("Das Truppmitglied fehlt.")
            with_pressure = [member for member in members if member.current_press is not None]
            member = min(with_pressure, key=lambda member: member.current_press or 0) if with_pressure else members[0]
            troop_member_id = member.id
        label = {"one_third": "1/3", "two_third": "2/3", "frei": "frei"}[stufe]
        log_pressure(
            db, troop, troop_member_id, pressure_bar,
            note=note or f"Kontrolle {label}: niedrigster Druck",
            recorded_by_user_id=user_id,
        )
    now = _now()
    due_seconds = {"one_third": troop.one_third_seconds, "two_third": troop.two_third_seconds}.get(stufe)
    faellig_at = (
        troop.entry_at + timedelta(seconds=due_seconds)
        if troop.entry_at is not None and due_seconds is not None else None
    )
    if art == "kein_kontakt":
        troop.funk_verloren_at = now
    kontrolle = BreathingKontrolle(
        troop=troop, troop_id=troop.id, deployment_id=None,
        stufe=stufe, art=art, faellig_at=faellig_at, erledigt_at=now,
        niedrigster_druck=pressure_bar if art == "funk" else None,
        troop_member_id=troop_member_id, note=note, user_id=user_id,
    )
    db.add(kontrolle)
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.kontrolle", "breathing_troop", troop.id,
        before=None, after={"stufe": stufe, "art": art, "pressure_bar": kontrolle.niedrigster_druck},
        user_id=user_id,
    )
    return kontrolle


def set_funk_verloren(
    db: Session, troop: BreathingTroop, active: bool, user_id: int | None = None,
) -> BreathingTroop:
    """Markiert Funkverlust oder hebt ihn auch nach der Rückkehr auf."""
    if active and troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Funkverlust kann nur im Einsatz oder Rückzug gemeldet werden.")
    before = {"active": troop.funk_verloren_at is not None}
    troop.funk_verloren_at = (troop.funk_verloren_at or _now()) if active else None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.funk_verloren", "breathing_troop", troop.id,
        before=before, after={"active": active}, user_id=user_id,
    )
    return troop


def declare_notfall(
    db: Session, troop: BreathingTroop, user_id: int | None = None,
) -> BreathingTroop:
    """Meldet einen Atemschutznotfall ohne Änderung des Einsatzstatus."""
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Ein Notfall kann nur im Einsatz oder Rückzug gemeldet werden.")
    if troop.notfall_at is not None:
        return troop
    troop.notfall_at = _now()
    troop.notfall_von_user_id = user_id
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.notfall", "breathing_troop", troop.id,
        before=None, after={"active": True}, user_id=user_id,
    )
    return troop


def clear_notfall(
    db: Session, troop: BreathingTroop, user_id: int | None = None,
) -> BreathingTroop:
    """Hebt einen Notfall auch nach Abschluss des Einsatzes auf."""
    before = {"active": troop.notfall_at is not None}
    troop.notfall_at = None
    troop.notfall_von_user_id = None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.notfall_cleared", "breathing_troop", troop.id,
        before=before, after={"active": False}, user_id=user_id,
    )
    return troop


def report_objective_reached(
    db: Session,
    troop: BreathingTroop,
    troop_member_id: int,
    pressure_bar: float,
    recorded_by_user_id: int | None = None,
) -> TroopMember:
    """Erfasst das Einsatzziel und berechnet den Rückzugsdruck nach FAZ-Lehrbehelf.

    Rückzug bei doppeltem Anmarschverbrauch, nie unter der Warneinrichtung.
    Ein Zieldruck über dem Anfangsdruck wird als Verbrauch von 0 angesetzt.
    """
    pressure_bar = validate_pressure(pressure_bar)
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Das Einsatzziel kann nur im Einsatz oder Rückzug gemeldet werden.")
    member = next((m for m in troop.active_members if m.id == troop_member_id), None)
    if member is None or member.start_press is None:
        raise ValueError("Das Truppmitglied oder sein Anfangsdruck fehlt.")
    now = _now()
    member.objective_press = pressure_bar
    member.objective_press_at = now
    member.current_press = pressure_bar
    floor = _asuew_settings_for_troop(db, troop).rueckzug_untergrenze_bar
    member.withdraw_press = round(max(floor, 2 * (member.start_press - pressure_bar)), 1)
    db.add(PressureLog(
        troop=troop,
        troop_id=troop.id,
        troop_member=member,
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
    pressure_bar = validate_pressure(pressure_bar)
    if troop.status not in ("zurueck", "erholt", "rueckzug"):
        raise ValueError("Der Enddruck kann nur im Rückzug oder nach der Rückkehr gemeldet werden.")
    member = next((m for m in troop.active_members if m.id == troop_member_id), None)
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
    if kind in ("one_third", "two_third"):
        raise ValueError("Die Kontrolle erfolgt über Druckabfrage, nicht über Quittieren.")
    if kind == "max_time":
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


def get_warning_level(troop: BreathingTroop, gelb_prozent: int = 75) -> PressureWarning:
    """Prüft jedes Mitglied einzeln; der ungünstigste Status bestimmt den Trupp."""
    worst = PressureWarning("ok", None)
    rank = {"ok": 0, "yellow": 1, "red": 2}
    for member in troop.active_members:
        current = member.current_press
        if current is None or member.start_press is None:
            continue
        if member.withdraw_press is not None and current <= member.withdraw_press:
            candidate = "red"
        elif current <= member.start_press * gelb_prozent / 100:
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
                     Funkkontrolle oder Druckmeldung nach dieser Schwelle eingegangen.
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

    stages = (
        (troop.two_third_seconds, "two_third_due", "two_third"),
        (troop.one_third_seconds, "one_third_due", "one_third"),
    )
    for due_seconds, warning, stufe in stages:
        if due_seconds and elapsed >= due_seconds:
            due_at = entry + timedelta(seconds=due_seconds)
            controlled = any(
                kontrolle.art == "funk" and kontrolle.stufe == stufe
                for kontrolle in troop.active_kontrollen
            )
            reported = any(
                (log.ts if log.ts.tzinfo else log.ts.replace(tzinfo=UTC)) >= due_at
                for log in troop.active_pressure_logs
            )
            if not controlled and not reported:
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
    if troop.notfall_at is not None:
        warnings.append("notfall")
    elif troop.funk_verloren_at is not None:
        warnings.append("funk_verloren")
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


def _collect_new_breathing_warnings(
    db: Session, sent: dict[int, set[str]]
) -> list[tuple[int, int, str]]:
    """Sammelt neue Warnungen aktiver Orgs und führt fällige Eskalationen aus."""
    from app.models.incident import Incident
    from app.models.master import FireDept

    events: list[tuple[int, int, str]] = []
    escalation_changed = False
    enabled_by_org: dict[int | None, bool] = {}
    active_incidents = db.query(Incident).filter(Incident.status == "active").all()
    for incident in active_incidents:
        org_id = incident.primary_org_id
        if org_id not in enabled_by_org:
            enabled_by_org[org_id] = breathing_effective_enabled(org_id, db)
        if not enabled_by_org[org_id]:
            continue
        db.refresh(incident, ["breathing_troops"])
        dept = db.get(FireDept, org_id) if org_id else None
        grace_min = dept.escalation_grace_min if dept else 3
        for troop in incident.breathing_troops:
            if troop.status not in ("im_einsatz", "rueckzug"):
                sent.pop(troop.id, None)
                continue
            active_warnings = set(check_troop_warnings(troop))
            prev_warnings = sent.get(troop.id, set())
            for kind in active_warnings - prev_warnings:
                events.append((incident.id, troop.id, kind))
            sent[troop.id] = active_warnings
            if check_and_escalate_troop(db, incident, troop, grace_min):
                escalation_changed = True
    if escalation_changed:
        db.commit()
    return events


async def _breathing_watchdog_loop() -> None:
    """Prüft alle 5 Sekunden alle laufenden Trupps und broadcastet Warnungen."""
    global _watchdog_last_ok

    import asyncio

    from app.db import SessionLocal
    from app.services.broadcast import manager

    # Tracking bereits gesendeter Warns (troop_id → set[kind])
    # verhindert dauerhaftes Re-Senden ohne Zustandsänderung
    sent: dict[int, set[str]] = {}
    last_error_logged: float | None = None

    def _collect_new_warnings() -> list[tuple[int, int, str]]:
        """DB-Scan im Threadpool (Audit B2)."""
        from app.core.tenant import set_tenant_context

        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return _collect_new_breathing_warnings(db, sent)
        finally:
            db.close()

    while True:
        try:
            await asyncio.sleep(5)
            for incident_id, troop_id, kind in await asyncio.to_thread(_collect_new_warnings):
                await manager.broadcast(incident_id, {
                    "type": "troop_warning",
                    "troop_id": troop_id,
                    "kind": kind,
                })
            _watchdog_last_ok = _now()
        except asyncio.CancelledError:
            raise
        except Exception:
            now = monotonic()
            if last_error_logged is None or now - last_error_logged >= 60:
                logger.exception("Atemschutz-Watchdog: Fehler im Scan")
                last_error_logged = now
