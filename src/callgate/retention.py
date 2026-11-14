"""Retention: delete what is no longer needed, keep what is evidence.

Personal data that is kept "just in case" is a liability: it is what leaks,
and it is what a regulator asks why you still hold. Deleting too eagerly is
the opposite mistake, because consent records and gate decisions are what
you show when someone says they never agreed to a call. The defaults lean to
keeping evidence and dropping operational data:

=====================  ============================  ==================
Table                  Deleted when                  Default
=====================  ============================  ==================
audit                  older than RETAIN_AUDIT_DAYS  1825 days
attempt                older than RETAIN_ATTEMPT_DAYS 365 days (min 2)
consent (revoked)      revoked longer than           1825 days
                       RETAIN_REVOKED_CONSENT_DAYS
webhook_seen           older than RETAIN_WEBHOOK_DAYS 90 days
idempotency            past its 24 hour TTL          fixed
consent (active)       never
dnc                    never
=====================  ============================  ==================

Five years is longer than the four-year federal limitations period that
generally applies to TCPA claims. State law or your contracts may need more;
that is a question for counsel, and every number here is a setting.

Attempts younger than two days are never deleted whatever the setting,
because the daily cap and minimum gap are computed from them.

Deletes run in batches, each in its own transaction, so a large first run
does not hold the write lock long enough to stall the gate. ``secure_delete``
is switched on for the run (some SQLite builds default to it, many do not),
so SQLite overwrites the freed pages instead of leaving deleted numbers
readable in the file. Backups, and a WAL file if you switch WAL on, are
out of its reach. Each applied run writes one
``retention`` audit row with the counts, never the values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from callgate.store import IDEMPOTENCY_TTL_S, Store

DAY = 86_400.0
MIN_ATTEMPT_DAYS = 2
BATCH = 5_000


class RetentionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    audit_days: int = 1825
    attempt_days: int = 365
    revoked_consent_days: int = 1825
    webhook_days: int = 90

    def __post_init__(self) -> None:
        for name in ("audit_days", "attempt_days", "revoked_consent_days", "webhook_days"):
            if getattr(self, name) < 1:
                raise RetentionError(f"{name} must be at least 1")
        if self.attempt_days < MIN_ATTEMPT_DAYS:
            raise RetentionError(
                f"attempt_days must be at least {MIN_ATTEMPT_DAYS}; the daily cap reads them"
            )

    def rules(self, now: float) -> dict[str, tuple[str, str, float]]:
        """table label -> (table, WHERE clause with one ``?``, cutoff)."""
        return {
            "audit": ("audit", "ts < ?", now - self.audit_days * DAY),
            "attempt": ("attempt", "ts < ?", now - self.attempt_days * DAY),
            "consent_revoked": (
                "consent",
                "revoked_at IS NOT NULL AND revoked_at < ?",
                now - self.revoked_consent_days * DAY,
            ),
            "webhook_seen": ("webhook_seen", "ts < ?", now - self.webhook_days * DAY),
            "idempotency": ("idempotency", "created_at < ?", now - IDEMPOTENCY_TTL_S),
        }


@dataclass(slots=True)
class RetentionReport:
    applied: bool
    now: float
    counts: dict[str, int] = field(default_factory=dict)
    cutoffs: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "now": self.now,
            "deleted" if self.applied else "would_delete": dict(self.counts),
            "cutoffs": {k: round(v, 3) for k, v in self.cutoffs.items()},
        }


def plan(store: Store, policy: RetentionPolicy, now: float | None = None) -> RetentionReport:
    """Count what ``apply`` would delete. Reads only."""
    now = time.time() if now is None else now
    report = RetentionReport(False, now)
    with store.tx():
        for label, (table, where, cutoff) in policy.rules(now).items():
            (n,) = store.db.execute(
                f"SELECT count(*) FROM {table} WHERE {where}", (cutoff,)
            ).fetchone()
            report.counts[label] = int(n)
            report.cutoffs[label] = cutoff
    return report


def apply(
    store: Store, policy: RetentionPolicy, now: float | None = None, batch: int = BATCH
) -> RetentionReport:
    if batch < 1:
        raise RetentionError("batch must be at least 1")
    now = time.time() if now is None else now
    report = RetentionReport(True, now)
    # Connection-wide, so it is restored afterwards: the gate shares this
    # connection and has no reason to pay for overwriting freed pages.
    with store.tx():
        (previous,) = store.db.execute("PRAGMA secure_delete").fetchone()
        store.db.execute("PRAGMA secure_delete = ON")
    try:
        for label, (table, where, cutoff) in policy.rules(now).items():
            total = 0
            while True:
                with store.tx():
                    cur = store.db.execute(
                        f"DELETE FROM {table} WHERE rowid IN"
                        f" (SELECT rowid FROM {table} WHERE {where} LIMIT ?)",
                        (cutoff, batch),
                    )
                    n = cur.rowcount
                total += n
                if n < batch:
                    break
            report.counts[label] = total
            report.cutoffs[label] = cutoff
    finally:
        with store.tx():
            store.db.execute(f"PRAGMA secure_delete = {int(previous)}")
    summary = ", ".join(f"{k}={v}" for k, v in report.counts.items())
    store.audit("retention", None, summary, now)
    return report
