from callgate.twilio import compute_signature, validate_signature

# Vectors computed with the official twilio-python RequestValidator using the
# auth token "12345". They pin our implementation to Twilio's algorithm.
TOKEN = "12345"
URL = "https://example.com/twilio/voice/gather?appt=a1"
PARAMS = {
    "CallSid": "CA" + "0" * 32,
    "Digits": "9",
    "From": "+15555550100",
    "To": "+15555550199",
    "AccountSid": "AC" + "0" * 32,
}
EXPECTED = "4CojJnIxYO/uqqWhsrJiZTRoP/A="
STATUS = {"CallSid": "CA" + "1" * 32, "CallStatus": "completed"}
EXPECTED_WITH_PORT = "2e4F0/3nQZKO8O99Rs9H4vhTB6M="


def test_matches_official_vector():
    assert compute_signature(TOKEN, URL, PARAMS) == EXPECTED
    assert validate_signature(TOKEN, URL, PARAMS, EXPECTED)


def test_port_variants_accepted_both_ways():
    assert (
        compute_signature(TOKEN, "https://example.com:443/twilio/status", STATUS)
        == EXPECTED_WITH_PORT
    )
    assert validate_signature(
        TOKEN, "https://example.com/twilio/status", STATUS, EXPECTED_WITH_PORT
    )
    sig = compute_signature(TOKEN, "https://example.com/twilio/status", STATUS)
    assert validate_signature(TOKEN, "https://example.com:443/twilio/status", STATUS, sig)


def test_tampering_rejected():
    assert not validate_signature(TOKEN, URL, {**PARAMS, "Digits": "1"}, EXPECTED)
    assert not validate_signature(TOKEN, URL + "&x=1", PARAMS, EXPECTED)
    assert not validate_signature("other", URL, PARAMS, EXPECTED)
    assert not validate_signature(TOKEN, URL, PARAMS, None)
    assert not validate_signature("", URL, PARAMS, EXPECTED)


def test_repeated_params_sorted():
    a = compute_signature(TOKEN, "https://example.com/x", {"k": ["b", "a"]})
    b = compute_signature(TOKEN, "https://example.com/x", {"k": ["a", "b"]})
    assert a == b
