"""Tests für die sicherheitskritische Atemschutzlogik (Phase 1 bis 4)."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.breathing import BreathingTroop, PressureLog, TroopMember
from app.services.breathing_service import (
    InvalidTroopTransition,
    ReadinessWarning,
    _collect_new_breathing_warnings,
    ack_warning,
    breathing_effective_enabled,
    breathing_system_enabled,
    calc_withdraw_pressure,
    check_and_escalate_troop,
    check_start_readiness,
    check_troop_warnings,
    create_troop,
    get_time_warning,
    get_troop_cycles,
    get_warning_level,
    log_pressure,
    redeploy_troop,
    report_back_pressure,
    report_objective_reached,
    start_troop,
    update_troop_status,
    validate_pressure,
)


class _SystemFlag:
    def __init__(self, value: str):
        self.value = value


class _OrgFlag:
    def __init__(self, enabled: bool):
        self.atemschutz_ueberwachung_modul_aktiv = enabled


def test_breathing_system_enabled_defaults_to_true_when_missing():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert breathing_system_enabled(db) is True


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("true", True)])
def test_breathing_system_enabled_uses_stored_value(value, expected):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _SystemFlag(value)
    assert breathing_system_enabled(db) is expected


def test_breathing_effective_enabled_rejects_missing_org_id():
    assert breathing_effective_enabled(None, MagicMock()) is False


def test_breathing_effective_enabled_rejects_system_off():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _SystemFlag("false")
    assert breathing_effective_enabled(1, db) is False


@pytest.mark.parametrize(("org_row", "expected"), [(_OrgFlag(False), False), (_OrgFlag(True), True), (None, True)])
def test_breathing_effective_enabled_respects_org_flag_and_missing_default(org_row, expected):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        _SystemFlag("true"),
        org_row,
    ]
    assert breathing_effective_enabled(1, db) is expected


def test_breathing_route_returns_404_when_module_disabled():
    from app.db import get_db
    from app.routers.ui_breathing import router

    test_app = FastAPI()

    @test_app.middleware("http")
    async def disable_breathing(request, call_next):
        request.state.breathing_module_enabled = False
        return await call_next(request)

    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = lambda: MagicMock()

    with TestClient(test_app) as client:
        response = client.get("/einsatz/1/atemschutz")
    assert response.status_code == 404


def test_watchdog_skips_incident_when_org_module_disabled(monkeypatch):
    import app.services.breathing_service as svc

    incident = SimpleNamespace(id=7, primary_org_id=23)
    query = MagicMock()
    query.filter.return_value.all.return_value = [incident]
    db = MagicMock()
    db.query.return_value = query
    monkeypatch.setattr(svc, "breathing_effective_enabled", lambda org_id, session: False)

    assert _collect_new_breathing_warnings(db, {}) == []
    db.refresh.assert_not_called()
    db.get.assert_not_called()


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass

    def get(self, model, ident):
        return None


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
    member.deployment_id = None
    return member


def _troop(*members: TroopMember) -> BreathingTroop:
    troop = BreathingTroop()
    troop.id = 1
    troop.incident_id = 1
    troop.members = list(members)
    troop.deployments = []
    troop.pressure_logs = []
    troop.warn_withdraw_acked_at = None
    troop.warn_withdraw_acked_press = None
    troop.is_sicherheitstrupp = False
    troop.bottle_preset = "1x6"
    troop.readiness_override_reason = None
    troop.readiness_override_by_user_id = None
    troop.readiness_override_at = None
    troop.escalated_at = None
    troop.name = "Trupp 1"
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
    troop.status = "im_einsatz"
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


def test_text_report_does_not_suppress_stage_even_after_its_due_time():
    troop = _timer_troop(offset_minutes=12, planned_minutes=33)
    troop.last_meldung_at = troop.entry_at + timedelta(minutes=5)
    assert get_time_warning(troop) == "one_third_due"
    troop.last_meldung_at = troop.entry_at + timedelta(minutes=11, seconds=1)
    assert get_time_warning(troop) == "one_third_due"


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


@pytest.mark.parametrize(("start", "objective", "expected"), [
    (300, 270, 60),
    (270, 180, 180),
    (300, 220, 160),
    (280, 255, 60),
    (280, 300, 60),
])
def test_report_objective_reached_uses_consumption_and_warning_floor(
    monkeypatch, start, objective, expected,
):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", start, start, 160)
    troop = _troop(member)
    troop.status = "im_einsatz"
    monkeypatch.setattr(svc, "write_incident_change", lambda *args, **kwargs: None)

    report_objective_reached(FakeDB(), troop, 1, objective)
    assert member.withdraw_press == expected
    assert member.current_press == objective
    assert member.objective_press == objective
    assert member.objective_press_at is not None


def test_create_troop_rejects_fewer_than_two_members():
    with pytest.raises(ValueError, match="mindestens 2"):
        create_troop(FakeDB(), 1, "Trupp", [{"free_text_name": "Nur eine Person"}])


def test_start_troop_rejects_fewer_than_two_members():
    troop = _troop(_member(1, "Person A", 300, 300, 160))
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


def test_report_back_pressure_sets_value_and_writes_audit(monkeypatch):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", 300, 120, 160)
    troop = _troop(member)
    troop.status = "zurueck"
    events = []
    monkeypatch.setattr(
        svc, "write_incident_change",
        lambda db, incident_id, event, *args, **kwargs: events.append((event, kwargs)),
    )

    result = report_back_pressure(FakeDB(), troop, 1, 80, recorded_by_user_id=42)

    assert result is member
    assert member.back_press == 80
    assert events == [(
        "troop.back_pressure_reported",
        {"before": None, "after": {"pressure_bar": 80}, "user_id": 42},
    )]


def test_escalation_fires_once_and_again_after_ack_reset(monkeypatch):
    import app.services.breathing_service as svc

    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    troop = _timer_troop(offset_minutes=14, planned_minutes=10)
    troop.entry_at = now - timedelta(minutes=14)
    incident = SimpleNamespace(id=7, incident_leader_user_id=99)
    notifications = []

    def notifier(*args, **kwargs):
        notifications.append((args, kwargs))

    assert check_and_escalate_troop(
        FakeDB(), incident, troop, 3, now=now, notifier=notifier,
    ) is True
    assert troop.escalated_at == now
    assert len(notifications) == 1

    assert check_and_escalate_troop(
        FakeDB(), incident, troop, 3, now=now + timedelta(minutes=1), notifier=notifier,
    ) is False
    assert len(notifications) == 1

    monkeypatch.setattr(svc, "write_incident_change", lambda *args, **kwargs: None)
    ack_warning(FakeDB(), troop, "max_time")
    assert troop.escalated_at is None
    troop.warn_max_time_acked_at = None

    assert check_and_escalate_troop(
        FakeDB(), incident, troop, 3, now=now + timedelta(minutes=2), notifier=notifier,
    ) is True
    assert len(notifications) == 2


def test_redeploy_rejects_troop_that_is_not_recovered():
    troop = _troop(
        _member(1, "Person A", 300, 300, 160),
        _member(2, "Person B", 300, 300, 160),
    )
    troop.status = "zurueck"
    with pytest.raises(ValueError, match="erholter"):
        redeploy_troop(FakeDB(), troop, [])


def test_get_troop_cycles_returns_unused_troop_only():
    troop = _troop()
    troop.entry_at = None
    assert get_troop_cycles(troop) == [troop]


def test_redeploy_archives_cycles_and_allows_roster_change(monkeypatch):
    import app.services.breathing_service as svc
    from app.core.tenant import set_tenant_context
    from tests.conftest import TestingSession

    monkeypatch.setattr(svc, "write_incident_change", lambda *args, **kwargs: None)
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        troop = BreathingTroop(
            incident_id=1, name="Mehrfachtrupp", status="erholt",
            is_sicherheitstrupp=True, bottle_preset="1x6", planned_duration_min=33,
            entry_at=datetime.now(UTC) - timedelta(minutes=20),
            back_at=datetime.now(UTC), start_press_avg=300, withdraw_press_calc=160,
            warn_one_third_acked_at=datetime.now(UTC),
            last_meldung_at=datetime.now(UTC), last_meldung_text="Zyklus 1",
        )
        member_a = TroopMember(
            troop=troop, free_text_name="Person A", role="truppfuehrer",
            start_press=300, current_press=100, deployment_id=None,
        )
        partner = TroopMember(
            troop=troop, free_text_name="Partner", role="truppmann",
            start_press=300, current_press=110, deployment_id=None,
        )
        db.add(troop)
        db.flush()
        log_a = PressureLog(
            troop=troop, troop_member=member_a, pressure_bar=200, note="Zyklus 1"
        )
        db.add(log_a)
        db.flush()

        redeploy_troop(db, troop, [
            {"free_text_name": "Person B", "role": "truppfuehrer", "start_press": 300},
            {"free_text_name": "Partner", "role": "truppmann", "start_press": 290},
        ])
        db.flush()

        first = troop.deployments[0]
        assert first.lfd_nr == 1
        assert {member.display_name for member in first.members} == {"Person A", "Partner"}
        assert [log.note for log in first.pressure_logs] == ["Zyklus 1"]
        assert member_a.deployment_id == first.id
        assert partner.deployment_id == first.id
        assert {member.display_name for member in troop.active_members} == {"Person B", "Partner"}
        assert all(member.deployment_id is None for member in troop.active_members)
        assert troop.status == "bereit"
        for field in (
            "entry_at", "withdraw_at", "back_at", "warn_one_third_acked_at",
            "warn_two_third_acked_at", "warn_max_time_acked_at", "warn_withdraw_acked_at",
            "warn_withdraw_acked_press", "readiness_override_reason",
            "readiness_override_by_user_id", "readiness_override_at", "escalated_at",
            "start_press_avg", "withdraw_press_calc", "last_meldung_at", "last_meldung_text",
        ):
            assert getattr(troop, field) is None
        assert get_troop_cycles(troop) == [first, troop]

        # Die bestehende Startlogik prüft und verwendet ausschließlich die neue Besetzung.
        with pytest.raises(ReadinessWarning) as exc_info:
            start_troop(db, troop)
        assert len(exc_info.value.issues) == 1
        assert "Erholzeit nicht eingehalten" in exc_info.value.issues[0]
        start_troop(db, troop, override_reason="Besetzung vollständig gewechselt", override_user_id=1)
        assert troop.status == "im_einsatz"
        assert {member.current_press for member in troop.active_members} == {290, 300}

        # Zweiten Zyklus abschließen und archivieren.
        troop.status = "erholt"
        troop.back_at = datetime.now(UTC)
        second_live_member = next(
            member for member in troop.active_members if member.display_name == "Person B"
        )
        db.add(PressureLog(
            troop=troop, troop_member=second_live_member, pressure_bar=180, note="Zyklus 2"
        ))
        db.flush()
        redeploy_troop(db, troop, [
            {"free_text_name": "Person C", "role": "truppfuehrer", "start_press": 300},
            {"free_text_name": "Partner", "role": "truppmann", "start_press": 300},
        ])
        db.flush()

        second = troop.deployments[1]
        assert second.lfd_nr == 2
        assert "Person A" not in {member.display_name for member in second.members}
        assert "Person B" not in {member.display_name for member in first.members}
        assert [log.note for log in second.pressure_logs] == ["Zyklus 2"]
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("standby_status", ["bereit", "im_einsatz"])
def test_readiness_counts_only_ready_sicherheitstrupp(standby_status):
    from app.core.tenant import set_tenant_context
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        troop = BreathingTroop(incident_id=1, name="Angriffstrupp", status="bereit")
        standby = BreathingTroop(
            incident_id=1, name="Sicherheitstrupp", status=standby_status,
            is_sicherheitstrupp=True,
        )
        db.add_all([troop, standby])
        db.flush()
        issues = check_start_readiness(db, troop)
        if standby_status == "bereit":
            assert issues == []
        else:
            assert issues == ["Kein bereiter Sicherheitstrupp für diesen Einsatz vorhanden."]
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize(("old_status", "new_status", "timestamp_field"), [
    ("im_einsatz", "rueckzug", "withdraw_at"),
    ("im_einsatz", "zurueck", "back_at"),
    ("rueckzug", "zurueck", "back_at"),
    ("zurueck", "erholt", None),
])
def test_update_troop_status_allows_transition(monkeypatch, old_status, new_status, timestamp_field):
    import app.services.breathing_service as svc

    troop = _troop()
    troop.status = old_status
    audit = MagicMock()
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(svc, "write_incident_change", audit)
    monkeypatch.setattr(svc, "_now", lambda: now)

    assert update_troop_status(FakeDB(), troop, new_status) is troop
    assert troop.status == new_status
    if timestamp_field:
        assert getattr(troop, timestamp_field) == now
    audit.assert_called_once()
    assert audit.call_args.kwargs["before"] == {"status": old_status}
    assert audit.call_args.kwargs["after"] == {"status": new_status}


@pytest.mark.parametrize(("old_status", "new_status"), [
    (old, new)
    for old in ("bereit", "im_einsatz", "rueckzug", "zurueck", "erholt")
    for new in ("bereit", "im_einsatz", "rueckzug", "zurueck", "erholt")
    if old != new and (old, new) not in {
        ("im_einsatz", "rueckzug"), ("im_einsatz", "zurueck"),
        ("rueckzug", "zurueck"), ("zurueck", "erholt"),
    }
])
def test_update_troop_status_rejects_transition(monkeypatch, old_status, new_status):
    import app.services.breathing_service as svc

    troop = _troop()
    troop.status = old_status
    db = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(svc, "write_incident_change", audit)

    with pytest.raises(InvalidTroopTransition):
        update_troop_status(db, troop, new_status)
    assert troop.status == old_status
    db.flush.assert_not_called()
    audit.assert_not_called()


def test_update_troop_status_rejects_unknown_status():
    troop = _troop()
    troop.status = "im_einsatz"
    with pytest.raises(ValueError, match="Unbekannter Truppstatus"):
        update_troop_status(FakeDB(), troop, "unbekannt")
    assert troop.status == "im_einsatz"


@pytest.mark.parametrize("status", ["bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"])
def test_update_troop_status_is_idempotent(monkeypatch, status):
    import app.services.breathing_service as svc

    troop = _troop()
    troop.status = status
    troop.withdraw_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    troop.back_at = troop.withdraw_at + timedelta(minutes=5)
    troop.escalated_at = troop.withdraw_at
    before = dict(vars(troop))
    db = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(svc, "write_incident_change", audit)

    assert update_troop_status(db, troop, status) is troop
    assert vars(troop) == before
    db.flush.assert_not_called()
    audit.assert_not_called()


@pytest.mark.parametrize("pressure", [float("nan"), float("inf"), -float("inf"), -1, 330.1])
def test_validate_pressure_rejects_invalid_values(pressure):
    with pytest.raises(ValueError):
        validate_pressure(pressure)


@pytest.mark.parametrize("pressure", [0, 60.5, 330])
def test_validate_pressure_accepts_valid_values(pressure):
    assert validate_pressure(pressure) == pressure


@pytest.mark.parametrize("report", [log_pressure, report_objective_reached, report_back_pressure])
@pytest.mark.parametrize("pressure", [float("nan"), float("inf"), -float("inf"), -0.1, 331])
def test_pressure_reports_reject_invalid_values_without_changes(monkeypatch, report, pressure):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", 300, 250, 160)
    troop = _troop(member)
    troop.status = "rueckzug"
    before_member = dict(vars(member))
    before_troop = dict(vars(troop))
    db = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(svc, "write_incident_change", audit)
    with pytest.raises(ValueError):
        report(db, troop, 1, pressure)
    assert vars(member) == before_member
    assert vars(troop) == before_troop
    db.add.assert_not_called()
    db.flush.assert_not_called()
    audit.assert_not_called()


@pytest.mark.parametrize(("report", "allowed_statuses"), [
    (log_pressure, {"im_einsatz", "rueckzug"}),
    (report_objective_reached, {"im_einsatz", "rueckzug"}),
    (report_back_pressure, {"zurueck", "erholt", "rueckzug"}),
])
@pytest.mark.parametrize("status", ["bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"])
@pytest.mark.parametrize("pressure", [0, 330])
def test_pressure_reports_check_status(monkeypatch, report, allowed_statuses, status, pressure):
    import app.services.breathing_service as svc

    member = _member(1, "Person A", 300, 250, 160)
    troop = _troop(member)
    troop.status = status
    db = FakeDB()
    audit = MagicMock()
    monkeypatch.setattr(svc, "write_incident_change", audit)
    if status in allowed_statuses:
        report(db, troop, 1, pressure)
        if report is report_back_pressure:
            assert member.back_press == pressure
        else:
            assert member.current_press == pressure
            assert db.added[0].pressure_bar == pressure
    else:
        before_member = dict(vars(member))
        before_troop = dict(vars(troop))
        with pytest.raises(ValueError):
            report(db, troop, 1, pressure)
        assert vars(member) == before_member
        assert vars(troop) == before_troop
        assert db.added == []
        audit.assert_not_called()


@pytest.mark.parametrize("pressure", [float("nan"), float("inf"), -float("inf"), -1, 331])
@pytest.mark.parametrize("operation", ["create", "redeploy"])
def test_member_start_pressure_is_validated_before_mutation(pressure, operation):
    troop = _troop()
    troop.status = "erholt"
    before = dict(vars(troop))
    members = [
        {"free_text_name": "Person A", "start_press": 300},
        {"free_text_name": "Person B", "start_press": pressure},
    ]
    db = MagicMock()
    with pytest.raises(ValueError):
        if operation == "create":
            create_troop(db, 1, "Trupp", members)
        else:
            redeploy_troop(db, troop, members)
    assert vars(troop) == before
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("pressure", [None, 0, 330])
def test_live_members_accept_valid_start_pressure(pressure):
    from app.services.breathing_service import _add_live_members

    members = _add_live_members(FakeDB(), _troop(), [
        {"free_text_name": "Person A", "start_press": pressure},
        {"free_text_name": "Person B", "start_press": 300},
    ])
    assert members[0].start_press == pressure
    assert members[0].current_press == pressure


async def test_watchdog_logs_throttled_errors_continues_and_updates_heartbeat(monkeypatch, caplog):
    import app.core.tenant as tenant
    import app.db as database
    import app.services.breathing_service as svc

    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(svc, "_watchdog_last_ok", None)
    monkeypatch.setattr(svc, "_now", lambda: now)
    ticks = iter([0.0, 59.0, 60.0])
    monkeypatch.setattr(svc, "monotonic", lambda: next(ticks))
    db = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(tenant, "set_tenant_context", lambda *args: None)
    scans = 0
    sleeps = 0

    def collect(session, sent):
        nonlocal scans
        scans += 1
        assert svc.get_watchdog_heartbeat() == (None if scans <= 3 else now)
        if scans in (1, 2, 4):
            raise RuntimeError("Scan fehlgeschlagen")
        return []

    async def sleep(delay):
        nonlocal sleeps
        assert delay == 5
        sleeps += 1
        if sleeps == 6:
            raise asyncio.CancelledError

    monkeypatch.setattr(svc, "_collect_new_breathing_warnings", collect)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    assert svc.get_watchdog_heartbeat() is None
    with caplog.at_level(logging.ERROR, logger="einsatzleiter.breathing"):
        with pytest.raises(asyncio.CancelledError):
            await svc._breathing_watchdog_loop()

    records = [record for record in caplog.records if record.name == "einsatzleiter.breathing"]
    assert len(records) == 2
    assert all(record.getMessage() == "Atemschutz-Watchdog: Fehler im Scan" for record in records)
    assert all(record.exc_info and record.exc_info[0] is RuntimeError for record in records)
    assert scans == 5
    assert db.close.call_count == 5
    assert svc.get_watchdog_heartbeat() == now
    assert svc.get_watchdog_heartbeat().tzinfo is UTC
