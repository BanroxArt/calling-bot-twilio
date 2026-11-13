"""Webhook and API endpoints, as a WSGI app.

Twilio-facing routes (``/twilio/...``) require a valid X-Twilio-Signature.
The one internal route, ``POST /calls``, requires a bearer token. Health is
open. Everything else is 404.
"""

from __future__ import annotations

import hmac
import json
import logging
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


class App:
    def __init__(self, settings: Settings, store: Store, dialer: Dialer) -> None:
        self.s = settings
        self.store = store
        self.dialer = dialer

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        try:
            status, ctype, body = self.dispatch(method, path, environ)
        except Exception:
            log.exception("unhandled error", extra={"path": path})
            status, ctype, body = 500, "application/json", json.dumps({"error": "internal error"})
        data = body.encode()
        start_response(
            f"{status} {'OK' if status < 400 else 'Error'}",
            [("Content-Type", ctype), ("Content-Length", str(len(data)))],
        )
        return [data]

    # plumbing

    @staticmethod
    def _form(environ: dict[str, Any]) -> dict[str, str]:
        try:
            length = min(int(environ.get("CONTENT_LENGTH") or 0), MAX_BODY)
        except ValueError:
            length = 0
        raw = environ["wsgi.input"].read(length).decode("utf-8", errors="replace") if length else ""
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def _public_url(self, environ: dict[str, Any]) -> str:
        # Validate against the URL Twilio was configured with, not the Host
        # header, which a proxy may have rewritten.
        qs = environ.get("QUERY_STRING", "")
        return self.s.public_base_url + environ.get("PATH_INFO", "/") + (f"?{qs}" if qs else "")

    def _signed(self, environ: dict[str, Any], form: dict[str, str]) -> bool:
        sig = environ.get("HTTP_X_TWILIO_SIGNATURE")
        ok = validate_signature(self.s.auth_token, self._public_url(environ), form, sig)
        if not ok:
            log.warning("bad twilio signature", extra={"path": environ.get("PATH_INFO")})
        return ok

    def dispatch(self, method: str, path: str, environ: dict[str, Any]) -> tuple[int, str, str]:
        if path == "/healthz" and method == "GET":
            return (
                200,
                "application/json",
                json.dumps({"status": "ok", "outbound": self.s.outbound_enabled}),
            )
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
            answered_by = form.get("AnsweredBy", "")
            self.store.update_attempt_by_sid(
                sid, status if not answered_by else f"{status}:{answered_by}"
            )
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
        try:
            length = min(int(environ.get("CONTENT_LENGTH") or 0), MAX_BODY)
            req = json.loads(environ["wsgi.input"].read(length) or b"{}")
            phone, purpose = str(req["phone"]), str(req["purpose"])
        except (ValueError, KeyError, TypeError):
            return 400, "application/json", json.dumps({"error": "need phone and purpose"})
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
