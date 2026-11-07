"""S2: reine Prioritaeten, lokale Kalender und UTC-Grenzen."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.infoscreen_agent_resolver import Display, Schedule, resolve, timeline
from app.services.infoscreen_aktivitaet import Aktivitaet

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
DISPLAY = Display(1, 1, "/infoscreen/alarm/test")


def override(kind, **kwargs):
    return dict(id=1, org_id=1, display_id=1, kind=kind, von=NOW - timedelta(hours=1),
                bis=NOW + timedelta(hours=1), url="/override", **kwargs)


def slot(start="00:00", end="23:59", **kwargs):
    return dict(weekdays=127, start_time=start, end_time=end, power="off", **kwargs)


@pytest.mark.parametrize("locked,allowed,gsl,incident,recent,gsl_override,manual,plan,mode", [
    (True, True, True, True, True, True, True, True, "locked"),
    (False, True, True, True, True, True, True, True, "override"),
    (False, False, True, True, True, True, True, True, "gsl"),
    (False, True, False, True, True, False, True, True, "incident"),
    (False, True, False, False, True, False, True, True, "nachlauf"),
    (False, True, False, False, False, False, True, True, "manual"),
    (False, True, False, False, False, False, False, True, "schedule"),
    (False, True, False, False, False, False, False, False, "default"),
    (False, False, False, False, False, True, False, False, "default"),
])
def test_prioritaeten(locked, allowed, gsl, incident, recent, gsl_override, manual, plan, mode):
    overrides = ([override("gsl_page")] if gsl_override else []) + ([override("manual")] if manual else [])
    result = resolve(replace(DISPLAY, locked=locked, gsl_override_erlaubt=allowed), NOW,
                     Aktivitaet(gsl, incident, NOW - timedelta(minutes=1) if recent else None),
                     overrides, Schedule(slots=[slot()]) if plan else None)
    assert result["mode"] == mode
    assert result["display_power"] == ("off" if mode == "schedule" else "on")
    assert result["update_allowed"] == (not (locked or gsl or incident or recent))


@pytest.mark.parametrize("offset,expected", [(-1, "nachlauf"), (0, "default"), (1, "default")])
def test_nachlauf_exakte_grenze(offset, expected):
    end = NOW - timedelta(minutes=10)
    result = resolve(DISPLAY, NOW + timedelta(microseconds=offset), Aktivitaet(letztes_ende=end), [], None)
    assert result["mode"] == expected
    if offset == -1:
        assert result["valid_until"] == NOW.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("day,begin,end", [
    ("2026-03-29", "2026-03-29T00:00:00Z", "2026-03-29T02:00:00Z"),
    ("2026-10-25", "2026-10-24T23:00:00Z", "2026-10-25T03:00:00Z"),
])
def test_dst(day, begin, end):
    now = datetime.fromisoformat(day).replace(tzinfo=UTC) - timedelta(hours=2)
    result = timeline(Schedule(slots=[slot("01:00", "04:00", valid_from=date.fromisoformat(day),
                                          valid_to=date.fromisoformat(day))]), now)
    off = [r for r in result if r["power"] == "off"]
    assert off == [{"from": begin, "until": end, "power": "off", "rotation_ref": []}]
    assert datetime.fromisoformat(result[-1]["until"]) - datetime.fromisoformat(result[0]["from"]) == timedelta(hours=72)
    assert all(a["until"] == b["from"] for a, b in zip(result, result[1:]))


@pytest.mark.parametrize("day,end", [("2026-03-29", "2026-03-29T02:00:00Z"),
                                      ("2026-10-25", "2026-10-25T01:30:00Z")])
def test_dst_luecke_und_doppelte_zeit(day, end):
    schedule = Schedule(slots=[slot("01:00", "02:30", valid_from=date.fromisoformat(day),
                                    valid_to=date.fromisoformat(day))])
    result = timeline(schedule, datetime.fromisoformat(day).replace(tzinfo=UTC) - timedelta(hours=2))
    off = [r for r in result if r["power"] == "off"][0]
    expected = "2026-03-29T01:30:00Z" if day.endswith("03-29") else end
    assert off["until"] == expected


def test_mitternacht_gueltigkeit_und_ausnahmetag():
    schedule = Schedule(
        slots=[dict(weekdays=1, start_time="22:00", end_time="06:00", power="off",
                    valid_from=date(2026, 11, 2), valid_to=date(2026, 11, 2))])
    result = timeline(schedule, NOW)
    assert [r for r in result if r["power"] == "off"] == [
        {"from": "2026-11-02T21:00:00Z", "until": "2026-11-03T05:00:00Z",
         "power": "off", "rotation_ref": []}]
    schedule.exceptions[date(2026, 11, 3)] = [slot("00:00", "02:00", rotation=[{"builtin": "weather"}]) | {"power": "on"}]
    result = timeline(schedule, NOW)
    assert next(r for r in result if r["from"] == "2026-11-02T23:00:00Z")["power"] == "on"
    assert next(r for r in result if r["from"] == "2026-11-03T01:00:00Z")["power"] == "off"


def test_feiertag_ganzer_tag_aus_und_prioritaet_spaeterer_start():
    schedule = Schedule(slots=[slot(priority=0), slot("10:00", "15:00", priority=1) | {"power": "on"},
                               slot("12:00", "14:00", priority=1, rotation=[{"url_id": 7}])])
    result = resolve(DISPLAY, NOW, Aktivitaet(), [], schedule)
    assert result["display_power"] == "off"  # 13:00 lokal, spaeterer Start gewinnt
    schedule.exceptions[NOW.date()] = []
    assert resolve(DISPLAY, NOW, Aktivitaet(), [], schedule)["display_power"] == "off"
    assert timeline(schedule, NOW)[0]["until"] == "2026-11-02T23:00:00Z"


def test_override_ablauf_org_display_aufhebung_und_zukunft():
    base = override("manual")
    for changes in ({"org_id": 2}, {"display_id": 2}, {"bis": NOW}, {"aufgehoben_am": NOW},
                    {"von": NOW + timedelta(seconds=1)}):
        assert resolve(DISPLAY, NOW, Aktivitaet(), [base | changes], None)["mode"] == "default"
    assert resolve(DISPLAY, NOW, Aktivitaet(), [base | {"von": NOW}], None)["mode"] == "manual"


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_ausnahmetag_an_dst_tag_hat_lokale_tageslaenge(day, hours):
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = timeline(Schedule(exceptions={day: []}), start)
    off = next(row for row in rows if row["power"] == "off")
    assert datetime.fromisoformat(off["until"]) - datetime.fromisoformat(off["from"]) == timedelta(hours=hours)


def test_schedule_zeitzone_ist_autoritaet_und_naive_eingabe_ist_utc():
    now = datetime(2026, 11, 2, 12, 30)
    slots = [slot("12:00", "13:00")]
    assert resolve(DISPLAY, now, Aktivitaet(), [], Schedule(timezone="UTC", slots=slots))["display_power"] == "off"
    assert resolve(DISPLAY, now, Aktivitaet(), [], Schedule(timezone="Europe/Vienna", slots=slots))["mode"] == "default"
