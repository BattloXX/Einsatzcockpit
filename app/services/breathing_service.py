"""Atemschutzüberwachung – Rückzugsdruck-Berechnung und Status-Maschine."""
import logging
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import NamedTuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import write_incident_change
from app.models.breathing import (
    BOTTLE_PRESET_NOMINAL_BAR,
    TROOP_STATUSES,
    BreathingDeployment,
    BreathingGeraetSitzung,
    BreathingKontrolle,
    BreathingTroop,
    BreathingWarnung,
    PressureLog,
    TroopMember,
)
from app.models.incident import Incident
from app.services.breathing_events import cleanup_client_events, event_audit

WARNEINRICHTUNG_BAR = 60.0
MAX_PRESSURE_BAR = 330
ALLOWED_TROOP_TRANSITIONS = {
    "im_einsatz": {"rueckzug", "zurueck"},
    "rueckzug": {"zurueck"},
    "zurueck": {"erholt"},
}
logger = logging.getLogger("einsatzleiter.breathing")
_watchdog_last_ok: datetime | None = None
_watchdog_last_sitzung_cleanup: datetime | None = None


class InvalidTroopTransition(ValueError):
    """Der angeforderte Statusübergang ist nicht erlaubt."""


class PressureIncreaseConflict(ValueError):
    """Ein aktueller Druckanstieg benötigt einen gültigen Korrekturgrund."""


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


def troop_timeline(troop: BreathingTroop, now: datetime | None = None) -> list[dict]:
    """Fristen und dokumentierte Ereignisse des aktuellen Einsatzzyklus."""
    if not troop.entry_at:
        return []
    events = []

    def add(label, soll_at=None, ist_at=None, status="erledigt", detail=""):
        events.append(dict(label=label, soll_at=soll_at, ist_at=ist_at, status=status, detail=detail))

    add("Eintritt", ist_at=troop.entry_at)
    entry = troop.entry_at if troop.entry_at.tzinfo else troop.entry_at.replace(tzinfo=UTC)
    end = troop.back_at or now or _now()
    end = end if end.tzinfo else end.replace(tzinfo=UTC)
    elapsed = (end - entry).total_seconds()
    for label, stufe, seconds in [
        ("1/3", "one_third", troop.one_third_seconds),
        ("2/3", "two_third", troop.two_third_seconds),
        ("Max", None, troop.max_seconds),
    ]:
        if seconds is None:
            continue
        controls = [k for k in troop.active_kontrollen if k.stufe == stufe and k.art == "funk"] if stufe else []
        control = max(controls, key=lambda k: k.erledigt_at) if controls else None
        # A return completes the maximum-duration deadline; radio loss never completes a pressure check.
        actual = control.erledigt_at if control else (troop.back_at if stufe is None else None)
        status = "erledigt" if actual else ("faellig" if elapsed >= seconds else "offen")
        detail = f"{control.niedrigster_druck:g} bar" if control and control.niedrigster_druck is not None else ""
        add(label, troop.entry_at + timedelta(seconds=seconds), actual, status, detail)
    for member in troop.active_members:
        if member.objective_press_at:
            detail = member.display_name
            if member.objective_press is not None:
                detail += f" ({member.objective_press:g} bar)"
            add("Einsatzziel", ist_at=member.objective_press_at, detail=detail)
    if troop.withdraw_at:
        add("Rückzug", ist_at=troop.withdraw_at)
    if troop.back_at:
        add("Zurück", ist_at=troop.back_at)
    return events


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


def parse_user_id_csv(raw: str | None) -> list[int]:
    """Liest ganze User-IDs und entfernt Duplikate unter Beibehaltung der Reihenfolge."""
    ids: list[int] = []
    for part in (raw or "").split(","):
        value = part.strip()
        if re.fullmatch(r"[+-]?[0-9]+", value) is None:
            continue
        try:
            user_id = int(value)
        except ValueError:
            continue
        if user_id not in ids:
            ids.append(user_id)
    return ids


