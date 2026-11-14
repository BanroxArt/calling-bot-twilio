"""Webhook and API endpoints, as a WSGI app.

Twilio-facing routes (``/twilio/...``) require a valid X-Twilio-Signature.
The one internal route, ``POST /calls``, requires a bearer token. Health is
open. Everything else is 404.

``POST /calls`` honours an ``Idempotency-Key`` header (the IETF httpapi
draft's semantics): the first request with a key runs, a retry with the same
key and body gets the stored response with ``Idempotent-Replayed: true``,
the same key with a different body is 422, and a retry while the first is
still running is 409. The gate's attempt cap and minimum gap already stop a
retry from ringing the phone twice; what the key adds is the right answer
for the retry (the original 202 and call SID instead of a 409 that says
"too soon").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import urllib.parse
from collections.abc import Callable, Iterable
from typing import Any

from callgate import twiml
from callgate.config import Settings
from callgate.dialer import Dialer
from callgate.phone import PhoneError, mask, normalize
from callgate.store import Store
from callgate.twilio import validate_signature

log = logging.getLogger(__name__)

# Carrier-standard opt-out and opt-in keywords for SMS.
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "revoke"}
START_WORDS = {"start", "unstop", "yes"}
MAX_BODY = 64 * 1024
# The draft leaves the format to the server. Printable ASCII, 1 to 255
# characters, covers UUIDs and any job-queue id without letting a client
# park megabytes in the table.
IDEMPOTENCY_KEY = re.compile(r"[\x20-\x7e]{1,255}")
EXTRA_HEADERS = "callgate.extra_headers"


class _BadRequest(Exception):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status = status
        self.msg = msg


STATUS_TEXT = {
    200: "OK",
    202: "Accepted",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    422: "Unprocessable Content",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class App:
    def __init__(self, settings: Settings, store: Store, dialer: Dialer) -> None:
        self.s = settings
        self.store = store
        self.dialer = dialer

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        extra: list[tuple[str, str]] = []
        environ[EXTRA_HEADERS] = extra
        try:
            status, ctype, body = self.dispatch(method, path, environ)
        except _BadRequest as exc:
            status, ctype, body = exc.status, "application/json", json.dumps({"error": exc.msg})
        except Exception:
            log.exception("unhandled error", extra={"path": path})
            status, ctype, body = 500, "application/json", json.dumps({"error": "internal error"})
        data = body.encode()
        start_response(
            f"{status} {STATUS_TEXT.get(status, 'Error')}",
            [("Content-Type", ctype), ("Content-Length", str(len(data))), *extra],
        )
        return [data]

    # plumbing

    @staticmethod
    def _body(environ: dict[str, Any]) -> bytes:
        """Read the request body, refusing anything over MAX_BODY.

        Truncating instead would hand a partial form to the signature check
        or a partial JSON document to the parser and report the wrong error.
        """
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError as exc:
            raise _BadRequest(400, "bad content length") from exc
        if length < 0:
            raise _BadRequest(400, "bad content length")
        if length > MAX_BODY:
            raise _BadRequest(413, "body too large")
        data: bytes = environ["wsgi.input"].read(length) if length else b""
        return data

    @classmethod
    def _form(cls, environ: dict[str, Any]) -> dict[str, str]:
        raw = cls._body(environ).decode("utf-8", errors="replace")
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def _public_url(self, environ: dict[str, Any]) -> str:
        # Validate against the URL Twilio was configured with, not the Host
        # header, which a proxy may have rewritten.
        qs = str(environ.get("QUERY_STRING", ""))
        path = str(environ.get("PATH_INFO", "/"))
        return self.s.public_base_url + path + (f"?{qs}" if qs else "")

    def _signed(self, environ: dict[str, Any], form: dict[str, str]) -> bool:
        sig = environ.get("HTTP_X_TWILIO_SIGNATURE")
        ok = validate_signature(self.s.auth_token, self._public_url(environ), form, sig)
        if not ok:
            log.warning("bad twilio signature", extra={"path": environ.get("PATH_INFO")})
        return ok

    def dispatch(self, method: str, path: str, environ: dict[str, Any]) -> tuple[int, str, str]:
        if path == "/healthz" and method == "GET":
            # Unauthenticated, so it says nothing about configuration (whether
            # outbound is on is not the internet's business). It does touch
            # the database, since a gate without its store cannot decide.
            try:
                self.store.ping()
            except Exception:
                log.exception("health check failed")
                return 503, "application/json", json.dumps({"status": "unavailable"})
            return 200, "application/json", json.dumps({"status": "ok"})
        if path == "/calls" and method == "POST":
            return self.api_call(environ)
        routes = {
            "/twilio/voice/reminder": self.voice_reminder,
            "/twilio/voice/gather": self.voice_gather,
            "/twilio/status": self.status_callback,
            "/twilio/sms/inbound": self.sms_inbound,
        }
        handler = routes.get(path)
        if handler is None:
            return 404, "application/json", json.dumps({"error": "not found"})
        if method != "POST":
            return 405, "application/json", json.dumps({"error": "method not allowed"})
        form = self._form(environ)
        if not self._signed(environ, form):
            return 403, "text/plain", "invalid signature"
        query = dict(urllib.parse.parse_qsl(environ.get("QUERY_STRING", "")))
        return 200, "application/xml", handler(form, query)

    # Twilio webhooks

    def voice_reminder(self, form: dict[str, str], query: dict[str, str]) -> str:
        # Re-check DNC at answer time: an opt-out can land between dial and pickup.
        try:
            to = normalize(form.get("To", ""))
        except PhoneError:
            return twiml.say_and_hangup("Goodbye.")
        if self.store.on_dnc(to):
            return twiml.say_and_hangup("Goodbye.")
        when = query.get("when", "your scheduled time")[:80]
        q = urllib.parse.urlencode(
            {"appt": query.get("appt", ""), "purpose": query.get("purpose", "")}
        )
        return twiml.reminder(
            self.s.caller_name, when, f"{self.s.public_base_url}/twilio/voice/gather?{q}"
        )

    def voice_gather(self, form: dict[str, str], query: dict[str, str]) -> str:
        digit = form.get("Digits", "")
        try:
            to = normalize(form.get("To", ""))
        except PhoneError:
            return twiml.say_and_hangup("Goodbye.")
        if digit == "1":
            # A replayed gather must not confirm twice.
            sid = form.get("CallSid", "")
            if not sid or self.store.first_time(f"confirm:{sid}"):
                self.store.audit("confirmed", mask(to), f"appt={query.get('appt', '')}")
            return twiml.say_and_hangup("Thanks, you are confirmed. Goodbye.")
        if digit == "2" and self.s.support_number:
            self.store.audit("transfer", mask(to), "caller asked for a person")
            return twiml.transfer("Connecting you now.", self.s.support_number, self.s.from_number)
        if digit == "2":
            return twiml.say_and_hangup("Please call us back during business hours. Goodbye.")
        if digit == "9":
            self.opt_out(to, "voice_keypress")
            return twiml.say_and_hangup("You will not get these calls again. Goodbye.")
        return twiml.say_and_hangup("Goodbye.")

    def status_callback(self, form: dict[str, str], query: dict[str, str]) -> str:
        sid, status = form.get("CallSid", ""), form.get("CallStatus", "")
        if sid and status:
            outcome = self.store.advance_by_sid(sid, status, form.get("AnsweredBy") or None)
            if outcome != "updated":
                # Replays and out-of-order deliveries land here. Twilio gets a
                # 200 either way so it does not retry.
                log.info("status callback ignored", extra={"outcome": outcome, "status": status})
        return twiml.sms_reply(None)

    def sms_inbound(self, form: dict[str, str], query: dict[str, str]) -> str:
        word = form.get("Body", "").strip().lower()
        try:
            sender = normalize(form.get("From", ""))
        except PhoneError:
            return twiml.sms_reply(None)
        if word in STOP_WORDS:
            self.opt_out(sender, f"sms_{word}")
            # Twilio's Advanced Opt-Out may also reply; keep ours short.
            return twiml.sms_reply(
                f"{self.s.caller_name}: you are opted out of reminder calls and texts."
            )
        if word in START_WORDS:
            # Re-subscribing to texts does not restore call consent. That needs a
            # fresh, recorded opt-in through the normal flow.
            self.store.audit("sms_start", mask(sender), "no consent restored")
            return twiml.sms_reply(None)
        return twiml.sms_reply(None)

    def opt_out(self, phone: str, via: str) -> None:
        revoked = self.store.revoke_all(phone, via)
        self.store.add_dnc(phone, via)
        self.store.audit("opt_out", mask(phone), f"{via}; revoked {revoked} consent record(s)")

    # Internal API

    def api_call(self, environ: dict[str, Any]) -> tuple[int, str, str]:
        auth = environ.get("HTTP_AUTHORIZATION", "")
        token = self.s.api_token
        if not token or not (auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token)):
            return 401, "application/json", json.dumps({"error": "unauthorized"})
        raw = self._body(environ)
        try:
            req = json.loads(raw or b"{}")
            phone, purpose = str(req["phone"]), str(req["purpose"])
        except (ValueError, KeyError, TypeError):
            return 400, "application/json", json.dumps({"error": "need phone and purpose"})
        key = environ.get("HTTP_IDEMPOTENCY_KEY")
        if key is None:
            if self.s.require_idempotency_key:
                return 400, "application/json", json.dumps({"error": "Idempotency-Key required"})
            return self._place(req, phone, purpose)
        if not IDEMPOTENCY_KEY.fullmatch(key):
            return 400, "application/json", json.dumps({"error": "bad Idempotency-Key"})
        # Canonical JSON, so key order and whitespace in the retry do not matter.
        canon = json.dumps(req, sort_keys=True, separators=(",", ":"))
        claim = self.store.claim_idempotency_key(key, hashlib.sha256(canon.encode()).hexdigest())
        if claim.outcome == "replay":
            environ[EXTRA_HEADERS].append(("Idempotent-Replayed", "true"))
            return int(claim.status or 500), "application/json", str(claim.body)
        if claim.outcome == "mismatch":
            msg = "Idempotency-Key was already used with a different request body"
            return 422, "application/json", json.dumps({"error": msg})
        if claim.outcome == "in_flight":
            environ[EXTRA_HEADERS].append(("Retry-After", "1"))
            msg = "a request with this Idempotency-Key is still being processed"
            return 409, "application/json", json.dumps({"error": msg})
        try:
            status, ctype, body = self._place(req, phone, purpose)
        except BaseException:
            # Nothing to replay. The attempt row (if any) still counts against
            # the cap, so a retry with the same key cannot ring twice.
            self.store.release_idempotency_key(claim, key)
            raise
        self.store.complete_idempotency_key(claim, key, status, body)
        return status, ctype, body

    def _place(self, req: dict[str, Any], phone: str, purpose: str) -> tuple[int, str, str]:
        result = self.dialer.remind(
            phone,
            purpose,
            str(req.get("appointment_id", "")),
            str(req.get("when_text", "your scheduled time")),
        )
        body = {
            "placed": result.placed,
            "reasons": list(result.decision.reasons),
            "retry_after": result.decision.retry_after.isoformat()
            if result.decision.retry_after
            else None,
            "call_sid": result.call_sid,
            "error": result.error,
        }
        return (202 if result.placed else 409), "application/json", json.dumps(body)
