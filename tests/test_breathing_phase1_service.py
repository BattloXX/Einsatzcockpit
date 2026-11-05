"""Phase 1: Druckabfragen, Notfallflags und Bereitschaftsprüfung."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.sql import operators

from app.models.breathing import BreathingDeployment, BreathingKontrolle, BreathingTroop, PressureLog
from app.models.incident import Incident
from app.models.master import OrgSettings
from app.services import breathing_service as svc
from tests.test_breathing import FakeDB, _member, _troop

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *conditions):
        for condition in conditions:
            key = condition.left.key
            value = getattr(condition.right, "value", None)
            if condition.operator is operators.in_op:
                self.rows = [row for row in self.rows if getattr(row, key) in value]
            elif condition.operator is operators.is_:
                self.rows = [row for row in self.rows if getattr(row, key) is True]
            else:
                self.rows = [row for row in self.rows if condition.operator(getattr(row, key), value)]
        return self

    def first(self):
        return next(iter(self.rows), None)

    def all(self):
        return self.rows


class ServiceDB(FakeDB):
    def __init__(self, troop, org_id=None, settings=None, others=()):
        super().__init__()
        self.troop = troop
        self.incident = SimpleNamespace(primary_org_id=org_id)
        self.settings = settings
        self.others = list(others)

    def get(self, model, ident):
        assert model is Incident and ident == self.troop.incident_id
        return self.incident

    def query(self, model):
        if model is OrgSettings:
            return Query([self.settings] if self.settings is not None else [])
        assert model is BreathingTroop
        return Query([self.troop, *self.others])

    def flush(self):
        for row in self.added:
            if isinstance(row, PressureLog) and row not in self.troop.pressure_logs:
                row.ts = row.ts or svc._now()
                row.troop_member = next(m for m in self.troop.active_members if m.id == row.troop_member_id)
                row.troop = self.troop


@pytest.fixture(autouse=True)
def fixed_time_and_audit(monkeypatch):
    events = []
    monkeypatch.setattr(svc, "_now", lambda: NOW)
    monkeypatch.setattr(
        svc, "write_incident_change",
        lambda db, incident_id, event, *args, **kwargs: events.append((event, kwargs)),
    )
    return events


def live_troop(status="im_einsatz"):
    troop = _troop(_member(1, "Anna", 300, 280, 60), _member(2, "Berta", 300, 250, 60))
    troop.status = status
    troop.is_sicherheitstrupp = True
    troop.planned_duration_min = 30
    troop.entry_at = NOW - timedelta(minutes=11)
    return troop


@pytest.mark.parametrize(("org_id", "floor"), [(None, 60), (42, None), (42, 55)])
def test_start_uses_default_or_org_floor(org_id, floor):
    troop = live_troop("bereit")
    settings = OrgSettings(org_id=42, asuew_rueckzug_untergrenze_bar=floor) if org_id else None
    db = ServiceDB(troop, org_id, settings)
    svc.start_troop(db, troop)
    expected = float(floor if floor is not None else 60)
    assert troop.withdraw_press_calc == expected
    assert all(member.withdraw_press == expected for member in troop.active_members)
    assert all(isinstance(member.withdraw_press, float) for member in troop.active_members)
    assert troop.start_press_avg == 300
    assert [member.current_press for member in troop.active_members] == [300, 300]
    assert troop.entry_at == NOW and troop.status == "im_einsatz"


def test_start_handles_zero_and_missing_start_pressure():
    troop = live_troop("bereit")
    troop.bottle_preset = "manuell"
    troop.members[0].start_press = 0
    troop.members[1].start_press = None
    troop.members[1].withdraw_press = None
    svc.start_troop(ServiceDB(troop), troop)
    assert troop.members[0].withdraw_press == 60.0
    assert troop.members[0].current_press == 0
    assert troop.members[1].withdraw_press is None
    assert troop.start_press_avg == 0


@pytest.mark.parametrize(("pressure", "expected"), [(290, 80), (220, 160)])
def test_objective_uses_org_floor(pressure, expected):
    troop = live_troop()
    db = ServiceDB(troop, 42, OrgSettings(org_id=42, asuew_rueckzug_untergrenze_bar=80))
    member = svc.report_objective_reached(db, troop, 1, pressure)
    assert member.withdraw_press == expected
    assert member.objective_press_at == NOW
    assert member.current_press == pressure


@pytest.mark.parametrize(("percent", "pressure", "expected"), [
    (75, 226, "ok"), (75, 225, "yellow"), (80, 240, "yellow"),
    (80, 241, "ok"), (50, 151, "ok"), (50, 150, "yellow"), (50, 60, "red"),
])
def test_configurable_yellow_threshold(percent, pressure, expected):
    troop = live_troop()
    for member in troop.members:
        member.current_press = pressure
    assert svc.get_warning_level(troop, percent).level == expected


@pytest.mark.parametrize(("stufe", "offset", "due_minutes"), [
    ("one_third", 11, 10), ("two_third", 21, 20), ("frei", 11, None),
])
def test_funk_kontrolle_records_pressure_and_due_time(stufe, offset, due_minutes, fixed_time_and_audit):
    troop = live_troop()
    troop.entry_at = NOW - timedelta(minutes=offset)
    db = ServiceDB(troop)
    kontrolle = svc.record_kontrolle(db, troop, stufe, "funk", 200, user_id=17)
    assert isinstance(kontrolle, BreathingKontrolle)
    assert kontrolle in troop.active_kontrollen
    assert kontrolle.erledigt_at == NOW
    expected_due = troop.entry_at + timedelta(minutes=due_minutes) if due_minutes else None
    assert kontrolle.faellig_at == expected_due
    assert kontrolle.niedrigster_druck == 200
    assert kontrolle.troop_member_id == 2 and kontrolle.user_id == 17
    assert troop.members[1].current_press == 200
    assert troop.members[0].current_press == 280
    assert troop.last_meldung_at == NOW
    assert len(troop.active_pressure_logs) == 1
    log = troop.active_pressure_logs[0]
    assert log.pressure_bar == 200 and log.recorded_by_user_id == 17
    assert log.note.startswith("Kontrolle ")
    assert svc.get_time_warnings(troop) == []
    assert fixed_time_and_audit[-1][0] == "troop.kontrolle"
    assert fixed_time_and_audit[-1][1]["user_id"] == 17


@pytest.mark.parametrize("explicit", [False, True])
def test_kontrolle_member_selection_with_missing_pressures(explicit):
    troop = live_troop()
    for member in troop.members:
        member.current_press = None
    kontrolle = svc.record_kontrolle(
        ServiceDB(troop), troop, "one_third", "funk", 220,
        troop_member_id=2 if explicit else None, note="Lage stabil",
    )
    assert kontrolle.troop_member_id == (2 if explicit else 1)
    assert kontrolle.note == "Lage stabil"
    assert troop.pressure_logs[0].note == "Lage stabil"


def test_text_and_legacy_ack_do_not_complete_controls():
    troop = live_troop()
    svc.update_meldung(ServiceDB(troop), troop, "Alles gut")
    troop.warn_one_third_acked_at = NOW
    assert troop.last_meldung_text == "Alles gut"
    assert svc.get_time_warnings(troop) == ["one_third_due"]
    troop.entry_at = NOW - timedelta(minutes=21)
    troop.warn_two_third_acked_at = NOW
    assert svc.get_time_warnings(troop) == ["two_third_due", "one_third_due"]


@pytest.mark.parametrize("naive", [False, True])
@pytest.mark.parametrize(("seconds_after_due", "expected"), [
    (-1, ["one_third_due"]), (0, []), (1, []),
])
def test_pressure_log_completes_control_only_from_due_time(naive, seconds_after_due, expected):
    troop = live_troop()
    ts = troop.entry_at + timedelta(minutes=10, seconds=seconds_after_due)
    if naive:
        troop.entry_at = troop.entry_at.replace(tzinfo=None)
        ts = ts.replace(tzinfo=None)
    troop.pressure_logs = [PressureLog(troop_member=troop.members[0], ts=ts, pressure_bar=230)]
    assert svc.get_time_warnings(troop) == expected


def test_only_active_matching_funk_kontrollen_or_active_logs_complete_stage():
    troop = live_troop()
    troop.kontrollen = [
        BreathingKontrolle(stufe="one_third", art="funk", deployment_id=99, erledigt_at=NOW),
        BreathingKontrolle(stufe="two_third", art="funk", erledigt_at=NOW),
        BreathingKontrolle(stufe="frei", art="funk", erledigt_at=NOW),
    ]
    old_member = _member(3, "Alt", 300, 200, 60)
    old_member.deployment_id = 99
    troop.pressure_logs = [PressureLog(troop_member=old_member, ts=NOW, pressure_bar=200)]
    assert svc.get_time_warnings(troop) == ["one_third_due"]
    troop.kontrollen.append(BreathingKontrolle(stufe="one_third", art="funk", erledigt_at=NOW))
    assert svc.get_time_warnings(troop) == []


@pytest.mark.parametrize("stufe", ["one_third", "two_third"])
def test_ack_controls_rejected(stufe, fixed_time_and_audit):
    troop = live_troop()
    db = ServiceDB(troop)
    with pytest.raises(ValueError, match="Die Kontrolle erfolgt über Druckabfrage, nicht über Quittieren."):
        svc.ack_warning(db, troop, stufe)
    assert troop.warn_one_third_acked_at is troop.warn_two_third_acked_at is None
    assert db.added == [] and fixed_time_and_audit == []


def test_kein_kontakt_does_not_complete_control(fixed_time_and_audit):
    troop = live_troop()
    db = ServiceDB(troop)
    kontrolle = svc.record_kontrolle(db, troop, "one_third", "kein_kontakt", user_id=17)
    assert kontrolle.erledigt_at == troop.funk_verloren_at == NOW
    assert kontrolle.niedrigster_druck is None
    assert not troop.pressure_logs
    assert troop.last_meldung_at is None
    assert svc.check_troop_warnings(troop) == ["funk_verloren", "one_third_due"]
    assert troop.status == "im_einsatz"
    assert fixed_time_and_audit[-1][0] == "troop.kontrolle"


@pytest.mark.parametrize("status", ["im_einsatz", "rueckzug"])
def test_funk_and_notfall_flags_flow(status, fixed_time_and_audit):
    troop = live_troop(status)
    troop.members[0].current_press = 50
    db = ServiceDB(troop)
    svc.set_funk_verloren(db, troop, True, user_id=7)
    assert troop.funk_verloren_at == NOW
    assert svc.check_troop_warnings(troop) == ["funk_verloren", "withdraw", "one_third_due"]
    svc.declare_notfall(db, troop, user_id=17)
    assert troop.notfall_at == NOW and troop.notfall_von_user_id == 17
    assert svc.check_troop_warnings(troop) == ["notfall", "withdraw", "one_third_due"]
    svc.clear_notfall(db, troop, user_id=18)
    assert troop.notfall_at is troop.notfall_von_user_id is None
    assert svc.check_troop_warnings(troop)[0] == "funk_verloren"
    svc.set_funk_verloren(db, troop, False, user_id=19)
    assert troop.funk_verloren_at is None
    assert svc.check_troop_warnings(troop) == ["withdraw", "one_third_due"]
    assert troop.status == status
    assert [event for event, _ in fixed_time_and_audit] == [
        "troop.funk_verloren", "troop.notfall", "troop.notfall_cleared", "troop.funk_verloren",
    ]
    assert [kwargs["user_id"] for _, kwargs in fixed_time_and_audit] == [7, 17, 18, 19]


def test_notfall_is_idempotent(monkeypatch, fixed_time_and_audit):
    troop = live_troop()
    db = ServiceDB(troop)
    svc.declare_notfall(db, troop, user_id=17)
    monkeypatch.setattr(svc, "_now", lambda: NOW + timedelta(minutes=5))
    svc.declare_notfall(db, troop, user_id=18)
    assert troop.notfall_at == NOW and troop.notfall_von_user_id == 17
    assert [event for event, _ in fixed_time_and_audit] == ["troop.notfall"]
    assert troop.status == "im_einsatz"


@pytest.mark.parametrize("status", ["bereit", "zurueck", "erholt"])
def test_flags_reject_activation_but_allow_clearing_after_return(status):
    troop = live_troop(status)
    db = ServiceDB(troop)
    with pytest.raises(ValueError):
        svc.set_funk_verloren(db, troop, True)
    with pytest.raises(ValueError):
        svc.declare_notfall(db, troop)
    assert troop.funk_verloren_at is troop.notfall_at is None
    troop.funk_verloren_at = troop.notfall_at = NOW
    troop.notfall_von_user_id = 17
    svc.set_funk_verloren(db, troop, False)
    svc.clear_notfall(db, troop)
    assert troop.funk_verloren_at is troop.notfall_at is troop.notfall_von_user_id is None
    assert troop.status == status


@pytest.mark.parametrize("naive", [False, True])
@pytest.mark.parametrize(("pause", "elapsed", "expected"), [
    (30, 10, ["Erholzeit nicht eingehalten: noch 20 Min. (empfohlen 30 Min. Pause)"]),
    (45, 10, ["Erholzeit nicht eingehalten: noch 35 Min. (empfohlen 45 Min. Pause)"]),
    (0, 0, []), (30, 30, []), (30, 31, []),
])
def test_recovery_readiness(naive, pause, elapsed, expected):
    troop = live_troop("bereit")
    back_at = NOW - timedelta(minutes=elapsed)
    if naive:
        back_at = back_at.replace(tzinfo=None)
    troop.deployments = [BreathingDeployment(back_at=back_at)]
    db = ServiceDB(troop, 42, OrgSettings(org_id=42, asuew_erholzeit_min=pause))
    assert svc.check_start_readiness(db, troop) == expected


def test_recovery_default_rounds_up_and_can_be_overridden():
    troop = live_troop("bereit")
    troop.deployments = [BreathingDeployment(back_at=NOW - timedelta(minutes=10, seconds=30))]
    db = ServiceDB(troop)
    with pytest.raises(svc.ReadinessWarning) as exc:
        svc.start_troop(db, troop)
    assert exc.value.issues == ["Erholzeit nicht eingehalten: noch 20 Min. (empfohlen 30 Min. Pause)"]
    assert troop.status == "bereit"
    svc.start_troop(db, troop, override_reason="Begründete Ausnahme", override_user_id=17)
    assert troop.status == "im_einsatz"
    assert troop.readiness_override_reason == "Begründete Ausnahme"
    assert troop.readiness_override_by_user_id == 17


def test_recovery_uses_only_latest_deployment_with_back_time():
    troop = live_troop("bereit")
    troop.deployments = [BreathingDeployment(back_at=NOW), BreathingDeployment(back_at=None)]
    assert svc.check_start_readiness(ServiceDB(troop), troop) == []


@pytest.mark.parametrize(("incident_id", "status", "expected"), [
    (1, "im_einsatz", ["Anna ist bereits in Trupp Trupp 2 im Einsatz."]),
    (1, "rueckzug", ["Anna ist bereits in Trupp Trupp 2 im Einsatz."]),
    (2, "im_einsatz", []), (1, "bereit", []), (1, "zurueck", []), (1, "erholt", []),
])
def test_double_assignment_readiness(incident_id, status, expected):
    troop = live_troop("bereit")
    troop.members[0].member_id = 42
    other = live_troop(status)
    other.id = 2
    other.name = "Trupp 2"
    other.incident_id = incident_id
    other.members[0].member_id = 42
    db = ServiceDB(troop, others=[other])
    assert svc.check_start_readiness(db, troop) == expected
    if expected:
        with pytest.raises(svc.ReadinessWarning) as exc:
            svc.start_troop(db, troop)
        assert exc.value.issues == expected
        svc.start_troop(db, troop, override_reason="Ablösung bestätigt", override_user_id=17)
        assert troop.status == "im_einsatz"
        assert troop.readiness_override_reason == "Ablösung bestätigt"


def test_double_assignment_ignores_archived_members_and_free_text():
    troop = live_troop("bereit")
    troop.members[0].member_id = 42
    other = live_troop()
    other.id = 2
    other.members[0].member_id = 42
    other.members[0].deployment_id = 99
    assert svc.check_start_readiness(ServiceDB(troop, others=[other]), troop) == []
    troop.members[0].deployment_id = 99
    other.members[0].deployment_id = None
    assert svc.check_start_readiness(ServiceDB(troop, others=[other]), troop) == []


@pytest.mark.parametrize("status", ["bereit", "zurueck", "erholt"])
@pytest.mark.parametrize("art", ["funk", "kein_kontakt"])
def test_kontrolle_rejects_inactive_status(status, art):
    troop = live_troop(status)
    db = ServiceDB(troop)
    with pytest.raises(ValueError, match="nur im Einsatz oder Rückzug"):
        svc.record_kontrolle(db, troop, "one_third", art, 200)
    assert db.added == [] and troop.kontrollen == []


@pytest.mark.parametrize(("stufe", "art", "pressure", "member_id"), [
    ("ungültig", "funk", 200, None), ("one_third", "ungültig", 200, None),
    ("one_third", "funk", None, None), ("one_third", "funk", -1, None),
    ("one_third", "funk", 331, None), ("one_third", "funk", float("nan"), None),
    ("one_third", "funk", float("inf"), None), ("one_third", "funk", 200, 99),
])
def test_kontrolle_validates_before_mutation(stufe, art, pressure, member_id, fixed_time_and_audit):
    troop = live_troop()
    db = ServiceDB(troop)
    with pytest.raises(ValueError):
        svc.record_kontrolle(db, troop, stufe, art, pressure, troop_member_id=member_id)
    assert db.added == [] and troop.kontrollen == []
    assert troop.last_meldung_at is None and fixed_time_and_audit == []
    assert [member.current_press for member in troop.members] == [280, 250]


def test_pressure_and_kontrolle_visible_before_and_after_reload():
    from app.core.tenant import set_tenant_context
    from app.models.breathing import TroopMember
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        troop = BreathingTroop(
            incident_id=1, name="Integration", status="im_einsatz", planned_duration_min=30,
            entry_at=NOW - timedelta(minutes=11),
        )
        member = TroopMember(troop=troop, free_text_name="Anna", start_press=300, current_press=250)
        db.add(troop)
        db.flush()
        assert troop.active_pressure_logs == []
        assert troop.active_kontrollen == []
        assert svc.get_time_warnings(troop) == ["one_third_due"]
        log = svc.log_pressure(db, troop, member.id, 240)
        assert troop.active_pressure_logs == [log]
        assert svc.get_time_warnings(troop) == []
        db.expire(troop, ["pressure_logs"])
        assert svc.get_time_warnings(troop) == []
        kontrolle = svc.record_kontrolle(db, troop, "one_third", "funk", 230, user_id=1)
        assert kontrolle in troop.active_kontrollen
        db.expire(troop, ["kontrollen", "pressure_logs"])
        assert svc.get_time_warnings(troop) == []
        assert troop.active_kontrollen[0].niedrigster_druck == 230
        assert member.current_press == 230
        assert len(troop.active_pressure_logs) == 2
    finally:
        db.rollback()
        db.close()