def get_eskalation_empfaenger_ids(db: Session, org_id: int) -> tuple[list[int], bool]:
    """Liefert konfigurierte aktive Empfänger der eigenen Org und das SMS-Flag."""
    from app.models.master import OrgSettings
    from app.models.user import User

    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if row is None:
        return [], True
    ids = parse_user_id_csv(row.asuew_eskalation_user_ids)
    # Nur darstellbare BigInteger-Werte an die Datenbank weiterreichen.
    valid_ids = {user.id for user in db.query(User).filter(
        User.org_id == org_id, User.active.is_(True),
        User.id.in_([value for value in ids if -(2**63) <= value < 2**63]),
    ).all()} if ids else set()
    return [value for value in ids if value in valid_ids], row.asuew_eskalation_sms is not False


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
    ts: datetime | None = None,
    nachgetragen: bool = False,
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
        troop.withdraw_at = ts or _now()
    elif new_status == "zurueck":
        troop.back_at = ts or _now()
    if new_status not in ("im_einsatz", "rueckzug"):
        troop.escalated_at = None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.status_changed", "breathing_troop", troop.id,
        before=before, after={"status": new_status, **event_audit(ts, nachgetragen)},
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
    ts: datetime | None = None,
    nachgetragen: bool = False,
    korrektur_grund: str | None = None,
    allow_increase: bool = False,
) -> PressureLog:
    pressure_bar = validate_pressure(pressure_bar)
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Druckmeldungen sind nur im Einsatz oder Rückzug erlaubt.")
    member = next((m for m in troop.active_members if m.id == troop_member_id), None)
    if member is None:
        raise ValueError("Das Truppmitglied gehört nicht zu diesem Atemschutztrupp.")
    now = ts or _now()
    historical = any(
        log.troop_member_id == member.id
        and log.ts.replace(tzinfo=None) > now.replace(tzinfo=None)
        for log in troop.active_pressure_logs
    )
    reason = (korrektur_grund or "").strip()
    if reason and not 3 <= len(reason) <= 250:
        raise PressureIncreaseConflict("Der Korrekturgrund muss 3 bis 250 Zeichen lang sein.")
    if (not historical and not allow_increase and member.current_press is not None
            and pressure_bar > member.current_press + 10 and not reason):
        raise PressureIncreaseConflict("Der Druck kann nicht steigen. Bei einer Korrektur bitte einen Grund angeben.")
    if reason:
        correction = f"Korrektur: {reason}"
        note = f"{correction}; {note}"[:300] if note else correction
    log = PressureLog(
        troop=troop,
        troop_id=troop.id,
        troop_member=member,
        troop_member_id=member.id,
        member_id=member.member_id,
        pressure_bar=pressure_bar,
        note=note or None,
        recorded_by_user_id=recorded_by_user_id,
        ts=now, nachgetragen=nachgetragen,
    )
    db.add(log)
    if not historical:
        member.current_press = pressure_bar
        # Jede Druckmeldung gilt als Lagemeldung (Leitfaden: "Lage- und Flaschendruck-meldung")
        troop.last_meldung_at = now
        if note:
            troop.last_meldung_text = note
    db.flush()
    if ts is not None or nachgetragen:
        write_incident_change(
            db, troop.incident_id, "troop.pressure_logged", "troop_member", member.id,
            before=None, after={"pressure_bar": pressure_bar, "note": log.note, **event_audit(ts, nachgetragen)},
            user_id=recorded_by_user_id,
        )
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
    ts: datetime | None = None,
    nachgetragen: bool = False,
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
            recorded_by_user_id=user_id, ts=ts, nachgetragen=nachgetragen, allow_increase=True,
        )
    now = ts or _now()
    due_seconds = {"one_third": troop.one_third_seconds, "two_third": troop.two_third_seconds}.get(stufe)
    faellig_at = (
        troop.entry_at + timedelta(seconds=due_seconds)
        if troop.entry_at is not None and due_seconds is not None else None
    )
    if art == "kein_kontakt":
        troop.funk_verloren_at = now
    kontrolle = BreathingKontrolle(
        troop=troop, troop_id=troop.id, deployment_id=None,
        stufe=stufe, art=art, faellig_at=faellig_at, erledigt_at=now, nachgetragen=nachgetragen,
        niedrigster_druck=pressure_bar if art == "funk" else None,
        troop_member_id=troop_member_id, note=note, user_id=user_id,
    )
    db.add(kontrolle)
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.kontrolle", "breathing_troop", troop.id,
        before=None, after={"stufe": stufe, "art": art, "pressure_bar": kontrolle.niedrigster_druck,
                            **event_audit(ts, nachgetragen)},
        user_id=user_id,
    )
    return kontrolle


def set_funk_verloren(
    db: Session, troop: BreathingTroop, active: bool, user_id: int | None = None,
    ts: datetime | None = None,
    nachgetragen: bool = False,
) -> BreathingTroop:
    """Markiert Funkverlust oder hebt ihn auch nach der Rückkehr auf."""
    if active and troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Funkverlust kann nur im Einsatz oder Rückzug gemeldet werden.")
    before = {"active": troop.funk_verloren_at is not None}
    troop.funk_verloren_at = (troop.funk_verloren_at or ts or _now()) if active else None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.funk_verloren", "breathing_troop", troop.id,
        before=before, after={"active": active, **event_audit(ts, nachgetragen)}, user_id=user_id,
    )
    return troop


