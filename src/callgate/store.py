"""SQLite store for consent, do-not-call, attempts and the audit trail.

Consent is append-only: granting adds a row, revoking stamps ``revoked_at``.
Nothing is ever updated back to active, so the history of what a person
agreed to and when is always reconstructable.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

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
"""


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
        self.db.executescript(SCHEMA)

    def tx(self):
        """Serialised transaction. The gate check and the attempt insert share one."""
        return _Tx(self)

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
        with self.tx():
            cur = self.db.execute(
                "INSERT INTO consent (phone, purpose, timezone, source, evidence, disclosure_version, granted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (phone, purpose, timezone, source, evidence, disclosure_version, at or time.time()),
            )
            return int(cur.lastrowid or 0)

    def active_consent(self, phone: str, purpose: str) -> Consent | None:
        row = self.db.execute(
            "SELECT id, phone, purpose, timezone, source, evidence, disclosure_version, granted_at, revoked_at"
            " FROM consent WHERE phone = ? AND purpose = ? AND revoked_at IS NULL"
            " ORDER BY granted_at DESC LIMIT 1",
            (phone, purpose),
        ).fetchone()
        return Consent(*row) if row else None

    def revoke_all(self, phone: str, via: str, at: float | None = None) -> int:
        with self.tx():
            cur = self.db.execute(
                "UPDATE consent SET revoked_at = ?, revoked_via = ? WHERE phone = ? AND revoked_at IS NULL",
                (at or time.time(), via, phone),
            )
            return cur.rowcount

    def add_dnc(self, phone: str, reason: str, at: float | None = None) -> None:
        with self.tx():
            self.db.execute(
                "INSERT OR IGNORE INTO dnc (phone, added_at, reason) VALUES (?, ?, ?)",
                (phone, at or time.time(), reason),
            )

    def on_dnc(self, phone: str) -> bool:
        return self.db.execute("SELECT 1 FROM dnc WHERE phone = ?", (phone,)).fetchone() is not None

    def attempts_since(self, phone: str, since: float) -> list[float]:
        rows = self.db.execute(
            "SELECT ts FROM attempt WHERE phone = ? AND ts >= ? ORDER BY ts", (phone, since)
        ).fetchall()
        return [r[0] for r in rows]

    def add_attempt(self, phone: str, purpose: str, ts: float, status: str) -> int:
        cur = self.db.execute(
            "INSERT INTO attempt (phone, purpose, ts, status) VALUES (?, ?, ?, ?)",
            (phone, purpose, ts, status),
        )
        return int(cur.lastrowid or 0)

    def update_attempt(self, attempt_id: int, *, call_sid: str | None = None, status: str) -> None:
        with self.tx():
            self.db.execute(
                "UPDATE attempt SET call_sid = COALESCE(?, call_sid), status = ? WHERE id = ?",
                (call_sid, status, attempt_id),
            )

    def update_attempt_by_sid(self, call_sid: str, status: str) -> int:
        with self.tx():
            return self.db.execute(
                "UPDATE attempt SET status = ? WHERE call_sid = ?", (status, call_sid)
            ).rowcount

    def audit(
        self, event: str, phone_ref: str | None, detail: str, at: float | None = None
    ) -> None:
        self.db.execute(
            "INSERT INTO audit (ts, event, phone_ref, detail) VALUES (?, ?, ?, ?)",
            (at or time.time(), event, phone_ref, detail),
        )

    def audit_rows(self) -> list[tuple[str, str | None, str]]:
        return self.db.execute("SELECT event, phone_ref, detail FROM audit ORDER BY id").fetchall()

    def close(self) -> None:
        self.db.close()


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

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.depth_owner:
                self.store.db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
