import sqlite3
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import CALLEE, NOON

import callgate.gate as gate_mod
from callgate.gate import Approval, Gate, redeem
from callgate.ratelimit import TokenBucket

P = "appointment_reminder"


def test_allows_with_consent_and_records_attempt(gate, consented):
    d = gate.check(CALLEE, P, NOON)
    assert d.allowed and d.approval.phone == CALLEE
    assert consented.attempts_since(CALLEE, 0) == [NOON.timestamp()]
    event, ref, detail = consented.audit_rows()[-1]
    assert event == "gate_allow" and CALLEE not in (ref or "") and "ok" in detail


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda s, st: (replace(s, outbound_enabled=False), st), "outbound_disabled"),
        (lambda s, st: (s, st), "no_consent"),
    ],
)
def test_basic_denials(settings, store, setup, reason):
    s, st = setup(settings, store)
    d = Gate(s, st).check(CALLEE, P, NOON)
    assert not d.allowed and d.reasons == (reason,)


def test_invalid_number(gate, consented):
    assert gate.check("not a number", P, NOON).reasons == ("invalid_number",)


def test_consent_is_per_purpose(gate, consented):
    assert gate.check(CALLEE, "marketing", NOON).reasons == ("no_consent",)


def test_dnc_beats_consent(gate, consented):
    consented.add_dnc(CALLEE, "manual")
    assert gate.check(CALLEE, P, NOON).reasons == ("do_not_call",)


def test_revoked_consent(gate, consented):
    consented.revoke_all(CALLEE, "test")
    assert gate.check(CALLEE, P, NOON).reasons == ("no_consent",)


def test_expired_consent(settings, consented):
    g = Gate(replace(settings, consent_max_age_days=0), consented)
    assert g.check(CALLEE, P, NOON).reasons == ("consent_expired",)


def _raw_consent(store, evidence, at):
    # Rows written by an older version or a bulk import, bypassing add_consent.
    store.db.execute(
        "INSERT INTO consent (phone, purpose, timezone, source, evidence, disclosure_version,"
        " granted_at) VALUES (?, ?, 'America/New_York', 'import', ?, 'v1', ?)",
        (CALLEE, P, evidence, at),
    )


def test_consent_without_evidence_is_not_consent(gate, store):
    _raw_consent(store, "  ", NOON.timestamp() - 10)
    assert gate.check(CALLEE, P, NOON).reasons == ("consent_without_evidence",)
    with pytest.raises(ValueError):
        store.add_consent(CALLEE, P, "America/New_York", "import", " ", "v1")


def test_newer_incomplete_row_does_not_shadow_valid_consent(gate, consented):
    # Regression: only the newest unrevoked row was looked at, so an
    # evidence-less row added later denied a person who had valid consent.
    _raw_consent(consented, "", NOON.timestamp() - 10)
    d = gate.check(CALLEE, P, NOON)
    assert d.allowed and d.approval is not None
    assert d.approval.consent_id == 1


def test_regrant_supersedes_older_rows(store):
    first = store.add_consent(CALLEE, P, "UTC", "web_form", "form:1", "v1", at=1000.0)
    second = store.add_consent(CALLEE, P, "America/Chicago", "web_form", "form:2", "v2", at=2000.0)
    assert [c.id for c in store.active_consents(CALLEE, P)] == [second]
    row = store.db.execute(
        "SELECT revoked_at, revoked_via FROM consent WHERE id = ?", (first,)
    ).fetchone()
    assert row == (2000.0, f"superseded:{second}")


def test_epoch_zero_timestamps_are_kept(store):
    # Regression: "at or time.time()" replaced an explicit 0.0 with now.
    cid = store.add_consent(CALLEE, P, "UTC", "web_form", "form:1", "v1", at=0.0)
    assert store.active_consents(CALLEE, P)[0].granted_at == 0.0
    store.add_dnc(CALLEE, "manual", at=0.0)
    store.audit("x", None, "y", at=0.0)
    assert store.db.execute("SELECT added_at FROM dnc").fetchone() == (0.0,)
    assert store.db.execute("SELECT ts FROM audit").fetchone() == (0.0,)
    assert cid


def test_quiet_hours_with_retry_after(gate, consented):
    late = datetime(2026, 3, 19, 2, 30, tzinfo=UTC)  # 22:30 in New York
    d = gate.check(CALLEE, P, late)
    assert d.reasons == ("outside_calling_hours",)
    assert d.retry_after == datetime(2026, 3, 19, 12, 0, tzinfo=UTC)


def test_bad_timezone_on_record(gate, store):
    store.add_consent(
        CALLEE, P, "Nowhere/Land", "web_form", "form:1", "v1", at=NOON.timestamp() - 10
    )
    assert gate.check(CALLEE, P, NOON).reasons == ("bad_timezone_on_consent",)


