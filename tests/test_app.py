import json
from datetime import UTC, datetime

from conftest import API, CALLEE, FROM, NOON, call

P = "appointment_reminder"


def test_health(app):
    status, _, body = call(app, "GET", "/healthz")
    assert status == 200 and json.loads(body)["outbound"] is True


def test_webhooks_require_valid_signature(app):
    form = {"To": CALLEE, "From": FROM, "CallSid": "CA" + "4" * 32}
    assert call(app, "POST", "/twilio/voice/reminder?appt=a1", form, sign=False)[0] == 403
    assert call(app, "POST", "/twilio/voice/reminder?appt=a1", form, token="wrong")[0] == 403
    status, ctype, body = call(app, "POST", "/twilio/voice/reminder?appt=a1&when=Friday", form)
    assert status == 200 and ctype == "application/xml"
    assert "Example Clinic" in body and "Friday" in body


def test_signature_covers_query_string(app):
    form = {"To": CALLEE}
    from callgate.twilio import compute_signature

    sig = compute_signature(
        "test-token-for-unit-tests", "https://calls.example.com/twilio/voice/reminder?appt=a1", form
    )
    status, _, _ = call(
        app,
        "POST",
        "/twilio/voice/reminder?appt=a2",
        form,
        sign=False,
        headers={"X-Twilio-Signature": sig},
    )
    assert status == 403


def test_reminder_hangs_up_if_opted_out_after_dial(app, store):
    store.add_dnc(CALLEE, "sms_stop")
    _, _, body = call(app, "POST", "/twilio/voice/reminder", {"To": CALLEE})
    assert "Gather" not in body and "Hangup" in body


def test_press_9_opts_out(app, consented):
    _, _, body = call(app, "POST", "/twilio/voice/gather?appt=a1", {"To": CALLEE, "Digits": "9"})
    assert "will not get these calls again" in body
    assert consented.on_dnc(CALLEE)
    assert consented.active_consent(CALLEE, P) is None
    events = [e for e, _, _ in consented.audit_rows()]
    assert "opt_out" in events


def test_press_1_and_2(app):
    _, _, body = call(app, "POST", "/twilio/voice/gather?appt=a1", {"To": CALLEE, "Digits": "1"})
    assert "confirmed" in body
    _, _, body = call(app, "POST", "/twilio/voice/gather", {"To": CALLEE, "Digits": "2"})
    assert "<Dial" in body and "+15555550101" in body


def test_sms_stop_words(app, consented):
    _, _, body = call(app, "POST", "/twilio/sms/inbound", {"From": CALLEE, "Body": "  Stop "})
    assert "opted out" in body
    assert consented.on_dnc(CALLEE)


def test_sms_start_does_not_restore_call_consent(app, consented):
    call(app, "POST", "/twilio/sms/inbound", {"From": CALLEE, "Body": "STOP"})
    call(app, "POST", "/twilio/sms/inbound", {"From": CALLEE, "Body": "START"})
    assert consented.on_dnc(CALLEE)
    assert consented.active_consent(CALLEE, P) is None


def test_status_callback_updates_attempt(app, consented, gate, fake):
    d = app.dialer.remind(CALLEE, P, "a1", "Friday", now=NOON)
    assert d.placed
    call(
        app,
        "POST",
        "/twilio/status",
        {"CallSid": d.call_sid, "CallStatus": "completed", "AnsweredBy": "human"},
    )
    row = consented.db.execute(
        "SELECT status FROM attempt WHERE call_sid = ?", (d.call_sid,)
    ).fetchone()
    assert row == ("completed:human",)


def test_api_requires_token(app):
    status, _, _ = call(app, "POST", "/calls", json_body={"phone": CALLEE, "purpose": P})
    assert status == 401
    status, _, _ = call(
        app,
        "POST",
        "/calls",
        json_body={"phone": CALLEE, "purpose": P},
        headers={"Authorization": "Bearer nope"},
    )
    assert status == 401


def test_api_refuses_without_consent_and_never_dials(app, fake):
    status, _, body = call(
        app,
        "POST",
        "/calls",
        json_body={"phone": CALLEE, "purpose": P},
        headers={"Authorization": f"Bearer {API}"},
    )
    assert status == 409 and json.loads(body)["reasons"] == ["no_consent"]
    assert fake.requests == []


def test_api_places_call_with_consent(app, consented, fake, monkeypatch):
    import callgate.gate as gate_mod

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOON

    monkeypatch.setattr(gate_mod, "datetime", FixedDatetime)
    status, _, body = call(
        app,
        "POST",
        "/calls",
        json_body={"phone": CALLEE, "purpose": P, "appointment_id": "a1", "when_text": "Fri"},
        headers={"Authorization": f"Bearer {API}"},
    )
    assert status == 202, body
    out = json.loads(body)
    assert out["placed"] and out["call_sid"]
    ((_, _, form, _),) = fake.requests
    assert form["Url"][0].startswith("https://calls.example.com/twilio/voice/reminder?appt=a1")


def test_bad_api_body(app):
    status, _, _ = call(
        app,
        "POST",
        "/calls",
        json_body={"phone": CALLEE},
        headers={"Authorization": f"Bearer {API}"},
    )
    assert status == 400


def test_dial_failure_marks_attempt(app, consented, fake):
    fake.replies = [(500, {"message": "oops"})]
    r = app.dialer.remind(CALLEE, P, "a1", "Fri", now=datetime(2026, 3, 18, 15, tzinfo=UTC))
    assert not r.placed and r.error
    assert consented.db.execute("SELECT status FROM attempt").fetchone() == ("unknown",)


def test_unknown_route_and_method(app):
    assert call(app, "GET", "/nope")[0] == 404
    assert call(app, "GET", "/twilio/status")[0] == 405