def declare_notfall(
    db: Session, troop: BreathingTroop, user_id: int | None = None,
    ts: datetime | None = None,
    nachgetragen: bool = False,
) -> BreathingTroop:
    """Meldet einen Atemschutznotfall ohne Änderung des Einsatzstatus."""
    if troop.status not in ("im_einsatz", "rueckzug"):
        raise ValueError("Ein Notfall kann nur im Einsatz oder Rückzug gemeldet werden.")
    if troop.notfall_at is not None:
        return troop
    troop.notfall_at = ts or _now()
    troop.notfall_von_user_id = user_id
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.notfall", "breathing_troop", troop.id,
        before=None, after={"active": True, **event_audit(ts, nachgetragen)}, user_id=user_id,
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
    ts: datetime | None = None,
    nachgetragen: bool = False,
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
    now = ts or _now()
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
        ts=now, nachgetragen=nachgetragen,
    ))
    troop.last_meldung_at = now
    troop.last_meldung_text = "Einsatzziel erreicht"
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.objective_reached", "troop_member", member.id,
        before=None,
        after={"pressure_bar": pressure_bar, "withdraw_press": member.withdraw_press, **event_audit(ts, nachgetragen)},
        user_id=recorded_by_user_id,
    )
    return member


def report_back_pressure(
    db: Session,
    troop: BreathingTroop,
    troop_member_id: int,
    pressure_bar: float,
    recorded_by_user_id: int | None = None,
    ts: datetime | None = None,
    nachgetragen: bool = False,
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
        before=None, after={"pressure_bar": pressure_bar, **event_audit(ts, nachgetragen)},
        user_id=recorded_by_user_id,
    )
    return member


def update_meldung(
    db: Session,
    troop: BreathingTroop,
    text: str | None,
    user_id: int | None = None,
    ts: datetime | None = None,
    nachgetragen: bool = False,
) -> None:
    """Setzt letzte Lagemeldung (Zeitpunkt + Text) ohne Druckprotokoll."""
    troop.last_meldung_at = ts or _now()
    troop.last_meldung_text = text or None
    db.flush()
    write_incident_change(
        db, troop.incident_id, "troop.meldung", "breathing_troop", troop.id,
        before=None, after={"text": text, **event_audit(ts, nachgetragen)},
        user_id=user_id,
    )


def ack_warning(
    db: Session,
    troop: BreathingTroop,
    kind: str,
    user_id: int | None = None,
    ts: datetime | None = None,
    nachgetragen: bool = False,
) -> None:
    """Quittiert den aktuellen Warnzustand nach ASÜW-Leitfaden/FwDV 7."""
    now = ts or _now()
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
        before=None, after={"kind": kind, **event_audit(ts, nachgetragen)},
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


def troop_urgency(troop: BreathingTroop, warning_level: str, active_warnings: Iterable[str]) -> int:
    """Kleinere Werte stehen auf dem Board weiter vorne; Sicherheitslagen gehen vor."""
    warnings = set(active_warnings)
    if troop.notfall_at is not None or "notfall" in warnings:
        return 0
    if troop.funk_verloren_at is not None or "funk_verloren" in warnings:
        return 1
    if "withdraw" in warnings or warning_level == "red":
        return 2
    if troop.status == "rueckzug":
        return 3
    if "max_exceeded" in warnings:
        return 4
    if warnings.intersection({"two_third_due", "one_third_due"}):
        return 5
    if warning_level == "yellow":
        return 6
    return {"im_einsatz": 7, "bereit": 8, "zurueck": 9, "erholt": 10}[troop.status]


def get_time_warnings(troop: BreathingTroop, now: datetime | None = None) -> list[str]:
    """Gibt alle gleichzeitig aktiven Zeitwarnungen nach ASÜW-Leitfaden/FwDV 7 zurück.

    Trigger-Regeln (Leitfaden ASÜW / FwDV 7):
      one_third_due / two_third_due: jeweilige Schwelle verstrichen UND keine
                     Funkkontrolle oder Druckmeldung nach dieser Schwelle eingegangen.
      max_exceeded:  Maximale Einsatzzeit überschritten UND nicht quittiert.
    """
    if troop.entry_at is None or troop.status not in ("im_einsatz", "rueckzug"):
        return []

    now = now or _now()
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
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


