"""SQLite store for consent, do-not-call, attempts and the audit trail.

Consent is append-only: granting adds a row, revoking stamps ``revoked_at``.
Nothing is ever updated back to active, so the history of what a person
agreed to and when is always reconstructable. A new grant for the same number
and purpose supersedes the older active rows (``revoked_via`` says which row
replaced them), so there is one current record per number and purpose.

Attempts move through a one-way status machine (``STATUS_RANK``). Twilio
webhooks carry no nonce or timestamp, so a replayed or late status callback
is indistinguishable from a real one; the rank check means it can only move
an attempt forward, never back from ``completed`` to ``ringing``.

Every connection access goes through one re-entrant lock.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
  id INTEGER PRIMARY KEY,
  phone TEXT NOT NULL,
  purpose TEXT NOT NULL,
  timezone TEXT NOT NULL,
  source TEXT NOT NULL,
  evidence TEXT NOT NULL,
  disclosure_version TEXT NOT NULL,
  granted_at REAL NOT NULL,
  revoked_at REAL,
  revoked_via TEXT
);
CREATE INDEX IF NOT EXISTS consent_lookup ON consent (phone, purpose);
CREATE TABLE IF NOT EXISTS dnc (
  phone TEXT PRIMARY KEY,
  added_at REAL NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY,
  phone TEXT NOT NULL,
  purpose TEXT NOT NULL,
  ts REAL NOT NULL,
  call_sid TEXT,
  status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempt_phone_ts ON attempt (phone, ts);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  event TEXT NOT NULL,
  phone_ref TEXT,
  detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_seen (
  key TEXT PRIMARY KEY,
  ts REAL NOT NULL
);
"""

# Schema changes after the first release, applied in order on open and
# tracked in PRAGMA user_version.
MIGRATIONS = (
    # 1: answered_by gets its own column instead of being glued onto status.
    "ALTER TABLE attempt ADD COLUMN answered_by TEXT",
    # 2: Idempotency-Key records for POST /calls.
    "CREATE TABLE IF NOT EXISTS idempotency ("
    " key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL, state TEXT NOT NULL,"
    " status INTEGER, body TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
)

# How long a completed Idempotency-Key answers with its stored response, and
# how long an in-flight one blocks a retry before it counts as abandoned (the
# process died between claiming the key and finishing the request).
IDEMPOTENCY_TTL_S = 86_400.0
IN_FLIGHT_TIMEOUT_S = 120.0

# Our own states (pending, unknown, not_placed) plus Twilio's CallStatus
# values. Higher rank wins; equal rank never overwrites. Rank 5 is terminal.
STATUS_RANK = {
    "pending": 0,
    "unknown": 1,
    "queued": 2,
    "initiated": 2,
    "ringing": 3,
    "in-progress": 4,
    "completed": 5,
    "busy": 5,
    "no-answer": 5,
    "canceled": 5,
    "failed": 5,
    "not_placed": 5,
}
# These never rang the phone, so they do not count against the daily cap.
NOT_PLACED = ("not_placed", "failed")


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """``new`` (go ahead), ``replay`` (send ``status``/``body``), ``mismatch`` or ``in_flight``."""

    outcome: str
    status: int | None = None
    body: str | None = None
    # Random per claim. Completing or releasing needs it, so a request that
    # was declared abandoned cannot overwrite the claim that replaced it.
    owner: str = ""


