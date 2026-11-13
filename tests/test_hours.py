from datetime import UTC, datetime, time

import pytest

from callgate.hours import TimezoneError, in_window, next_window_start

START, END = time(8), time(21)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_window_uses_callee_timezone():
    t = utc(2026, 3, 18, 15, 0)  # 11:00 New York (EDT), 08:00 Los Angeles (PDT)
    assert in_window(t, "America/New_York", START, END)
    assert in_window(t, "America/Los_Angeles", START, END)
    assert not in_window(utc(2026, 3, 18, 14, 59), "America/Los_Angeles", START, END)


def test_end_is_exclusive():
    assert not in_window(utc(2026, 1, 15, 2, 0), "America/New_York", START, END)  # 21:00 EST
    assert in_window(utc(2026, 1, 15, 1, 59), "America/New_York", START, END)


def test_next_window_same_day_and_next_day():
    early = utc(2026, 1, 15, 11, 0)  # 06:00 EST
    assert next_window_start(early, "America/New_York", START, END) == utc(2026, 1, 15, 13, 0)
    late = utc(2026, 1, 16, 3, 0)  # 22:00 EST on the 15th
    assert next_window_start(late, "America/New_York", START, END) == utc(2026, 1, 16, 13, 0)


def test_next_window_across_dst_change():
    # 2026-03-08: US clocks go forward. 08:00 local is 12:00 UTC after the change.
    sat_night = utc(2026, 3, 8, 3, 0)  # 22:00 EST on the 7th
    assert next_window_start(sat_night, "America/New_York", START, END) == utc(2026, 3, 8, 12, 0)


def test_inside_window_returns_now():
    t = utc(2026, 3, 18, 15, 0)
    assert next_window_start(t, "America/New_York", START, END) == t


def test_unknown_timezone():
    with pytest.raises(TimezoneError):
        in_window(utc(2026, 1, 1), "Mars/Olympus", START, END)


def test_window_start_inside_dst_gap():
    # 2026-03-08: New York clocks jump from 02:00 to 03:00. A window that
    # starts at 02:30 really opens at 03:00 local (07:00 UTC) that day.
    # Regression: the old code returned 03:30 local, outside a window that
    # ends at 03:15.
    now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)  # 00:00 EST
    got = next_window_start(now, "America/New_York", time(2, 30), time(3, 15))
    assert got == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert in_window(got, "America/New_York", time(2, 30), time(3, 15))


def test_window_start_inside_fall_back_overlap_uses_first():
    # 2026-11-01: 01:30 happens twice in New York; the first is 05:30 UTC.
    now = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 00:00 EDT
    got = next_window_start(now, "America/New_York", time(1, 30), time(9, 0))
    assert got == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