def check_troop_warnings(troop: BreathingTroop, now: datetime | None = None) -> list[str]:
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
    warnings.extend(get_time_warnings(troop, now))
    return warnings


def incident_hinweise(troops: Iterable[BreathingTroop]) -> dict[str, BreathingTroop]:
    """Einsatzbezogene Hinweise am eingesetzten Trupp mit der niedrigsten ID verankern.

    Erwartet die Trupps eines Einsatzes. Bei einem Ankerwechsel wird die alte
    Warnung geschlossen und am neuen Anker eine neue Warnung angelegt.
    """
    troops = list(troops)
    eingesetzt = [troop for troop in troops if troop.status in ("im_einsatz", "rueckzug")]
    if not eingesetzt:
        return {}
    anker = min(eingesetzt, key=lambda troop: troop.id)
    hinweise = {}
    if len(eingesetzt) >= 3:
        hinweise["assp"] = anker
    if any(not troop.is_sicherheitstrupp for troop in eingesetzt) and not any(
        troop.is_sicherheitstrupp and troop.status == "bereit" for troop in troops
    ):
        hinweise["kein_sicherheitstrupp"] = anker
    return hinweise


def dashboard_summary(troops: list[BreathingTroop], settings: AsuewSettings, now: datetime) -> dict:
    """Verdichtet bereits geladene Trupps ohne Datenbankzugriff für die EL-Kachel."""
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    counters = {
        "im_einsatz": sum(t.status in ("im_einsatz", "rueckzug") for t in troops),
        "bereit": sum(t.status == "bereit" for t in troops),
        "sicherheitstrupp_bereit": sum(bool(t.is_sicherheitstrupp) and t.status == "bereit" for t in troops),
        "in_erholung": sum(t.status in ("zurueck", "erholt") for t in troops),
    }
    rows = []
    for troop in troops:
        if troop.status not in ("im_einsatz", "rueckzug"):
            continue
        pressure = get_warning_level(troop, settings.gelb_prozent)
        warnings = check_troop_warnings(troop, now)
        measured = [m for m in troop.active_members if m.current_press is not None]
        lowest = min(measured, key=lambda m: m.current_press or 0) if measured else None
        known = any(m.current_press is not None and m.start_press is not None for m in troop.active_members)
        entry = troop.entry_at
        if entry and not entry.tzinfo:
            entry = entry.replace(tzinfo=UTC)
        deadline = None
        kinds = {"1/3": "one_third", "2/3": "two_third", "Max": "max"}
        for event in troop_timeline(troop, now):
            if event["soll_at"] is None or event["status"] == "erledigt":
                continue
            due = event["soll_at"]
            due = due if due.tzinfo else due.replace(tzinfo=UTC)
            deadline = {
                "kind": kinds[event["label"]],
                "label": "Max-Zeit" if event["label"] == "Max" else f"Kontrolle {event['label']}",
                "in_s": int((due - now).total_seconds()), "due_at": due,
            }
            break
        rows.append({
            "id": troop.id, "name": troop.name, "status": troop.status,
            "entry_at": entry, "entry_iso": entry.astimezone(UTC).isoformat() if entry else None,
            "laufzeit_s": max(0, int((now - entry).total_seconds())) if entry else 0,
            "geplant_min": troop.planned_duration_min,
            "niedrigster_druck": lowest.current_press if lowest else None,
            "niedrigster_name": lowest.display_name if lowest else None,
            "druck_klasse": {"red": "rot", "yellow": "gelb", "ok": "gruen"}[pressure.level] if known else "unbekannt",
            "naechste_frist": deadline, "warnungen": warnings,
            "notfall": troop.notfall_at is not None, "funk_verloren": troop.funk_verloren_at is not None,
            "urgency": troop_urgency(troop, pressure.level, warnings),
        })
    rows.sort(key=lambda row: (row["urgency"], row["id"]))
    hints = incident_hinweise(troops)
    return {
        "zaehler": counters, "hinweise": {key: key in hints for key in ("assp", "kein_sicherheitstrupp")},
        "trupps": rows, "gesamt": len(troops), "server_now": now,
    }


def asuew_heartbeat_fehlt(db: Session, incident: Incident, now: datetime) -> bool:
    """App-Pflicht mit eingesetztem Trupp, aber ohne frischen Geraete-Heartbeat."""
    from app.models.master import OrgSettings
    from app.services.breathing_sitzung import hat_frischen_heartbeat

    if not any(t.status in ("im_einsatz", "rueckzug") for t in incident.breathing_troops):
        return False
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == incident.primary_org_id).first()
    return bool(org_settings and org_settings.asuew_app_pflicht
                and not hat_frischen_heartbeat(db, incident.id, now))


