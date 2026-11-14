"""Day-of-week rules, per-day windows and holidays in the callee's timezone."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from conftest import CALLEE, NOON
from hypothesis import given, settings
from hypothesis import strategies as st

from callgate.config import ConfigError, Settings, load
from callgate.gate import Gate
from callgate.hours import (
    CallingPolicy,
    PolicyError,
    TimezoneError,
    parse_days,
    parse_holidays,
)
from callgate.store import Store

NY = "America/New_York"


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_default_policy_matches_the_plain_window():
    p = CallingPolicy()
    assert p.blocked(utc(2026, 3, 22, 15, 0), NY) is None  # Sunday 11:00 EDT
    assert p.blocked(utc(2026, 3, 22, 11, 0), NY) == "outside_calling_hours"


def test_weekend_off_rolls_to_monday():
    p = CallingPolicy(days=parse_days("mon-fri"))
    sat = utc(2026, 3, 21, 15, 0)  # Saturday 11:00 EDT
    assert p.blocked(sat, NY) == "calling_day_not_allowed"
    assert p.next_allowed(sat, NY) == utc(2026, 3, 23, 12, 0)  # Monday 08:00 EDT


def test_per_day_window_overrides_the_default():
    p = CallingPolicy(per_day=((5, time(10), time(17)),))  # Saturday 10:00-17:00
    assert p.blocked(utc(2026, 3, 21, 13, 30), NY) == "outside_calling_hours"  # 09:30
    assert p.next_allowed(utc(2026, 3, 21, 13, 30), NY) == utc(2026, 3, 21, 14, 0)
    assert p.blocked(utc(2026, 3, 21, 21, 30), NY) == "outside_calling_hours"  # 17:30
    # Weekdays keep 08:00-21:00.
    assert p.blocked(utc(2026, 3, 20, 21, 30), NY) is None


def test_holiday_is_a_local_date():
    p = CallingPolicy(holidays=frozenset({date(2026, 7, 3)}))
    # 03:30 UTC on July 3 is still 23:30 on July 2 in New York: not the holiday,
    # just outside hours.
    assert p.blocked(utc(2026, 7, 3, 3, 30), NY) == "outside_calling_hours"
    assert p.blocked(utc(2026, 7, 3, 15, 0), NY) == "holiday"
    assert p.next_allowed(utc(2026, 7, 3, 15, 0), NY) == utc(2026, 7, 4, 12, 0)


def test_holiday_block_then_weekend_then_next_window():
    p = CallingPolicy(
        days=parse_days("mon-fri"),
        holidays=parse_holidays(["2026-12-24, 2026-12-25  # office closed", "2026-12-28"]),
    )
    start = utc(2026, 12, 24, 15, 0)  # Thursday
    assert p.next_allowed(start, NY) == utc(2026, 12, 29, 13, 0)  # Tuesday 08:00 EST


def test_spring_forward_on_a_per_day_window():
    # 2026-03-08 is a Sunday and the US spring-forward day; 02:30 does not exist.
    p = CallingPolicy(per_day=((6, time(2, 30), time(4)),))
    assert p.next_allowed(utc(2026, 3, 8, 5, 0), NY) == utc(2026, 3, 8, 7, 0)  # 03:00 EDT


def test_policy_that_never_allows_a_call_fails_instead_of_looping():
    every_day = frozenset(date(2026, 1, 1) + timedelta(days=i) for i in range(500))
    p = CallingPolicy(holidays=every_day)
    with pytest.raises(TimezoneError, match="400 days"):
        p.next_allowed(utc(2026, 1, 1, 15, 0), NY)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("all", set(range(7))),
        ("mon-fri", {0, 1, 2, 3, 4}),
        ("mon,wed,fri", {0, 2, 4}),
        ("mon-fri, sun", {0, 1, 2, 3, 4, 6}),
        ("SAT", {5}),
    ],
)
def test_parse_days(text, expected):
    assert parse_days(text) == frozenset(expected)


@pytest.mark.parametrize("text", ["fri-mon", "monday", "mon-", ","])
def test_parse_days_rejects(text):
    with pytest.raises(PolicyError):
        parse_days(text)


def test_parse_holidays_rejects_bad_dates():
    with pytest.raises(PolicyError, match="line 2"):
        parse_holidays(["2026-01-01", "2026-13-01"])


def test_policy_validation():
    with pytest.raises(PolicyError):
        CallingPolicy(start=time(21), end=time(8))
    with pytest.raises(PolicyError):
        CallingPolicy(days=frozenset())
    with pytest.raises(PolicyError):
        CallingPolicy(per_day=((5, time(10), time(9)),))
    with pytest.raises(PolicyError):
        CallingPolicy(per_day=((5, time(9), time(10)), (5, time(11), time(12))))


def test_config_reads_days_windows_and_holiday_file(tmp_path):
    f = tmp_path / "holidays.txt"
    f.write_text("# closed\n2026-11-26\n2026-12-25 # Christmas\n", encoding="utf-8")
    s = load(
        {
            "CALL_DAYS": "mon-sat",
            "CALL_WINDOW_SAT": "10:00-17:00",
            "CALL_HOLIDAYS": "2026-07-03",
            "CALL_HOLIDAYS_FILE": str(f),
        },
        require_twilio=False,
    )
    p = s.calling_policy
    assert p.days == frozenset(range(6))
    assert p.per_day == ((5, time(10), time(17)),)
    assert p.holidays == {date(2026, 7, 3), date(2026, 11, 26), date(2026, 12, 25)}


@pytest.mark.parametrize(
    "env",
    [
        {"CALL_DAYS": "funday"},
        {"CALL_WINDOW_SAT": "10:00"},
        {"CALL_WINDOW_SAT": "17:00-10:00"},
        {"CALL_HOLIDAYS": "tomorrow"},
        {"CALL_HOLIDAYS_FILE": "/nonexistent/holidays.txt"},
    ],
)
def test_config_rejects_bad_policy(env):
    with pytest.raises(ConfigError):
        load(env, require_twilio=False)


def test_gate_reports_holiday_and_retry_after(settings):
    store = Store()
    store.add_consent(CALLEE, "reminder", NY, "web", "form-1", "v1", at=NOON.timestamp() - 60)
    s = Settings(**{**_fields(settings), "holidays": frozenset({NOON.date()})})
    d = Gate(s, store).check(CALLEE, "reminder", NOON)
    assert not d.allowed and d.reasons == ("holiday",)
    assert d.retry_after == utc(2026, 3, 19, 12, 0)
    assert store.audit_rows()[-1][2] == "reminder: holiday"


def test_gate_reports_day_not_allowed(settings):
    store = Store()
    store.add_consent(CALLEE, "reminder", NY, "web", "form-1", "v1", at=NOON.timestamp() - 60)
    s = Settings(**{**_fields(settings), "call_days": frozenset({0})})  # Mondays only
    d = Gate(s, store).check(CALLEE, "reminder", NOON)  # a Wednesday
    assert d.reasons == ("calling_day_not_allowed",)
    assert d.retry_after == utc(2026, 3, 23, 12, 0)


def _fields(s):
    return {f: getattr(s, f) for f in s.__dataclass_fields__}


ZONES = ["America/New_York", "America/Los_Angeles", "Pacific/Honolulu", "Europe/London"]


@settings(max_examples=150, deadline=None)
@given(
    st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31)),
    st.sampled_from(ZONES),
    st.sets(st.integers(min_value=0, max_value=6), min_size=1),
    st.integers(min_value=0, max_value=22),
    st.integers(min_value=1, max_value=12),
    st.sets(st.integers(min_value=0, max_value=60), max_size=6),
)
def test_next_allowed_is_allowed_and_the_minute_before_is_not(
    naive, tz, days, start_h, length_h, holiday_offsets
):
    now = naive.replace(tzinfo=UTC, second=0, microsecond=0)
    end_h = min(start_h + length_h, 23)
    if end_h <= start_h:
        return
    holidays = frozenset(now.date() + timedelta(days=d) for d in holiday_offsets)
    p = CallingPolicy(time(start_h), time(end_h), frozenset(days), (), holidays)
    got = p.next_allowed(now, tz)
    assert got >= now
    assert p.blocked(got, tz) is None
    if got > now:
        assert p.blocked(got - timedelta(minutes=1), tz) is not None
