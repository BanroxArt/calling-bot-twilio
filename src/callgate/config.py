"""Settings from the environment. There are no defaults for credentials."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time

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
    consent_max_age_days: int = 548
    host: str = "127.0.0.1"
    port: int = 8080


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
    start = _hhmm(env.get("CALL_WINDOW_START", "08:00"), "CALL_WINDOW_START")
    end = _hhmm(env.get("CALL_WINDOW_END", "21:00"), "CALL_WINDOW_END")
    if not start < end:
        raise ConfigError("call window must not wrap midnight")
    return Settings(
        account_sid=sid,
        auth_token=env.get("TWILIO_AUTH_TOKEN", ""),
        from_number=from_number,
        public_base_url=base,
        caller_name=env.get("CALLER_NAME", "Example Co"),
        support_number=support,
        database_path=env.get("DATABASE_PATH", ":memory:"),
        api_token=env.get("CALLGATE_API_TOKEN", ""),
        audit_key=env.get("AUDIT_HASH_KEY", "").encode(),
        outbound_enabled=env.get("OUTBOUND_ENABLED", "false").lower() == "true",
        calls_per_minute=float(env.get("CALLS_PER_MINUTE", "10")),
        max_attempts_per_day=int(env.get("MAX_ATTEMPTS_PER_DAY", "2")),
        min_gap_minutes=int(env.get("MIN_GAP_MINUTES", "120")),
        window_start=start,
        window_end=end,
        consent_max_age_days=int(env.get("CONSENT_MAX_AGE_DAYS", "548")),
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8080")),
    )
