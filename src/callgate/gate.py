"""The outbound gate.

``Gate.check`` is the only place in this package that issues an
``Approval``, and ``TwilioClient.create_call`` will not run without one. An
approval is signed with a key generated when the process starts, is good for
one call, and expires after ``APPROVAL_TTL_S``. So "no consent record, no
dial" is enforced by the call graph, and an approval cannot be built by hand,
reused for a second call, or saved and used hours later.

What this is not: a security boundary inside the process. Code running in
the same interpreter can read the signing key, and anything holding the
Twilio auth token can call the REST API without this package at all. The
approval stops mistakes and shortcuts in your own code; keeping the auth
token away from code that should not dial is still your job.

Checks, in order, all of which must pass:

1. Outbound calling is switched on (``OUTBOUND_ENABLED``, the kill switch).
2. The number is valid E.164.
3. The number is not on the internal do-not-call list.
4. There is an active, unrevoked consent record for this exact number and
   purpose, younger than ``consent_max_age_days``.
5. It is inside the calling window in the timezone on that record.
6. Fewer than ``max_attempts_per_day`` attempts in the last 24 hours, and at
   least ``min_gap_minutes`` since the last one. Attempts Twilio refused
   outright (``not_placed``) or reported as ``failed`` never rang the phone
   and do not count; ambiguous ones (``unknown``) do.
7. The global rate limit has a token. If recording the attempt fails, the
   token is returned.

Every decision, allowed or not, is written to the audit table with the
reasons. Denials are normal; the audit trail is a record of what the gate
decided, kept in the same database as everything else. It is not tamper
evident: anyone who can write the database can edit it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from callgate.config import Settings
from callgate.hours import TimezoneError, in_window, next_window_start
from callgate.phone import PhoneError, digest, mask, normalize
from callgate.ratelimit import TokenBucket
from callgate.store import Consent, Store

APPROVAL_TTL_S = 60.0
MIN_AUDIT_KEY_LEN = 32


class _Ledger:
    """Signs approvals and remembers which ones are still unspent."""

    def __init__(self, ttl_s: float = APPROVAL_TTL_S, max_open: int = 10_000) -> None:
        self._key = secrets.token_bytes(32)
        self.ttl_s = ttl_s
        self._open: OrderedDict[str, float] = OrderedDict()
        self._max_open = max_open
        self._lock = threading.Lock()

    def tag(self, phone: str, purpose: str, consent_id: int, attempt_id: int) -> str:
        msg = f"{phone}\x00{purpose}\x00{consent_id}\x00{attempt_id}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def issue(self, tag: str) -> None:
        with self._lock:
            self._open[tag] = time.monotonic()
            while len(self._open) > self._max_open:
                self._open.popitem(last=False)

    def check(self, a: Approval, *, spend: bool) -> str | None:
        """Return why ``a`` cannot be used, or None. ``spend`` marks it used."""
        if not hmac.compare_digest(a.tag, self.tag(a.phone, a.purpose, a.consent_id, a.attempt_id)):
            return "not issued by Gate.check"
        with self._lock:
            issued = self._open.get(a.tag)
            if issued is None:
                return "already used"
            if time.monotonic() - issued > self.ttl_s:
                del self._open[a.tag]
                return "expired"
            if spend:
                del self._open[a.tag]
        return None


_LEDGER = _Ledger()


@dataclass(frozen=True, slots=True)
class Approval:
    """Permission for one call to one number, valid for ``APPROVAL_TTL_S``."""

    phone: str
    purpose: str
    consent_id: int
    attempt_id: int
    tag: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if _LEDGER.check(self, spend=False) == "not issued by Gate.check":
            raise PermissionError("Approval can only be created by Gate.check")

    @property
    def valid(self) -> bool:
        """Signed, unspent and unexpired. Does not spend it."""
        return _LEDGER.check(self, spend=False) is None


def redeem(approval: Approval) -> None:
    """Spend an approval. Raises PermissionError if it is forged, used or expired."""
    if not isinstance(approval, Approval):
        raise PermissionError("create_call needs an Approval from Gate.check")
    problem = _LEDGER.check(approval, spend=True)
    if problem is not None:
        raise PermissionError(f"approval {problem}")


def _issue(phone: str, purpose: str, consent_id: int, attempt_id: int) -> Approval:
    tag = _LEDGER.tag(phone, purpose, consent_id, attempt_id)
    _LEDGER.issue(tag)
    return Approval(phone, purpose, consent_id, attempt_id, tag)


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

    def phone_ref(self, phone: str) -> str:
        """What the audit log stores instead of the number: a keyed digest."""
        return digest(phone, self.s.audit_key) if self.s.audit_key else mask(phone)

    _ref = phone_ref

    def check(self, raw_phone: str, purpose: str, now: datetime | None = None) -> Decision:
        now = now or datetime.now(UTC)
        took: list[bool] = []
        try:
            with self.store.tx():
                decision = self._check(raw_phone, purpose, now, took=took)
                ref = (
                    self._ref(decision.approval.phone)
                    if decision.approval
                    else self._safe_ref(raw_phone)
                )
                event = "gate_allow" if decision.allowed else "gate_deny"
                self.store.audit(
                    event, ref, f"{purpose}: {', '.join(decision.reasons)}", now.timestamp()
                )
        except BaseException:
            # The transaction rolled back, so no attempt exists for this
            # token. Give it back rather than slowing every later call.
            if took:
                self.bucket.give_back()
            raise
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

    def _check(
        self,
        raw_phone: str,
        purpose: str,
        now: datetime,
        commit: bool = True,
        took: list[bool] | None = None,
    ) -> Decision:
        if not self.s.outbound_enabled:
            return Decision(False, ("outbound_disabled",))
        if commit and len(self.s.audit_key) < MIN_AUDIT_KEY_LEN:
            # Fail closed. Without a key the audit log can only hold masked
            # numbers, which collide and cannot be joined to a person's history.
            return Decision(False, ("no_audit_key",))
        try:
            phone = normalize(raw_phone)
        except PhoneError:
            return Decision(False, ("invalid_number",))
        if self.store.on_dnc(phone):
            return Decision(False, ("do_not_call",))
        # Pick the newest record that is actually usable. Looking only at the
        # newest row let an incomplete newer row hide a valid older one.
        records = self.store.active_consents(phone, purpose)
        if not records:
            return Decision(False, ("no_consent",))
        consent = next((c for c in records if not self._consent_problems(c, now)), None)
        if consent is None:
            return Decision(False, self._consent_problems(records[0], now))
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
        if took is not None:
            took.append(True)
        attempt_id = self.store.add_attempt(phone, purpose, ts, "pending")
        approval = _issue(phone, purpose, consent.id, attempt_id)
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