def _cleanup_breathing_sitzungen(db: Session, now: datetime) -> bool:
    """Abgelaufene und alte beendete Sitzungen in begrenzten Chargen aufraeumen."""
    from app.services.breathing_sitzung import SITZUNG_MAX_STUNDEN

    changed = cleanup_client_events(db, now)
    for sitzung in db.query(BreathingGeraetSitzung).join(
        Incident, Incident.id == BreathingGeraetSitzung.incident_id,
    ).filter(
        BreathingGeraetSitzung.beendet_at.is_(None),
        or_(BreathingGeraetSitzung.gestartet_at < now - timedelta(hours=SITZUNG_MAX_STUNDEN),
            Incident.status != "active"),
    ).order_by(BreathingGeraetSitzung.id).limit(200).all():
        sitzung.beendet_at, sitzung.ende_grund = now, "abgelaufen"
        changed = True
    for sitzung in db.query(BreathingGeraetSitzung).filter(
        BreathingGeraetSitzung.beendet_at < now - timedelta(days=30),
    ).order_by(BreathingGeraetSitzung.id).limit(200).all():
        db.delete(sitzung)
        changed = True
    return changed


def _collect_new_breathing_warnings(db: Session) -> list[tuple[int, int, str]]:
    """Aktive Warnungen persistieren und Events nur fuer neue Warnungen liefern."""
    global _watchdog_last_sitzung_cleanup

    from app.services.breathing_notify import schliesse_warnungen_fuer_trupp, sync_warnungen

    events: list[tuple[int, int, str]] = []
    changed = False
    enabled_by_org: dict[int | None, bool] = {}
    now = _now().replace(tzinfo=None)
    cleanup_due = (_watchdog_last_sitzung_cleanup is None
                   or now - _watchdog_last_sitzung_cleanup >= timedelta(minutes=10))
    try:
        if cleanup_due:
            changed = _cleanup_breathing_sitzungen(db, now)
        active_incidents = db.query(Incident).filter(Incident.status == "active").all()
        for incident in active_incidents:
            org_id = incident.primary_org_id
            if org_id not in enabled_by_org:
                enabled_by_org[org_id] = breathing_effective_enabled(org_id, db)
            if not enabled_by_org[org_id]:
                continue
            db.refresh(incident, ["breathing_troops"])
            hinweise = incident_hinweise(incident.breathing_troops)
            if asuew_heartbeat_fehlt(db, incident, now):
                hinweise["kein_heartbeat"] = min(
                    (t for t in incident.breathing_troops if t.status in ("im_einsatz", "rueckzug")),
                    key=lambda t: t.id,
                )
            for troop in incident.breathing_troops:
                offene = db.query(BreathingWarnung).filter(
                    BreathingWarnung.troop_id == troop.id, BreathingWarnung.erledigt_at.is_(None),
                )
                if (troop.status not in ("im_einsatz", "rueckzug")
                        and troop.notfall_at is None and troop.funk_verloren_at is None):
                    if db.query(offene.exists()).scalar():
                        schliesse_warnungen_fuer_trupp(db, troop, now)
                        changed = True
                    continue
                aktive = set(check_troop_warnings(troop))
                aktive |= {kind for kind, anker in hinweise.items() if anker.id == troop.id}
                bisher_offen = offene.all()
                neue = sync_warnungen(db, incident, troop, aktive, now)
                if neue or any(w.erledigt_at is not None for w in bisher_offen):
                    changed = True
                events.extend((incident.id, troop.id, w.kind) for w in neue)
        if changed:
            db.commit()
        if cleanup_due:
            _watchdog_last_sitzung_cleanup = now
    except Exception:
        db.rollback()
        raise
    return events


async def _breathing_watchdog_loop() -> None:
    """Prüft alle 5 Sekunden alle laufenden Trupps und broadcastet Warnungen."""
    global _watchdog_last_ok

    import asyncio

    from app.db import SessionLocal
    from app.services.broadcast import manager
    from app.services.loop_utils import iteration_watch

    last_error_logged: float | None = None

    def _collect_new_warnings() -> list[tuple[int, int, str]]:
        """DB-Scan im Threadpool (Audit B2)."""
        from app.core.tenant import set_tenant_context

        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return _collect_new_breathing_warnings(db)
        finally:
            db.close()

    while True:
        try:
            await asyncio.sleep(5)
            with iteration_watch(logger, "breathing_watchdog", 5):
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
