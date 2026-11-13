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
