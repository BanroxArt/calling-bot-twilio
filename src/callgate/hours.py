"""Calling window in the callee's local time.

The default window is 08:00 to 21:00 local, which is the TCPA's
time-of-day limit for telephone solicitations. Some states are stricter
(shorter windows, no Sunday or holiday calls); set the window from your
counsel's advice, not from this default.

The timezone comes from the consent record, captured when the person opted
in. We do not guess it from the area code: mobile numbers move with people,
and a wrong guess is a call at 6 a.m.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    z = zone(tz)
    if in_window(now_utc, tz, start, end):
        return now_utc
    local = now_utc.astimezone(z)
    day = local.date()
    for _ in range(8):
        found = _first_on(day, z, start, end)
        if found is not None and found >= now_utc:
            return found.astimezone(now_utc.tzinfo)
        day += timedelta(days=1)
    raise TimezoneError(f"no calling window found in {tz!r} within a week")
