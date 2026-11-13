"""Property tests for the parts with arithmetic or parsing in them."""

from datetime import UTC, datetime, timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from callgate.hours import in_window, next_window_start
from callgate.phone import PhoneError, normalize
from callgate.ratelimit import TokenBucket
from callgate.store import STATUS_RANK, Store

ZONES = [
    "UTC",
    "America/New_York",
    "America/Los_Angeles",
    "America/Phoenix",
    "Europe/London",
    "Australia/Lord_Howe",  # 30-minute DST shift
    "Asia/Kolkata",
]
instants = st.datetimes(
    min_value=datetime(2024, 1, 1), max_value=datetime(2030, 12, 31), timezones=st.just(UTC)
)
clock_times = st.times().map(lambda t: t.replace(second=0, microsecond=0))


@settings(max_examples=300, deadline=None)
@given(instants, st.sampled_from(ZONES), clock_times, clock_times)
def test_next_window_start_is_in_window_and_not_in_the_past(now, tz, start, end):
    assume(
        start < end
        and (datetime.combine(now.date(), end) - datetime.combine(now.date(), start))
        >= timedelta(minutes=90)
    )
    got = next_window_start(now, tz, start, end)
    assert got >= now
    assert in_window(got, tz, start, end)
    assert got - now < timedelta(days=2)


@given(st.text(max_size=30))
def test_normalize_is_idempotent_or_rejects(raw):
    try:
        e164 = normalize(raw)
    except PhoneError:
        return
    assert e164.startswith("+") and e164[1:].isdigit()
    assert normalize(e164) == e164


@given(st.lists(st.tuples(st.floats(0, 5), st.booleans()), max_size=60))
def test_bucket_never_exceeds_burst(steps):
    now = [0.0]
    b = TokenBucket(2.0, 3.0, clock=lambda: now[0])
    taken = 0
    for dt, give_back in steps:
        now[0] += dt
        if b.try_take():
            taken += 1
            if give_back:
                b.give_back()
        assert b._tokens <= b.burst + 1e-9


@given(st.lists(st.sampled_from(sorted(STATUS_RANK)), min_size=1, max_size=12))
def test_status_only_moves_forward(statuses):
    store = Store()
    try:
        aid = store.add_attempt("+15555550142", "p", 0.0, "pending")
        store.update_attempt(aid, call_sid="CA1", status="queued")
        best = STATUS_RANK["queued"]
        for s in statuses:
            store.advance_by_sid("CA1", s)
            row = store.attempt(aid)
            assert row is not None
            best = max(best, STATUS_RANK[s])
            assert STATUS_RANK[row[1]] == best
    finally:
        store.close()
