"""The outbound gate.

``Gate.check`` is the only place that can produce an ``Approval``, and
``TwilioClient.create_call`` will not run without one. So the rule "no
consent record, no dial" is enforced by the call graph, not by a comment
asking people to remember.

Checks, in order, all of which must pass:

1. Outbound calling is switched on (``OUTBOUND_ENABLED``, the kill switch).
2. The number is valid E.164.
3. The number is not on the internal do-not-call list.
4. There is an active, unrevoked consent record for this exact number and
   purpose, younger than ``consent_max_age_days``.
5. It is inside the calling window in the timezone on that record.
6. Fewer than ``max_attempts_per_day`` attempts in the last 24 hours, and at
   least ``min_gap_minutes`` since the last one.
7. The global rate limit has a token.

Every decision, allowed or not, is written to the audit table with the
reasons. Denials are normal; the audit trail is how you show the gate works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from callgate.config import Settings
from callgate.hours import TimezoneError, in_window, next_window_start
from callgate.phone import PhoneError, digest, mask, normalize
from callgate.ratelimit import TokenBucket
from callgate.store import Consent, Store

_MINT = object()  # only this module holds it


@dataclass(frozen=True, slots=True)
class Approval:
    phone: str
    purpose: str
    consent_id: int
    attempt_id: int
    _key: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._key is not _MINT:
            raise PermissionError("Approval can only be created by Gate.check")

    @property
    def valid(self) -> bool:
        return self._key is _MINT


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...]
    approval: Approval | None = None
    retry_after: datetime | None = None


class Gate:
    def __init__(self, settings: Settings, store: Store, bucket: TokenBucket | None = None) -> None:
        self.s = settings
        self.store = store
        self.bucket = bucket or TokenBucket(
            settings.calls_per_minute / 60.0, max(1.0, settings.calls_per_minute)
        )

    def _ref(self, phone: str) -> str:
        return digest(phone, self.s.audit_key) if self.s.audit_key else mask(phone)

    def check(self, raw_phone: str, purpose: str, now: datetime | None = None) -> Decision:
        now = now or datetime.now(UTC)
        with self.store.tx():
            decision = self._check(raw_phone, purpose, now)
            ref = (
                self._ref(decision.approval.phone)
                if decision.approval
                else self._safe_ref(raw_phone)
            )
            event = "gate_allow" if decision.allowed else "gate_deny"
            self.store.audit(
                event, ref, f"{purpose}: {', '.join(decision.reasons)}", now.timestamp()
            )
            return decision

    def preview(self, raw_phone: str, purpose: str, now: datetime | None = None) -> Decision:
        """Same checks without taking a rate-limit token, recording an attempt or auditing."""
        with self.store.tx():
            return self._check(raw_phone, purpose, now or datetime.now(UTC), commit=False)

    def _safe_ref(self, raw: str) -> str | None:
        try:
            return self._ref(normalize(raw))
        except PhoneError:
            return None

    def _check(self, raw_phone: str, purpose: str, now: datetime, commit: bool = True) -> Decision:
        if not self.s.outbound_enabled:
            return Decision(False, ("outbound_disabled",))
        try:
            phone = normalize(raw_phone)
        except PhoneError:
            return Decision(False, ("invalid_number",))
        if self.store.on_dnc(phone):
            return Decision(False, ("do_not_call",))
        consent = self.store.active_consent(phone, purpose)
        if consent is None:
            return Decision(False, ("no_consent",))
        reasons = self._consent_problems(consent, now)
        if reasons:
            return Decision(False, reasons)
        try:
            if not in_window(now, consent.timezone, self.s.window_start, self.s.window_end):
                nxt = next_window_start(
                    now, consent.timezone, self.s.window_start, self.s.window_end
                )
                return Decision(False, ("outside_calling_hours",), retry_after=nxt)
        except TimezoneError:
            return Decision(False, ("bad_timezone_on_consent",))
        ts = now.timestamp()
        recent = self.store.attempts_since(phone, ts - 86_400)
        if len(recent) >= self.s.max_attempts_per_day:
            return Decision(False, ("daily_attempt_cap",))
        if recent and ts - recent[-1] < self.s.min_gap_minutes * 60:
            retry = datetime.fromtimestamp(recent[-1] + self.s.min_gap_minutes * 60, UTC)
            return Decision(False, ("too_soon_since_last_attempt",), retry_after=retry)
        if not commit:
            return Decision(True, ("ok",))
        if not self.bucket.try_take():
            return Decision(False, ("rate_limited",))
        attempt_id = self.store.add_attempt(phone, purpose, ts, "approved")
        approval = Approval(phone, purpose, consent.id, attempt_id, _key=_MINT)
        return Decision(True, ("ok",), approval)

    def _consent_problems(self, c: Consent, now: datetime) -> tuple[str, ...]:
        age_days = (now.timestamp() - c.granted_at) / 86_400
        if age_days > self.s.consent_max_age_days:
            return ("consent_expired",)
        if age_days < 0:
            return ("consent_in_future",)
        if not c.evidence.strip():
            return ("consent_without_evidence",)
        return ()
