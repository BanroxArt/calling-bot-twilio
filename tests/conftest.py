from __future__ import annotations

import io
import json
import urllib.parse
from datetime import UTC, datetime

import pytest

from callgate.app import App
from callgate.config import Settings
from callgate.dialer import Dialer
from callgate.gate import Gate
from callgate.ratelimit import TokenBucket
from callgate.store import Store
from callgate.twilio import Backoff, HttpResponse, TwilioClient, compute_signature

# Obviously fake values. 555-01xx numbers are reserved for fiction.
SID = "AC" + "0" * 32
TOKEN = "test-token-for-unit-tests"
FROM = "+15555550100"
CALLEE = "+15555550142"
BASE = "https://calls.example.com"
API = "api-" + "x" * 28
# 15:00 UTC on a Wednesday: 10:00 in New York, 07:00 in Los Angeles.
NOON = datetime(2026, 3, 18, 15, 0, tzinfo=UTC)


class FakeTwilio:
    """Records requests; replies from a queue of (status, body) pairs."""

    def __init__(self, *replies):
        self.replies = list(replies) or [(201, {"sid": "CA" + "1" * 32, "status": "queued"})]
        self.requests = []

    def __call__(self, method, url, body, headers):
        self.requests.append((method, url, urllib.parse.parse_qs(body.decode()), dict(headers)))
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(item, BaseException):
            raise item
        status, payload = item[0], item[1]
        hdrs = item[2] if len(item) > 2 else {}
        return HttpResponse(status, json.dumps(payload).encode(), hdrs)


@pytest.fixture
def settings():
    return Settings(
        account_sid=SID,
        auth_token=TOKEN,
        from_number=FROM,
        public_base_url=BASE,
        caller_name="Example Clinic",
        support_number="+15555550101",
        api_token=API,
        audit_key=b"k" * 32,
        outbound_enabled=True,
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def consented(store):
    store.add_consent(
        CALLEE,
        "appointment_reminder",
        "America/New_York",
        "web_form",
        "form:abc123",
        "v2",
        at=NOON.timestamp() - 86_400,
    )
    return store


@pytest.fixture
def fake():
    return FakeTwilio()


@pytest.fixture
def gate(settings, store):
    return Gate(settings, store, TokenBucket(100, 100))


@pytest.fixture
def client(fake):
    return TwilioClient(
        SID, TOKEN, transport=fake, backoff=Backoff(attempts=3), sleep=lambda s: None
    )


@pytest.fixture
def app(settings, store, gate, client):
    return App(settings, store, Dialer(settings, store, gate, client))


def call(app, method, path, form=None, headers=None, json_body=None, sign=True, token=TOKEN):
    path_only, _, qs = path.partition("?")
    body = b""
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
    if json_body is not None:
        body = json.dumps(json_body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_only,
        "QUERY_STRING": qs,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    if sign and form is not None:
        environ["HTTP_X_TWILIO_SIGNATURE"] = compute_signature(token, BASE + path, form)
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    out = {}

    def start_response(status, hdrs):
        out["status"] = int(status.split()[0])
        out["headers"] = dict(hdrs)

    data = b"".join(app(environ, start_response)).decode()
    return out["status"], out["headers"]["Content-Type"], data