@dataclass(frozen=True, slots=True)
class Consent:
    id: int
    phone: str
    purpose: str
    timezone: str
    source: str
    evidence: str
    disclosure_version: str
    granted_at: float
    revoked_at: float | None


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        with self._lock:
            self.db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        (version,) = self.db.execute("PRAGMA user_version").fetchone()
        for n, stmt in enumerate(MIGRATIONS[version:], start=version + 1):
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(stmt)
                self.db.execute(f"PRAGMA user_version = {n}")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def tx(self) -> _Tx:
        """Serialised transaction. The gate check and the attempt insert share one."""
        return _Tx(self)

    @contextmanager
    def _locked(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.db

    def ping(self) -> None:
        with self._locked() as db:
            db.execute("SELECT 1").fetchone()

    def add_consent(
        self,
        phone: str,
        purpose: str,
        timezone: str,
        source: str,
        evidence: str,
        disclosure_version: str,
        at: float | None = None,
    ) -> int:
        if not evidence.strip():
            # A grant without evidence would supersede a real one below.
            raise ValueError("consent needs evidence: where and how the person agreed")
        ts = time.time() if at is None else at
        with self.tx():
            cur = self.db.execute(
                "INSERT INTO consent"
                " (phone, purpose, timezone, source, evidence, disclosure_version, granted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (phone, purpose, timezone, source, evidence, disclosure_version, ts),
            )
            new_id = int(cur.lastrowid or 0)
            self.db.execute(
                "UPDATE consent SET revoked_at = ?, revoked_via = ?"
                " WHERE phone = ? AND purpose = ? AND revoked_at IS NULL AND id != ?",
                (ts, f"superseded:{new_id}", phone, purpose, new_id),
            )
            return new_id

    def active_consents(self, phone: str, purpose: str) -> list[Consent]:
        """Unrevoked records for this number and purpose, newest first."""
        with self._locked() as db:
            rows = db.execute(
                "SELECT id, phone, purpose, timezone, source, evidence, disclosure_version,"
                " granted_at, revoked_at FROM consent"
                " WHERE phone = ? AND purpose = ? AND revoked_at IS NULL"
                " ORDER BY granted_at DESC, id DESC",
                (phone, purpose),
            ).fetchall()
        return [Consent(*r) for r in rows]

    def active_consent(self, phone: str, purpose: str) -> Consent | None:
        rows = self.active_consents(phone, purpose)
        return rows[0] if rows else None

    def revoke_all(self, phone: str, via: str, at: float | None = None) -> int:
        with self.tx():
            cur = self.db.execute(
                "UPDATE consent SET revoked_at = ?, revoked_via = ?"
                " WHERE phone = ? AND revoked_at IS NULL",
                (time.time() if at is None else at, via, phone),
            )
            return cur.rowcount

    def add_dnc(self, phone: str, reason: str, at: float | None = None) -> None:
        with self.tx():
            self.db.execute(
                "INSERT OR IGNORE INTO dnc (phone, added_at, reason) VALUES (?, ?, ?)",
                (phone, time.time() if at is None else at, reason),
            )

    def on_dnc(self, phone: str) -> bool:
        with self._locked() as db:
            return db.execute("SELECT 1 FROM dnc WHERE phone = ?", (phone,)).fetchone() is not None

    def attempts_since(self, phone: str, since: float) -> list[float]:
        """Timestamps of attempts that rang or may have rung the phone."""
        with self._locked() as db:
            rows = db.execute(
                "SELECT ts FROM attempt WHERE phone = ? AND ts >= ?"
                f" AND status NOT IN ({','.join('?' * len(NOT_PLACED))}) ORDER BY ts",
                (phone, since, *NOT_PLACED),
            ).fetchall()
        return [r[0] for r in rows]

    def add_attempt(self, phone: str, purpose: str, ts: float, status: str) -> int:
        with self._locked() as db:
            cur = db.execute(
                "INSERT INTO attempt (phone, purpose, ts, status) VALUES (?, ?, ?, ?)",
                (phone, purpose, ts, status),
            )
            return int(cur.lastrowid or 0)

    def attempt(self, attempt_id: int) -> tuple[str | None, str, str | None] | None:
        """(call_sid, status, answered_by) for one attempt."""
        with self._locked() as db:
            row = db.execute(
                "SELECT call_sid, status, answered_by FROM attempt WHERE id = ?", (attempt_id,)
            ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def update_attempt(self, attempt_id: int, *, call_sid: str | None = None, status: str) -> None:
        """Record what the dialer learned while placing the call.

        The status only moves forward: a status callback can arrive before
        the dialer has written the Twilio response, and must not be undone.
        """
        rank = STATUS_RANK.get(status, 0)
        with self.tx():
            row = self.db.execute(
                "SELECT status FROM attempt WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                return
            new_status = status if rank > STATUS_RANK.get(row[0], 0) else row[0]
            self.db.execute(
                "UPDATE attempt SET call_sid = COALESCE(?, call_sid), status = ? WHERE id = ?",
                (call_sid, new_status, attempt_id),
            )

    def advance_by_sid(self, call_sid: str, status: str, answered_by: str | None = None) -> str:
        """Apply a status callback if it moves the attempt forward.

        Returns ``updated``, ``stale`` (replayed, late or out of order),
        ``unknown_status`` or ``unknown_call``.
        """
        rank = STATUS_RANK.get(status)
        if rank is None:
            return "unknown_status"
        with self.tx():
            row = self.db.execute(
                "SELECT id, status FROM attempt WHERE call_sid = ?", (call_sid,)
            ).fetchone()
            if row is None:
                return "unknown_call"
            current = STATUS_RANK.get(row[1], 0)
            if rank <= current:
                return "stale"
            self.db.execute(
                "UPDATE attempt SET status = ?, answered_by = COALESCE(?, answered_by)"
                " WHERE id = ?",
                (status, answered_by or None, row[0]),
            )
            return "updated"

    def first_time(self, key: str, at: float | None = None) -> bool:
        """True the first time ``key`` is seen. Makes webhook side effects idempotent."""
        with self.tx():
            cur = self.db.execute(
                "INSERT OR IGNORE INTO webhook_seen (key, ts) VALUES (?, ?)",
                (key, time.time() if at is None else at),
            )
            return cur.rowcount == 1

    def claim_idempotency_key(
        self, key: str, fingerprint: str, at: float | None = None
    ) -> IdempotencyClaim:
        """Reserve ``key`` for one request, or say why it cannot be.

        A key belongs to the first request body it was used with. The same
        body again gets the stored response; a different body is a client
        bug and is refused rather than silently answered with someone else's
        result.
        """
        now = time.time() if at is None else at
        with self.tx():
            row = self.db.execute(
                "SELECT fingerprint, state, status, body, created_at, updated_at"
                " FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                fp, state, status, body, created, updated = row
                expired = now - created >= IDEMPOTENCY_TTL_S
                abandoned = state == "in_flight" and now - updated >= IN_FLIGHT_TIMEOUT_S
                if not (expired or abandoned):
                    if fp != fingerprint:
                        return IdempotencyClaim("mismatch")
                    if state == "in_flight":
                        return IdempotencyClaim("in_flight")
                    return IdempotencyClaim("replay", int(status), str(body))
                self.db.execute("DELETE FROM idempotency WHERE key = ?", (key,))
            owner = secrets.token_hex(8)
            self.db.execute(
                "INSERT INTO idempotency (key, fingerprint, owner, state, created_at, updated_at)"
                " VALUES (?, ?, ?, 'in_flight', ?, ?)",
                (key, fingerprint, owner, now, now),
            )
            return IdempotencyClaim("new", owner=owner)

    def complete_idempotency_key(
        self, claim: IdempotencyClaim, key: str, status: int, body: str, at: float | None = None
    ) -> bool:
        """Store the response for replays. False if this claim no longer owns the key."""
        with self.tx():
            cur = self.db.execute(
                "UPDATE idempotency SET state = 'done', status = ?, body = ?, updated_at = ?"
                " WHERE key = ? AND owner = ? AND state = 'in_flight'",
                (status, body, time.time() if at is None else at, key, claim.owner),
            )
            return cur.rowcount == 1

    def release_idempotency_key(self, claim: IdempotencyClaim, key: str) -> None:
        """Forget an in-flight key after a failure, so the client can retry with it."""
        with self.tx():
            self.db.execute(
                "DELETE FROM idempotency WHERE key = ? AND owner = ? AND state = 'in_flight'",
                (key, claim.owner),
            )

    def audit(
        self, event: str, phone_ref: str | None, detail: str, at: float | None = None
    ) -> None:
        with self._locked() as db:
            db.execute(
                "INSERT INTO audit (ts, event, phone_ref, detail) VALUES (?, ?, ?, ?)",
                (time.time() if at is None else at, event, phone_ref, detail),
            )

    def audit_rows(self) -> list[tuple[str, str | None, str]]:
        with self._locked() as db:
            rows = db.execute("SELECT event, phone_ref, detail FROM audit ORDER BY id").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def close(self) -> None:
        with self._locked() as db:
            db.close()


class _Tx:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.depth_owner = False

    def __enter__(self) -> Store:
        self.store._lock.acquire()
        if not self.store.db.in_transaction:
            self.store.db.execute("BEGIN IMMEDIATE")
            self.depth_owner = True
        return self.store

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self.depth_owner:
                self.store.db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
