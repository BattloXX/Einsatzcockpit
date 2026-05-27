"""Tests für Atemschutz-Service."""
from datetime import UTC, datetime, timedelta

from app.services.breathing_service import (
    ack_warning,
    calc_withdraw_pressure,
    get_time_warning,
    get_warning_level,
)
from app.models.breathing import BreathingTroop, PressureLog


def test_withdraw_pressure_calculation():
    assert calc_withdraw_pressure(300, 0.5, 10) == 160.0
    assert calc_withdraw_pressure(200, 0.5, 10) == 110.0


def test_warning_level_ok():
    troop = BreathingTroop()
    troop.start_press_avg = 300
    troop.withdraw_press_calc = 160
    troop.pressure_logs = []
    assert get_warning_level(troop) == "ok"  # no logs → ok


def test_warning_level_yellow():
    troop = BreathingTroop()
    troop.start_press_avg = 300
    troop.withdraw_press_calc = 160
    log = PressureLog()
    log.pressure_bar = 220  # 300 * 0.75 = 225, below threshold → yellow
    troop.pressure_logs = [log]
    assert get_warning_level(troop) == "yellow"


def test_warning_level_red():
    troop = BreathingTroop()
    troop.start_press_avg = 300
    troop.withdraw_press_calc = 160
    log = PressureLog()
    log.pressure_bar = 155  # below 160 → red
    troop.pressure_logs = [log]
    assert get_warning_level(troop) == "red"


# ── Neue Tests (Leitfaden-Umbau) ──────────────────────────────────────────────

def test_one_third_seconds_property():
    troop = BreathingTroop()
    troop.planned_duration_min = 33
    assert troop.one_third_seconds == 660  # 33 * 20
    troop.planned_duration_min = None
    assert troop.one_third_seconds is None


def test_max_seconds_property():
    troop = BreathingTroop()
    troop.planned_duration_min = 37
    assert troop.max_seconds == 2220  # 37 * 60
    troop.planned_duration_min = None
    assert troop.max_seconds is None


def _make_troop_with_timer(entry_offset_min: int, planned_min: int) -> BreathingTroop:
    """Hilfsroutine: BreathingTroop ohne DB, bereits eingesetzt."""
    troop = BreathingTroop()
    troop.status = "im_einsatz"
    troop.planned_duration_min = planned_min
    troop.entry_at = datetime.now(UTC) - timedelta(minutes=entry_offset_min)
    troop.last_meldung_at = None
    troop.warn_one_third_acked_at = None
    troop.warn_max_time_acked_at = None
    troop.warn_withdraw_acked_at = None
    troop.pressure_logs = []
    return troop


def test_get_time_warning_ok_no_plan():
    troop = _make_troop_with_timer(20, planned_min=0)
    troop.planned_duration_min = None
    assert get_time_warning(troop) == "ok"


def test_get_time_warning_ok_within_one_third():
    """Noch innerhalb des ersten Drittels → ok."""
    troop = _make_troop_with_timer(entry_offset_min=5, planned_min=33)
    assert get_time_warning(troop) == "ok"


def test_get_time_warning_one_third_due():
    """1/3 verstrichen, keine Meldung → one_third_due."""
    troop = _make_troop_with_timer(entry_offset_min=12, planned_min=33)
    assert get_time_warning(troop) == "one_third_due"


def test_get_time_warning_resets_on_meldung():
    """Wenn seit Einsatzbeginn eine Meldung eingegangen ist → ok."""
    troop = _make_troop_with_timer(entry_offset_min=12, planned_min=33)
    troop.last_meldung_at = datetime.now(UTC) - timedelta(minutes=5)  # nach Einsatzbeginn
    assert get_time_warning(troop) == "ok"


def test_get_time_warning_one_third_due_after_ack_then_new_meldung():
    """Ack setzt warn aus, ohne neue Meldung bleibt Ack gültig."""
    troop = _make_troop_with_timer(entry_offset_min=12, planned_min=33)
    troop.warn_one_third_acked_at = datetime.now(UTC)
    assert get_time_warning(troop) == "ok"


def test_get_time_warning_max_exceeded():
    """Über Max-Zeit → max_exceeded."""
    troop = _make_troop_with_timer(entry_offset_min=34, planned_min=33)
    assert get_time_warning(troop) == "max_exceeded"


def test_get_time_warning_max_exceeded_acked():
    """Nach Quittierung der Max-Zeit → ok."""
    troop = _make_troop_with_timer(entry_offset_min=34, planned_min=33)
    troop.warn_max_time_acked_at = datetime.now(UTC)
    assert get_time_warning(troop) == "ok"


def test_ack_warning_sets_timestamp(tmp_path):
    """ack_warning setzt den richtigen Timestamp – ohne echte DB über stub."""
    class FakeDB:
        def flush(self): pass

    fakedb = FakeDB()

    # Monkeypatch write_incident_change
    import app.services.breathing_service as svc
    original = svc.write_incident_change
    calls = []
    svc.write_incident_change = lambda *a, **kw: calls.append((a, kw))

    try:
        troop = BreathingTroop()
        troop.incident_id = 1
        troop.warn_one_third_acked_at = None
        troop.warn_max_time_acked_at = None
        troop.warn_withdraw_acked_at = None

        before = datetime.now(UTC)
        ack_warning(fakedb, troop, "one_third")
        after = datetime.now(UTC)

        assert troop.warn_one_third_acked_at is not None
        assert before <= troop.warn_one_third_acked_at <= after
        assert troop.warn_max_time_acked_at is None
        assert troop.warn_withdraw_acked_at is None
    finally:
        svc.write_incident_change = original


def test_start_troop_uses_primary_org_factor(monkeypatch):
    """start_troop holt Factor/Reserve über primary_org_id, nicht slug=='wolfurt'."""
    import app.services.breathing_service as svc

    class FakeDept:
        withdraw_press_factor = 0.4
        withdraw_press_reserve = 20

    class FakeIncident:
        primary_org_id = 99

    class FakeDB:
        def flush(self): pass
        def get(self, model, pk):
            if model.__name__ == "Incident":
                return FakeIncident()
            if model.__name__ == "FireDept":
                return FakeDept()
            return None

    # Monkeypatch imports used inside start_troop
    import app.models.incident as inc_mod
    import app.models.master as master_mod

    original_write = svc.write_incident_change
    svc.write_incident_change = lambda *a, **kw: None

    try:
        troop = BreathingTroop()
        troop.incident_id = 1
        troop.status = "bereit"
        troop.entry_at = None
        troop.start_press_avg = None
        troop.withdraw_press_calc = None

        m1 = type("M", (), {"start_press": 300, "withdraw_press": None})()
        m2 = type("M", (), {"start_press": 290, "withdraw_press": None})()
        troop.members = [m1, m2]

        # Inject fake model classes into the service's import namespace
        orig_incident = getattr(inc_mod, "Incident", None)
        orig_firedept = getattr(master_mod, "FireDept", None)
        inc_mod.Incident = type("Incident", (), {"__name__": "Incident"})
        master_mod.FireDept = type("FireDept", (), {"__name__": "FireDept"})

        db = FakeDB()
        db.get = lambda model, pk: FakeIncident() if "Incident" in str(model) else FakeDept()

        svc.start_troop(db, troop)

        # avg = (300 + 290) / 2 = 295; withdraw = 295 * 0.4 + 20 = 138.0
        assert troop.withdraw_press_calc == 138.0
    finally:
        svc.write_incident_change = original_write
