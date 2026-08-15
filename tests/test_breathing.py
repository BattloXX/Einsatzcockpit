"""Tests für die sicherheitskritische Atemschutzlogik (Phase 1 und 2)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.breathing import BreathingTroop, TroopMember
from app.services.breathing_service import (
    ReadinessWarning,
    ack_warning,
    calc_withdraw_pressure,
    check_start_readiness,
    check_troop_warnings,
    create_troop,
    get_time_warning,
    get_warning_level,
    log_pressure,
    report_objective_reached,
    start_troop,
)


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args):
        return self

    def first(self):
        return self.result


class ReadinessDB(FakeDB):
    def __init__(self, standby=None):
        super().__init__()
        self.standby = standby
        self.query_count = 0

    def query(self, model):
        self.query_count += 1
        return FakeQuery(self.standby)


def _member(member_id: int, name: str, start: float, current: float, withdraw: float) -> TroopMember:
    member = TroopMember()
    member.id = member_id
    member.free_text_name = name
    member.start_press = start
    member.current_press = current
    member.withdraw_press = withdraw
    member.objective_press = None
    member.objective_press_at = None
    member.member_id = None
    return member


def _troop(*members: TroopMember) -> BreathingTroop:
    troop = BreathingTroop()
    troop.id = 1
    troop.incident_id = 1
    troop.members = list(members)
    troop.pressure_logs = []
    troop.warn_withdraw_acked_at = None
    troop.warn_withdraw_acked_press = None
    troop.is_sicherheitstrupp = False
    troop.bottle_preset = "1x6"
    troop.readiness_override_reason = None
    troop.readiness_override_by_user_id = None
    troop.readiness_override_at = None
    return troop


def test_withdraw_pressure_calculation():
    assert calc_withdraw_pressure(300, 0.5, 10) == 160.0
    assert calc_withdraw_pressure(200, 0.5, 10) == 110.0


def test_warning_level_checks_each_member_and_returns_trigger():
    safe = _member(1, "Person A", 300, 250, 160)
    weak = _member(2, "Person B", 280, 135, 140)
    warning = get_warning_level(_troop(safe, weak))
    assert warning.level == "red"
    assert warning.member is weak


def test_warning_level_uses_individual_yellow_threshold():
    a = _member(1, "Person A", 300, 230, 160)
    b = _member(2, "Person B", 260, 190, 140)
    warning = get_warning_level(_troop(a, b))
    assert warning.level == "yellow"
    assert warning.member is b


def test_free_text_members_do_not_cross_contaminate_pressure_readings():
    a = _member(11, "Freitext A", 300, 300, 160)
    b = _member(12, "Freitext B", 300, 300, 160)
    troop = _troop(a, b)
    db = FakeDB()

    log = log_pressure(db, troop, troop_member_id=11, pressure_bar=210)

    assert log.troop_member_id == 11
    assert log.member_id is None
    assert a.current_press == 210
    assert b.current_press == 300


def test_two_third_seconds_property_and_warning():
    troop = _timer_troop(offset_minutes=23, planned_minutes=33)
    assert troop.two_third_seconds == 1320
    assert get_time_warning(troop) == "two_third_due"


def _timer_troop(offset_minutes: int, planned_minutes: int) -> BreathingTroop:
    troop = _troop()
    troop.status = "im_einsatz"
    troop.planned_duration_min = planned_minutes
    troop.entry_at = datetime.now(UTC) - timedelta(minutes=offset_minutes)
    troop.last_meldung_at = None
    troop.warn_one_third_acked_at = None
    troop.warn_two_third_acked_at = None
    troop.warn_max_time_acked_at = None
    return troop


def test_report_only_suppresses_stage_when_after_its_due_time():
    troop = _timer_troop(offset_minutes=12, planned_minutes=33)
    troop.last_meldung_at = troop.entry_at + timedelta(minutes=5)
    assert get_time_warning(troop) == "one_third_due"
    troop.last_meldung_at = troop.entry_at + timedelta(minutes=11, seconds=1)
    assert get_time_warning(troop) == "ok"


def test_acknowledgement_does_not_suppress_worsening_pressure(monkeypatch):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", 300, 150, 160)
    troop = _troop(member)
    monkeypatch.setattr(svc, "write_incident_change", lambda *args, **kwargs: None)
    ack_warning(FakeDB(), troop, "withdraw")
    assert check_troop_warnings(troop) == []

    member.current_press = 140
    assert check_troop_warnings(troop) == ["withdraw"]


def test_acknowledged_one_third_does_not_suppress_two_thirds():
    troop = _timer_troop(offset_minutes=23, planned_minutes=33)
    troop.warn_one_third_acked_at = datetime.now(UTC)
    assert get_time_warning(troop) == "two_third_due"


def test_report_objective_reached_uses_consumption_and_reserve(monkeypatch):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", 300, 300, 160)
    troop = _troop(member)
    monkeypatch.setattr(svc, "_withdraw_settings", lambda db, value: (0.5, 50))
    monkeypatch.setattr(svc, "write_incident_change", lambda *args, **kwargs: None)

    report_objective_reached(FakeDB(), troop, 1, 220)
    assert member.withdraw_press == 140  # 2 * 220 - 300
    assert member.current_press == 220
    assert member.objective_press_at is not None

    report_objective_reached(FakeDB(), troop, 1, 160)
    assert member.withdraw_press == 50  # Mindestreserve


def test_create_troop_rejects_fewer_than_two_members():
    with pytest.raises(ValueError, match="mindestens 2"):
        create_troop(FakeDB(), 1, "Trupp", [{"free_text_name": "Nur eine Person"}])


def test_start_troop_rejects_fewer_than_two_members():
    troop = SimpleNamespace(members=[SimpleNamespace(start_press=300)])
    with pytest.raises(ValueError, match="mindestens 2"):
        start_troop(FakeDB(), troop)


def test_readiness_reports_missing_sicherheitstrupp():
    troop = _troop(
        _member(1, "Person A", 300, 300, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    issues = check_start_readiness(ReadinessDB(), troop)
    assert any("Kein bereiter Sicherheitstrupp" in issue for issue in issues)


def test_sicherheitstrupp_needs_no_own_standby():
    troop = _troop(
        _member(1, "Person A", 300, 300, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    troop.is_sicherheitstrupp = True
    db = ReadinessDB()
    assert check_start_readiness(db, troop) == []
    assert db.query_count == 0


def test_readiness_reports_pressure_below_ninety_percent():
    troop = _troop(
        _member(1, "Person A", 260, 260, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    issues = check_start_readiness(ReadinessDB(standby=object()), troop)
    assert any("Person A" in issue and "270 bar" in issue for issue in issues)


def test_manual_bottle_skips_pressure_readiness_check():
    troop = _troop(
        _member(1, "Person A", 100, 100, 60),
        _member(2, "Person B", 100, 100, 60),
    )
    troop.bottle_preset = "manuell"
    assert check_start_readiness(ReadinessDB(standby=object()), troop) == []


def test_readiness_is_empty_when_all_requirements_are_met():
    troop = _troop(
        _member(1, "Person A", 270, 270, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    assert check_start_readiness(ReadinessDB(standby=object()), troop) == []


def test_start_troop_raises_readiness_warning_without_override():
    troop = _troop(
        _member(1, "Person A", 260, 260, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    with pytest.raises(ReadinessWarning) as exc_info:
        start_troop(ReadinessDB(), troop)
    assert len(exc_info.value.issues) == 2
    assert troop.status != "im_einsatz"


def test_start_troop_override_starts_and_sets_audit_fields(monkeypatch):
    import app.services.breathing_service as svc

    troop = _troop(
        _member(1, "Person A", 260, 260, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    troop.status = "bereit"
    events = []
    monkeypatch.setattr(svc, "_withdraw_settings", lambda db, value: (0.5, 10))
    monkeypatch.setattr(
        svc, "write_incident_change",
        lambda db, incident_id, event, *args, **kwargs: events.append((event, kwargs)),
    )

    start_troop(
        ReadinessDB(), troop,
        override_reason="Gefahr weitgehend ausgeschlossen",
        override_user_id=42,
    )

    assert troop.status == "im_einsatz"
    assert troop.readiness_override_reason == "Gefahr weitgehend ausgeschlossen"
    assert troop.readiness_override_by_user_id == 42
    assert troop.readiness_override_at is not None
    override_event = next(item for item in events if item[0] == "troop.readiness_overridden")
    assert override_event[1]["after"]["reason"] == "Gefahr weitgehend ausgeschlossen"
    assert len(override_event[1]["after"]["issues"]) == 2
