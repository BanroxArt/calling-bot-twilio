"""Idempotency-Key on POST /calls."""

import hashlib
import io
import json
from datetime import datetime

import pytest
from conftest import API, CALLEE, NOON, call

from callgate.config import ConfigError, Settings, load
from callgate.store import IDEMPOTENCY_TTL_S, IN_FLIGHT_TIMEOUT_S, Store

P = "appointment_reminder"
AUTH = {"Authorization": f"Bearer {API}"}
BODY = {"phone": CALLEE, "purpose": P, "appointment_id": "a1", "when_text": "Fri"}


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    import callgate.gate as gate_mod

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOON

    monkeypatch.setattr(gate_mod, "datetime", FixedDatetime)


def post(app, body=BODY, key="job-81-attempt", **headers):
    hdrs = {**AUTH, **headers}
    if key is not None:
        hdrs["Idempotency-Key"] = key
    status, _, data = call(app, "POST", "/calls", json_body=body, headers=hdrs)
    return status, data


def raw_post(app, body: bytes, key: str):
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/calls",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
        "HTTP_AUTHORIZATION": AUTH["Authorization"],
        "HTTP_IDEMPOTENCY_KEY": key,
    }
    out = {}

    def start_response(status, hdrs):
        out["status"] = int(status.split()[0])
        out["headers"] = dict(hdrs)

    data = b"".join(app(environ, start_response)).decode()
    return out["status"], out["headers"], data


def test_retry_with_same_key_replays_the_first_response(app, consented, fake):
    s1, h1, b1 = raw_post(app, json.dumps(BODY).encode(), "k1")
    assert s1 == 202 and "Idempotent-Replayed" not in h1
    s2, h2, b2 = raw_post(app, json.dumps(BODY).encode(), "k1")
    assert (s2, b2) == (s1, b1)
    assert h2["Idempotent-Replayed"] == "true"
    assert len(fake.requests) == 1


def test_without_a_key_the_retry_is_judged_by_the_gate(app, consented, fake):
    assert post(app, key=None)[0] == 202
    status, data = post(app, key=None)
    # Still only one call, but the caller gets a confusing "too soon".
    assert status == 409 and "too_soon_since_last_attempt" in data
    assert len(fake.requests) == 1


def test_key_order_and_whitespace_do_not_change_the_fingerprint(app, consented, fake):
    first = json.dumps(BODY).encode()
    reordered = json.dumps(dict(reversed(list(BODY.items()))), indent=2).encode()
    assert raw_post(app, first, "k2")[0] == 202
    status, headers, _ = raw_post(app, reordered, "k2")
    assert status == 202 and headers["Idempotent-Replayed"] == "true"


def test_same_key_different_body_is_422(app, consented, fake):
    assert post(app, key="k3")[0] == 202
    status, data = post(app, body={**BODY, "appointment_id": "a2"}, key="k3")
    assert status == 422 and "different request body" in data
    assert len(fake.requests) == 1


def test_gate_denials_are_replayed_too(app, store, fake):
    status, data = post(app, key="k4")  # no consent
    assert status == 409
    assert post(app, key="k4") == (status, data)
    assert fake.requests == []


def test_in_flight_key_is_409_with_retry_after(app, store):
    canon = json.dumps(BODY, sort_keys=True, separators=(",", ":")).encode()
    # Another worker holds the key for this exact body right now.
    store.claim_idempotency_key("k5", hashlib.sha256(canon).hexdigest())
    body = json.dumps(BODY).encode()
    status, headers, data = raw_post(app, body, "k5")
    assert status == 409 and headers["Retry-After"] == "1"
    assert "still being processed" in data


def test_unexpected_error_releases_the_key(app, consented, fake, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("worker died")

    monkeypatch.setattr(app.dialer, "remind", boom)
    assert post(app, key="k6")[0] == 500
    monkeypatch.undo()
    import callgate.gate as gate_mod

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOON

    monkeypatch.setattr(gate_mod, "datetime", FixedDatetime)
    assert post(app, key="k6")[0] == 202


@pytest.mark.parametrize("key", ["", "x" * 256, "caf\u00e9", "a\tb"])
def test_bad_keys_are_400(app, key):
    status, data = post(app, key=key)
    assert status == 400 and "Idempotency-Key" in data


def test_key_can_be_required(settings, store, gate, client):
    from callgate.app import App
    from callgate.dialer import Dialer

    fields = {f: getattr(settings, f) for f in settings.__dataclass_fields__}
    s = Settings(**{**fields, "require_idempotency_key": True})
    app = App(s, store, Dialer(s, store, gate, client))
    status, data = post(app, key=None)
    assert status == 400 and "required" in data
    assert load({"REQUIRE_IDEMPOTENCY_KEY": "true"}, require_twilio=False).require_idempotency_key
    with pytest.raises(ConfigError):
        load({"REQUIRE_IDEMPOTENCY_KEY": "maybe"}, require_twilio=False)


def test_store_claim_lifecycle():
    s = Store()
    t = 1_000.0
    first = s.claim_idempotency_key("k", "fp1", at=t)
    assert first.outcome == "new" and first.owner
    assert s.claim_idempotency_key("k", "fp1", at=t + 1).outcome == "in_flight"
    assert s.claim_idempotency_key("k", "fp2", at=t + 1).outcome == "mismatch"
    assert s.complete_idempotency_key(first, "k", 202, '{"placed":true}', at=t + 2)
    replay = s.claim_idempotency_key("k", "fp1", at=t + 3)
    assert (replay.outcome, replay.status, replay.body) == ("replay", 202, '{"placed":true}')
    # After the TTL the key is free for a new request.
    assert s.claim_idempotency_key("k", "fp2", at=t + IDEMPOTENCY_TTL_S).outcome == "new"


def test_abandoned_claim_is_taken_over_and_cannot_be_completed_later():
    s = Store()
    t = 1_000.0
    stale = s.claim_idempotency_key("k", "fp", at=t)
    fresh = s.claim_idempotency_key("k", "fp", at=t + IN_FLIGHT_TIMEOUT_S)
    assert fresh.outcome == "new" and fresh.owner != stale.owner
    # The process that was presumed dead comes back and tries to finish.
    assert not s.complete_idempotency_key(stale, "k", 202, "{}", at=t + 200)
    s.release_idempotency_key(stale, "k")
    assert s.claim_idempotency_key("k", "fp", at=t + 201).outcome == "in_flight"
    assert s.complete_idempotency_key(fresh, "k", 409, "{}", at=t + 202)


def test_migration_adds_the_table_to_an_existing_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    Store(str(path)).close()
    db = sqlite3.connect(path)
    db.execute("DROP TABLE idempotency")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    s = Store(str(path))
    assert s.claim_idempotency_key("k", "fp").outcome == "new"
    assert s.db.execute("PRAGMA user_version").fetchone()[0] == 2
