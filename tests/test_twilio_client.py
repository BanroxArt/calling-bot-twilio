import dataclasses
from dataclasses import replace

import pytest
from conftest import CALLEE, NOON, SID, TOKEN, FakeTwilio

from callgate.gate import Gate
from callgate.twilio import Backoff, TwilioClient, TwilioError


def approval(gate, consented):
    # A permissive gate so one test can mint several approvals in a row.
    loose = Gate(
        replace(gate.s, min_gap_minutes=0, max_attempts_per_day=100), consented, gate.bucket
    )
    return loose.check(CALLEE, "appointment_reminder", NOON).approval


def client(fake, sleeps=None):
    return TwilioClient(
        SID,
        TOKEN,
        transport=fake,
        backoff=Backoff(attempts=3),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def kwargs():
    return {
        "from_number": "+15555550100",
        "twiml_url": "https://calls.example.com/t",
        "status_callback": "https://calls.example.com/s",
    }


def test_create_call_form_and_auth(gate, consented):
    fake = FakeTwilio()
    out = client(fake).create_call(approval(gate, consented), **kwargs())
    assert out["status"] == "queued"
    method, url, form, headers = fake.requests[0]
    assert method == "POST" and url.endswith(f"/Accounts/{SID}/Calls.json")
    assert form["To"] == [CALLEE] and form["From"] == ["+15555550100"]
    assert form["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]
    assert headers["Authorization"].startswith("Basic ")
    assert TOKEN not in repr(client(fake))


def test_refuses_without_real_approval(gate, consented):
    a = approval(gate, consented)
    forged = object.__new__(type(a))
    for f in dataclasses.fields(a):
        object.__setattr__(forged, f.name, getattr(a, f.name))
    object.__setattr__(forged, "_key", None)
    with pytest.raises(PermissionError):
        client(FakeTwilio()).create_call(forged, **kwargs())
    with pytest.raises(PermissionError):
        client(FakeTwilio()).create_call("+15555550142", **kwargs())  # type: ignore[arg-type]


def test_retries_429_with_retry_after_then_succeeds(gate, consented):
    sleeps = []
    fake = FakeTwilio(
        (429, {"message": "slow down"}, {"Retry-After": "2"}),
        (201, {"sid": "CA" + "2" * 32, "status": "queued"}),
    )
    out = client(fake, sleeps).create_call(approval(gate, consented), **kwargs())
    assert out["sid"].startswith("CA")
    assert sleeps == [2.0]
    assert len(fake.requests) == 2


def test_gives_up_after_attempts(gate, consented):
    sleeps = []
    fake = FakeTwilio((503, {"message": "busy"}))
    with pytest.raises(TwilioError) as e:
        client(fake, sleeps).create_call(approval(gate, consented), **kwargs())
    assert e.value.status == 503 and not e.value.ambiguous
    assert len(fake.requests) == 3 and len(sleeps) == 2


def test_no_retry_on_ambiguous_500_or_client_error(gate, consented):
    fake = FakeTwilio((500, {"message": "oops"}))
    with pytest.raises(TwilioError) as e:
        client(fake).create_call(approval(gate, consented), **kwargs())
    assert e.value.ambiguous and len(fake.requests) == 1

    fake = FakeTwilio((400, {"message": "bad To", "code": 21211}))
    with pytest.raises(TwilioError) as e:
        client(fake).create_call(approval(gate, consented), **kwargs())
    assert e.value.code == 21211 and not e.value.ambiguous and len(fake.requests) == 1


def test_timeout_is_ambiguous_refused_is_retried(gate, consented):
    fake = FakeTwilio(TimeoutError())
    with pytest.raises(TwilioError) as e:
        client(fake).create_call(approval(gate, consented), **kwargs())
    assert e.value.ambiguous and len(fake.requests) == 1

    fake = FakeTwilio(ConnectionRefusedError(), (201, {"sid": "CA" + "3" * 32}))
    assert client(fake).create_call(approval(gate, consented), **kwargs())["sid"]


def test_backoff_bounds():
    b = Backoff(base_s=1, cap_s=4)
    assert all(0 <= b.delay(n) <= 4 for n in range(10))
    assert b.delay(0, "30") == 4
    assert b.delay(0, "soon") <= 1
