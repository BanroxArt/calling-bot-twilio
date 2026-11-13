from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import CALLEE, NOON

from callgate.gate import Approval, Gate
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


def test_consent_without_evidence_is_not_consent(gate, store):
    store.add_consent(CALLEE, P, "America/New_York", "import", "  ", "v1", at=NOON.timestamp() - 10)
    assert gate.check(CALLEE, P, NOON).reasons == ("consent_without_evidence",)


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


def test_approval_cannot_be_forged():
    with pytest.raises(PermissionError):
        Approval(CALLEE, P, 1, 1)
    with pytest.raises(PermissionError):
        Approval(CALLEE, P, 1, 1, _key=object())
