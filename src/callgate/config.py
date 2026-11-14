"""Settings from the environment. There are no defaults for credentials."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

from callgate.hours import DAY_NAMES, CallingPolicy, PolicyError, parse_days, parse_holidays
from callgate.phone import PhoneError, normalize


class ConfigError(ValueError):
    pass


_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _hhmm(value: str, key: str) -> time:
    m = _HHMM.match(value)
    if not m:
        raise ConfigError(f"{key} must be HH:MM")
    return time(int(m.group(1)), int(m.group(2)))


@dataclass(frozen=True, slots=True)
class Settings:
    account_sid: str = ""
    auth_token: str = field(default="", repr=False)
    from_number: str = ""
    public_base_url: str = "http://localhost:8080"
    caller_name: str = "Example Co"
    support_number: str | None = None
    database_path: str = ":memory:"
    api_token: str = field(default="", repr=False)
    audit_key: bytes = field(default=b"", repr=False)
    outbound_enabled: bool = False
    calls_per_minute: float = 10.0
    max_attempts_per_day: int = 2
    min_gap_minutes: int = 120
    window_start: time = time(8, 0)
    window_end: time = time(21, 0)
    call_days: frozenset[int] = frozenset(range(7))
    day_windows: tuple[tuple[int, time, time], ...] = ()
    holidays: frozenset[date] = frozenset()
    consent_max_age_days: int = 548
    host: str = "127.0.0.1"
    port: int = 8080
    require_idempotency_key: bool = False

    @property
    def calling_policy(self) -> CallingPolicy:
        return CallingPolicy(
            self.window_start, self.window_end, self.call_days, self.day_windows, self.holidays
        )


MIN_SECRET_LEN = 32


def _num(env: Mapping[str, str], key: str, default: str, kind: type[int] | type[float]) -> float:
    raw = env.get(key, default)
    try:
        value = kind(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


def load(env: Mapping[str, str] | None = None, *, require_twilio: bool = True) -> Settings:
    env = os.environ if env is None else env
    missing = [
        k
        for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
        if not env.get(k)
    ]
    if require_twilio and missing:
        raise ConfigError(f"missing {', '.join(missing)}")
    sid = env.get("TWILIO_ACCOUNT_SID", "")
    if sid and not re.match(r"^AC[0-9a-fA-F]{32}$", sid):
        raise ConfigError("TWILIO_ACCOUNT_SID does not look like an account SID")
    try:
        from_number = normalize(env["TWILIO_FROM_NUMBER"]) if env.get("TWILIO_FROM_NUMBER") else ""
        support = normalize(env["SUPPORT_NUMBER"]) if env.get("SUPPORT_NUMBER") else None
    except PhoneError as exc:
        raise ConfigError(str(exc)) from exc
    base = env.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    if require_twilio and not base.startswith("https://"):
        # Twilio signs the exact URL it called; plain http in front of a TLS
        # proxy is the classic cause of every webhook failing validation.
        raise ConfigError("PUBLIC_BASE_URL must be the https URL Twilio calls")
    outbound = env.get("OUTBOUND_ENABLED", "false").strip().lower()
    if outbound not in ("true", "false"):
        raise ConfigError("OUTBOUND_ENABLED must be true or false")
    outbound_enabled = outbound == "true"
    require_key = env.get("REQUIRE_IDEMPOTENCY_KEY", "false").strip().lower()
    if require_key not in ("true", "false"):
        raise ConfigError("REQUIRE_IDEMPOTENCY_KEY must be true or false")
    audit_key = env.get("AUDIT_HASH_KEY", "")
    api_token = env.get("CALLGATE_API_TOKEN", "")
    if outbound_enabled and require_twilio:
        # Fail closed at startup. With outbound on, a blank audit key would quietly
        # degrade the audit log to masked numbers that cannot be joined, and
        # a short API token is the one thing between the internet and /calls.
        if len(audit_key) < MIN_SECRET_LEN:
            raise ConfigError(f"AUDIT_HASH_KEY must be at least {MIN_SECRET_LEN} characters")
        if len(api_token) < MIN_SECRET_LEN:
            raise ConfigError(f"CALLGATE_API_TOKEN must be at least {MIN_SECRET_LEN} characters")
        if not base.startswith("https://"):
            raise ConfigError("PUBLIC_BASE_URL must be https when outbound calling is on")
    start = _hhmm(env.get("CALL_WINDOW_START", "08:00"), "CALL_WINDOW_START")
    end = _hhmm(env.get("CALL_WINDOW_END", "21:00"), "CALL_WINDOW_END")
    if not start < end:
        raise ConfigError("call window must not wrap midnight")
    try:
        days = parse_days(env.get("CALL_DAYS", "all"))
        day_windows = []
        for i, name in enumerate(DAY_NAMES):
            raw = env.get(f"CALL_WINDOW_{name.upper()}", "").strip()
            if raw:
                a, sep, b = raw.partition("-")
                if not sep:
                    raise ConfigError(f"CALL_WINDOW_{name.upper()} must be HH:MM-HH:MM")
                key = f"CALL_WINDOW_{name.upper()}"
                day_windows.append((i, _hhmm(a.strip(), key), _hhmm(b.strip(), key)))
        holiday_lines = [env.get("CALL_HOLIDAYS", "")]
        holiday_file = env.get("CALL_HOLIDAYS_FILE", "")
        if holiday_file:
            try:
                holiday_lines += Path(holiday_file).read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise ConfigError(f"CALL_HOLIDAYS_FILE: {exc.strerror}") from exc
        holidays = parse_holidays(holiday_lines)
        CallingPolicy(start, end, days, tuple(day_windows), holidays)
    except PolicyError as exc:
        raise ConfigError(str(exc)) from exc
    return Settings(
        account_sid=sid,
        auth_token=env.get("TWILIO_AUTH_TOKEN", ""),
        from_number=from_number,
        public_base_url=base,
        caller_name=env.get("CALLER_NAME", "Example Co"),
        support_number=support,
        database_path=env.get("DATABASE_PATH", ":memory:"),
        api_token=api_token,
        audit_key=audit_key.encode(),
        outbound_enabled=outbound_enabled,
        calls_per_minute=_num(env, "CALLS_PER_MINUTE", "10", float),
        max_attempts_per_day=int(_num(env, "MAX_ATTEMPTS_PER_DAY", "2", int)),
        min_gap_minutes=int(_num(env, "MIN_GAP_MINUTES", "120", int)),
        window_start=start,
        window_end=end,
        call_days=days,
        day_windows=tuple(day_windows),
        holidays=holidays,
        consent_max_age_days=int(_num(env, "CONSENT_MAX_AGE_DAYS", "548", int)),
        host=env.get("HOST", "127.0.0.1"),
        port=int(_num(env, "PORT", "8080", int)),
        require_idempotency_key=require_key == "true",
    )
