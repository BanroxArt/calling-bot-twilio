"""Retention: what goes, what stays, and that a dry run changes nothing."""

import json

import pytest
from conftest import CALLEE

from callgate.cli import main
from callgate.retention import DAY, RetentionError, RetentionPolicy, apply, plan
from callgate.store import Store

NOW = 1_800_000_000.0
OTHER = "+15555550143"


def seeded(path=":memory:"):
    s = Store(path)
    # Consent: one revoked long ago, one revoked recently, one active and old.
    old = s.add_consent(OTHER, "r", "America/New_York", "web", "f1", "v1", at=NOW - 3000 * DAY)
    s.revoke_all(OTHER, "sms_stop", at=NOW - 2000 * DAY)
    s.add_consent(OTHER, "r", "America/New_York", "web", "f2", "v1", at=NOW - 900 * DAY)
    s.revoke_all(OTHER, "sms_stop", at=NOW - 100 * DAY)
    s.add_consent(CALLEE, "r", "America/New_York", "web", "f3", "v1", at=NOW - 3000 * DAY)
    s.add_dnc(OTHER, "sms_stop", at=NOW - 4000 * DAY)
    for age in (2000, 400, 30, 0.5):
        s.add_attempt(CALLEE, "r", NOW - age * DAY, "completed")
        s.audit("gate_allow", "ref", "r: ok", NOW - age * DAY)
    s.first_time("confirm:CA1", at=NOW - 200 * DAY)
    s.first_time("confirm:CA2", at=NOW - 10 * DAY)
    s.claim_idempotency_key("old", "fp", at=NOW - 2 * DAY)
    s.claim_idempotency_key("new", "fp", at=NOW - 60)
    return s, old


def count(s, table):
    return s.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_plan_counts_and_changes_nothing():
    s, _ = seeded()
    before = {t: count(s, t) for t in ("audit", "attempt", "consent", "webhook_seen", "dnc")}
    r = plan(s, RetentionPolicy(), NOW)
    assert not r.applied
    assert r.counts == {
        "audit": 1,
        "attempt": 2,
        "consent_revoked": 1,
        "webhook_seen": 1,
        "idempotency": 1,
    }
    assert {t: count(s, t) for t in before} == before


def test_apply_deletes_exactly_the_plan_and_keeps_evidence():
    s, old_consent = seeded()
    expected = plan(s, RetentionPolicy(), NOW).counts
    r = apply(s, RetentionPolicy(), NOW)
    assert r.applied and r.counts == expected
    ids = {row[0] for row in s.db.execute("SELECT id FROM consent")}
    assert old_consent not in ids and len(ids) == 2
    # The active consent is three thousand days old and still here.
    assert s.active_consent(CALLEE, "r") is not None
    assert s.on_dnc(OTHER)
    assert s.attempts_since(CALLEE, NOW - 2 * DAY) == [NOW - 0.5 * DAY]
    assert s.first_time("confirm:CA1", at=NOW)  # forgotten, so new again
    assert not s.first_time("confirm:CA2", at=NOW)
    assert s.claim_idempotency_key("new", "fp", at=NOW).outcome == "in_flight"


def test_apply_writes_one_audit_row_with_counts_only():
    s, _ = seeded()
    apply(s, RetentionPolicy(), NOW)
    event, ref, detail = s.audit_rows()[-1]
    assert event == "retention" and ref is None
    assert detail == "audit=1, attempt=2, consent_revoked=1, webhook_seen=1, idempotency=1"
    assert CALLEE not in detail


def test_second_run_deletes_nothing():
    s, _ = seeded()
    apply(s, RetentionPolicy(), NOW)
    assert set(apply(s, RetentionPolicy(), NOW).counts.values()) == {0}


def test_small_batches_delete_everything_due():
    s = Store()
    for i in range(23):
        s.audit("gate_deny", None, "x", NOW - 3000 * DAY + i)
    r = apply(s, RetentionPolicy(), NOW, batch=4)
    assert r.counts["audit"] == 23
    assert [row[0] for row in s.audit_rows()] == ["retention"]


def test_secure_delete_is_on_during_the_run_and_restored(monkeypatch):
    s, _ = seeded()
    s.db.execute("PRAGMA secure_delete = OFF")
    seen = []
    real_tx = s.tx

    def spy():
        seen.append(s.db.execute("PRAGMA secure_delete").fetchone()[0])
        return real_tx()

    monkeypatch.setattr(s, "tx", spy)
    apply(s, RetentionPolicy(), NOW)
    monkeypatch.undo()
    assert 1 in seen
    assert s.db.execute("PRAGMA secure_delete").fetchone()[0] == 0


def test_policy_floors():
    with pytest.raises(RetentionError, match="daily cap"):
        RetentionPolicy(attempt_days=1)
    with pytest.raises(RetentionError):
        RetentionPolicy(audit_days=0)
    s = Store()
    with pytest.raises(RetentionError):
        apply(s, RetentionPolicy(), NOW, batch=0)


def test_shorter_policy_from_settings():
    s, _ = seeded()
    r = plan(s, RetentionPolicy(audit_days=20, attempt_days=20, webhook_days=5), NOW)
    assert r.counts["audit"] == 3 and r.counts["attempt"] == 3 and r.counts["webhook_seen"] == 2


def test_cli_dry_run_is_read_only_and_apply_reports(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.db"
    s, _ = seeded(str(path))
    s.close()
    for k in ("RETAIN_AUDIT_DAYS", "RETAIN_ATTEMPT_DAYS"):
        monkeypatch.delenv(k, raising=False)
    before = path.read_bytes()
    assert main(["--db", str(path), "retention"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and "would_delete" in out
    assert path.read_bytes() == before
    assert main(["--db", str(path), "retention", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is True and out["deleted"]["audit"] >= 1
    monkeypatch.setenv("RETAIN_ATTEMPT_DAYS", "1")
    assert main(["--db", str(path), "retention"]) == 2
