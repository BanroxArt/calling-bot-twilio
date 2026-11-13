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

from datetime import datetime, time, timedelta
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


def next_window_start(now_utc: datetime, tz: str, start: time, end: time) -> datetime:
    """Earliest UTC time at or after ``now_utc`` that is inside the window."""
    z = zone(tz)
    local = now_utc.astimezone(z)
    if in_window(now_utc, tz, start, end):
        return now_utc
    day = (
        local.date()
        if local.time().replace(tzinfo=None) < start
        else local.date() + timedelta(days=1)
    )
    # Build the wall-clock start and let zoneinfo resolve DST for that date.
    candidate = datetime.combine(day, start, tzinfo=z)
    return candidate.astimezone(now_utc.tzinfo)
