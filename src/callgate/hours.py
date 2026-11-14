"""Calling window in the callee's local time.

The default window is 08:00 to 21:00 local, every day, which is the TCPA's
time-of-day limit for telephone solicitations. Some states are stricter
(shorter windows, shorter or no weekend hours, no calls on listed
holidays); set the policy from your counsel's advice, not from this default.

A ``CallingPolicy`` adds three things to the plain window:

- ``days``: weekdays calls are allowed on at all (0 is Monday).
- ``per_day``: a different window for some weekdays, e.g. Saturday 10:00
  to 17:00.
- ``holidays``: local calendar dates with no calls. They are dates in the
  callee's timezone, so a holiday in New York starts at midnight New York
  time, not at midnight wherever the server runs. No holiday list ships with
  this; which days count depends on the state and the call type.

The timezone comes from the consent record, captured when the person opted
in. We do not guess it from the area code: mobile numbers move with people,
and a wrong guess is a call at 6 a.m.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALL_DAYS = frozenset(range(7))
# Far enough to cross a year of holidays and still fail on a policy that
# never allows a call, instead of looping forever.
SEARCH_DAYS = 400


class TimezoneError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimezoneError(f"unknown timezone {name!r}") from exc


def in_window(now_utc: datetime, tz: str, start: time, end: time) -> bool:
    local = now_utc.astimezone(zone(tz))
    return start <= local.time().replace(tzinfo=None) < end


def _first_on(day: date, z: ZoneInfo, start: time, end: time) -> datetime | None:
    """First UTC instant on local ``day`` that is inside the window, if any.

    A wall-clock start can fall in a DST gap (02:30 on spring-forward day
    does not exist) or overlap (01:30 on fall-back day happens twice).
    Both readings of the start are computed; the earliest in-window minute
    between them is the answer. Outside DST changes they are equal.
    """
    readings = sorted(
        datetime.combine(day, start, tzinfo=z).replace(fold=f).astimezone(UTC) for f in (0, 1)
    )
    t, last = readings[0], readings[1]
    tz = z.key
    while t <= last:
        if in_window(t, tz, start, end):
            return t
        t += timedelta(minutes=1)
    return None


def next_window_start(now_utc: datetime, tz: str, start: time, end: time) -> datetime:
    """Earliest UTC time at or after ``now_utc`` that is inside the window."""
    return CallingPolicy(start, end).next_allowed(now_utc, tz)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CallingPolicy:
    start: time = time(8, 0)
    end: time = time(21, 0)
    days: frozenset[int] = ALL_DAYS
    per_day: tuple[tuple[int, time, time], ...] = ()
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if not self.start < self.end:
            raise PolicyError("window start must be before end (no wrap past midnight)")
        if not self.days or not self.days <= ALL_DAYS:
            raise PolicyError("days must be a non-empty subset of 0..6")
        seen = set()
        for d, s, e in self.per_day:
            if d not in ALL_DAYS or d in seen:
                raise PolicyError(f"per-day window for day {d!r} is invalid or repeated")
            if not s < e:
                raise PolicyError(f"{DAY_NAMES[d]} window start must be before end")
            seen.add(d)

    def window_on(self, day: date) -> tuple[time, time] | None:
        """The local window for a calendar day, or None if no calls that day."""
        if day in self.holidays or day.weekday() not in self.days:
            return None
        for d, s, e in self.per_day:
            if d == day.weekday():
                return s, e
        return self.start, self.end

    def blocked(self, now_utc: datetime, tz: str) -> str | None:
        """Why a call now would be outside the policy, or None if it is allowed."""
        local = now_utc.astimezone(zone(tz))
        day = local.date()
        if day in self.holidays:
            return "holiday"
        window = self.window_on(day)
        if window is None:
            return "calling_day_not_allowed"
        if not in_window(now_utc, tz, *window):
            return "outside_calling_hours"
        return None

    def next_allowed(self, now_utc: datetime, tz: str) -> datetime:
        """Earliest time at or after ``now_utc`` the policy allows a call."""
        z = zone(tz)
        if self.blocked(now_utc, tz) is None:
            return now_utc
        day = now_utc.astimezone(z).date()
        for _ in range(SEARCH_DAYS):
            window = self.window_on(day)
            if window is not None:
                found = _first_on(day, z, *window)
                if found is not None and found >= now_utc:
                    return found.astimezone(now_utc.tzinfo)
            day += timedelta(days=1)
        raise TimezoneError(f"no calling window found in {tz!r} within {SEARCH_DAYS} days")


def parse_days(text: str) -> frozenset[int]:
    """``mon-fri``, ``mon,wed,fri``, ``mon-fri,sun`` or ``all``."""
    text = text.strip().lower()
    if text in ("", "all"):
        return ALL_DAYS
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        lo, sep, hi = part.partition("-")
        try:
            a = DAY_NAMES.index(lo.strip())
            b = DAY_NAMES.index(hi.strip()) if sep else a
        except ValueError as exc:
            raise PolicyError(f"unknown day in {part!r}; use mon..sun") from exc
        if b < a:
            raise PolicyError(f"day range {part!r} runs backwards; write it as two ranges")
        out.update(range(a, b + 1))
    return frozenset(out)


def parse_holidays(lines: list[str]) -> frozenset[date]:
    """ISO dates, one per line or comma separated. ``#`` starts a comment."""
    out: set[date] = set()
    for n, line in enumerate(lines, 1):
        for item in line.split("#", 1)[0].split(","):
            item = item.strip()
            if not item:
                continue
            try:
                out.add(date.fromisoformat(item))
            except ValueError as exc:
                raise PolicyError(f"line {n}: {item!r} is not a YYYY-MM-DD date") from exc
    return frozenset(out)
