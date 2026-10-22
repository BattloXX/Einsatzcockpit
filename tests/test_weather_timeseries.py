"""Fenstergrenzen, UTC/DST und fehlende Werte der Wetterzeitreihen."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.weather_timeseries import (
    accumulated_to_interval,
    utc,
    value_at,
    window_max,
    window_sum,
)

REF = datetime(2026, 10, 22, 10, tzinfo=UTC)


def series(values, start=REF):
    return [(start + timedelta(hours=i), value) for i, value in enumerate(values)]


def test_sum_excludes_previous_hour_and_no_double_counting():
    rows = series([99, 1, 2, 4])
    assert window_sum(rows, REF, 3) == (7, True)
    a, _ = window_sum(rows, REF, 1)
    b, _ = window_sum(rows, REF + timedelta(hours=1), 2)
    assert a + b == 7


def test_partial_hours_and_offset():
    ref = datetime.fromisoformat("2026-10-22T12:30:00+02:00")
    rows = series([99, 2, 4, 8])
    assert window_sum(rows, ref, 2) == (9, True)
    assert window_sum(rows, ref, 1) == (3, True)


@pytest.mark.parametrize("values", [[1, None, 2], [1, 2]])
def test_unknown_or_short_series_never_returns_partial_sum(values):
    assert window_sum(series(values), REF, 2) == (None, False)


def test_gap_and_duplicate_are_incomplete():
    rows = series([1, 2, 3, 4])
    assert window_sum([rows[0], rows[1], rows[3]], REF, 3) == (None, False)
    assert window_sum(rows + [rows[1]], REF, 3) == (None, False)


def test_missing_outside_window_does_not_affect_sum():
    assert window_sum(series([None, 0, 0, None]), REF, 2) == (0, True)


def test_unsorted_rows():
    assert window_sum(list(reversed(series([99, 1, 2]))), REF, 2) == (3, True)


def test_maximum_time_and_tie_in_utc():
    offset = ZoneInfo("Europe/Vienna")
    rows = [(t.astimezone(offset), v) for t, v in series([99, 7, 12, 12])]
    assert window_max(rows, REF, 3) == (12, REF + timedelta(hours=2), True)


def test_maximum_with_unknown_is_marked_incomplete():
    assert window_max(series([99, None, 12]), REF, 2) == (12, REF + timedelta(hours=2), False)
    assert window_max(series([None, None]), REF, 1) == (None, None, False)


def test_maximum_partial_hour_is_not_scaled():
    assert window_max(series([99, 12, 7]), REF + timedelta(minutes=30), 1) == (
        12, REF + timedelta(hours=1), True,
    )


@pytest.mark.parametrize("start", ["2026-03-29T00:00:00+01:00", "2026-10-25T00:00:00+02:00"])
def test_dst_elapsed_hours_with_vienna_timestamps(start):
    ref = datetime.fromisoformat(start)
    vienna = ZoneInfo("Europe/Vienna")
    rows = [(t.astimezone(vienna), v) for t, v in series([99, 1, 2, 3, 4], utc(ref))]
    assert window_sum(rows, ref, 4) == (10, True)
    assert window_max(rows, ref, 4) == (4, utc(ref) + timedelta(hours=4), True)
    assert len({t.isoformat() for t, _ in rows}) == 5


def test_accumulated_to_intervals_not_accumulated_sum():
    rows = accumulated_to_interval(series([10, 11, 13, 16]))
    assert [v for _, v in rows] == [None, 1, 2, 3]
    assert window_sum(rows, REF, 3) == (6, True)


def test_accumulated_reset_and_missing_baseline():
    rows = accumulated_to_interval(series([5, 2, 4, None, 8, 9, -1, 2, 3]))
    assert [v for _, v in rows] == [None, None, 2, None, None, 1, None, None, 1]
    assert window_sum(rows, REF, 2) == (None, False)


def test_accumulated_gap_does_not_compress_two_hours():
    rows = series([0, 2, 4, 7])
    assert accumulated_to_interval([rows[0], rows[2], rows[3]]) == [
        (REF, None), (REF + timedelta(hours=2), None), (REF + timedelta(hours=3), 3),
    ]


def test_empty_and_zero_window():
    assert window_sum([], REF, 1) == (None, False)
    assert window_sum(series([99, 2]), REF, 0) == (0, True)
    assert window_max([], REF, 0) == (None, None, True)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        window_sum([], REF, -1)
    with pytest.raises(ValueError):
        utc(datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None))


def test_point_value_uses_time_and_never_clamps():
    rows = series([1, 2, 3])
    assert value_at(rows, REF + timedelta(minutes=30)) == 2
    assert value_at(rows, REF + timedelta(hours=3)) is None


def test_duplicate_with_none_is_incomplete():
    rows = series([0, 2, 3]) + [(REF + timedelta(hours=1), None)]
    assert window_sum(rows, REF, 2) == (None, False)
    assert window_max(rows, REF, 2) == (3, REF + timedelta(hours=2), False)