def test_attempt_gap_and_daily_cap(gate, consented):
    assert gate.check(CALLEE, P, NOON).allowed
    d = gate.check(CALLEE, P, NOON + timedelta(minutes=30))
    assert d.reasons == ("too_soon_since_last_attempt",)
    assert d.retry_after == NOON + timedelta(minutes=120)
    assert gate.check(CALLEE, P, NOON + timedelta(hours=3)).allowed
    assert gate.check(CALLEE, P, NOON + timedelta(hours=6)).reasons == ("daily_attempt_cap",)
    assert gate.check(CALLEE, P, NOON + timedelta(hours=25)).allowed


def test_global_rate_limit(settings, consented):
    g = Gate(settings, consented, TokenBucket(0.001, 1))
    assert g.check(CALLEE, P, NOON).allowed
    other = "+15555550143"
    consented.add_consent(
        other, P, "America/New_York", "web_form", "form:2", "v2", at=NOON.timestamp() - 10
    )
    assert g.check(other, P, NOON).reasons == ("rate_limited",)


def test_preview_has_no_side_effects(gate, consented):
    assert gate.preview(CALLEE, P, NOON).allowed
    assert gate.preview(CALLEE, P, NOON).approval is None
    assert consented.attempts_since(CALLEE, 0) == []
    assert consented.audit_rows() == []


def test_approval_cannot_be_built_by_hand(gate, consented):
    with pytest.raises(PermissionError):
        Approval(CALLEE, P, 1, 1)
    real = gate.check(CALLEE, P, NOON).approval
    # Regression: the old sentinel was a module global, so importing it was
    # enough to build an Approval. A tag copied from a real approval does not
    # carry over to different fields either.
    with pytest.raises(PermissionError):
        Approval("+15555550199", P, real.consent_id, real.attempt_id, real.tag)
    assert not hasattr(gate_mod, "_MINT")


def test_approval_is_single_use_and_expires(gate, consented, monkeypatch):
    a = gate.check(CALLEE, P, NOON).approval
    assert a.valid
    redeem(a)
    assert not a.valid
    with pytest.raises(PermissionError, match="already used"):
        redeem(a)
    b = Gate(replace(gate.s, min_gap_minutes=0), consented, gate.bucket).check(CALLEE, P, NOON)
    clock = [time.monotonic()]
    monkeypatch.setattr(gate_mod.time, "monotonic", lambda: clock[0] + gate_mod.APPROVAL_TTL_S + 1)
    with pytest.raises(PermissionError, match="expired"):
        redeem(b.approval)


def test_blank_audit_key_fails_closed(settings, consented):
    # Regression: an empty AUDIT_HASH_KEY silently fell back to masked
    # numbers in the audit log. With outbound on, the gate now refuses.
    g = Gate(replace(settings, audit_key=b""), consented)
    assert g.check(CALLEE, P, NOON).reasons == ("no_audit_key",)
    assert consented.attempts_since(CALLEE, 0) == []


def test_token_returned_when_recording_fails(settings, consented, monkeypatch):
    bucket = TokenBucket(0.001, 1)
    g = Gate(settings, consented, bucket)

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(consented, "add_attempt", boom)
    with pytest.raises(sqlite3.OperationalError):
        g.check(CALLEE, P, NOON)
    monkeypatch.undo()
    # Without the refund the only token would be gone and this would be rate_limited.
    assert g.check(CALLEE, P, NOON).allowed
    assert consented.attempts_since(CALLEE, 0) == [NOON.timestamp()]


def test_unplaced_calls_do_not_use_the_daily_cap(settings, consented):
    g = Gate(replace(settings, min_gap_minutes=0), consented, TokenBucket(100, 100))
    for _ in range(5):
        d = g.check(CALLEE, P, NOON)
        assert d.allowed
        consented.update_attempt(d.approval.attempt_id, status="not_placed")
    assert consented.attempts_since(CALLEE, 0) == []
    d = g.check(CALLEE, P, NOON)
    consented.update_attempt(d.approval.attempt_id, status="unknown")  # may have rung
    assert len(consented.attempts_since(CALLEE, 0)) == 1


def test_concurrent_checks_respect_daily_cap(settings, consented):
    g = Gate(replace(settings, min_gap_minutes=0), consented, TokenBucket(100, 100))
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(g.check(CALLEE, P, NOON).allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == settings.max_attempts_per_day


def test_old_database_is_migrated(tmp_path):
    import callgate.store as store_mod

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(store_mod.SCHEMA)  # first release: no answered_by
    old.execute(
        "INSERT INTO attempt (phone, purpose, ts, status) VALUES ('x', 'p', 1, 'completed')"
    )
    old.commit()
    old.close()
    s = store_mod.Store(path)
    assert s.db.execute("PRAGMA user_version").fetchone() == (len(store_mod.MIGRATIONS),)
    assert s.db.execute("SELECT status, answered_by FROM attempt").fetchone() == ("completed", None)
    s.close()
    store_mod.Store(path).close()  # reopening does not re-apply
