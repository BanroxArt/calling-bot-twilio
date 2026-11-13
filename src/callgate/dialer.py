"""Place one reminder call: gate, dial, record.

There is a gap between ``Gate.check`` committing and Twilio accepting the
call. The dialer re-reads the do-not-call list and consent right before the
REST request, which shrinks the gap to one HTTP round trip but cannot close
it: an opt-out that lands after Twilio accepts the call still lets the phone
ring. The answer-time check in ``App.voice_reminder`` then hangs up with no
message. Treat that as the expected worst case, not a bug.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

from callgate.config import Settings
from callgate.gate import Decision, Gate
from callgate.phone import mask
from callgate.store import Store
from callgate.twilio import TwilioClient, TwilioError

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DialResult:
    placed: bool
    decision: Decision
    call_sid: str | None = None
    error: str | None = None


class Dialer:
    def __init__(self, settings: Settings, store: Store, gate: Gate, client: TwilioClient) -> None:
        self.s = settings
        self.store = store
        self.gate = gate
        self.client = client

    def remind(
        self,
        phone: str,
        purpose: str,
        appointment_id: str,
        when_text: str,
        now: datetime | None = None,
    ) -> DialResult:
        decision = self.gate.check(phone, purpose, now)
        if not decision.allowed or decision.approval is None:
            return DialResult(False, decision)
        approval = decision.approval
        if self.store.on_dnc(approval.phone) or not any(
            c.id == approval.consent_id
            for c in self.store.active_consents(approval.phone, approval.purpose)
        ):
            self.store.update_attempt(approval.attempt_id, status="not_placed")
            self.store.audit(
                "dial_cancelled",
                self.gate.phone_ref(approval.phone),
                f"attempt={approval.attempt_id}: opted out between check and dial",
            )
            return DialResult(False, decision, error="opted out before dial")
        q = urllib.parse.urlencode({"appt": appointment_id, "when": when_text, "purpose": purpose})
        base = self.s.public_base_url
        try:
            call = self.client.create_call(
                approval,
                from_number=self.s.from_number,
                twiml_url=f"{base}/twilio/voice/reminder?{q}",
                status_callback=f"{base}/twilio/status",
            )
        except TwilioError as exc:
            # Ambiguous: Twilio may have placed it, so it counts against the
            # daily cap. Otherwise Twilio refused and the phone never rang.
            status = "unknown" if exc.ambiguous else "not_placed"
            self.store.update_attempt(approval.attempt_id, status=status)
            log.warning(
                "dial failed",
                extra={"to": mask(approval.phone), "status": status, "code": exc.code},
            )
            return DialResult(False, decision, error=str(exc))
        sid = str(call.get("sid", ""))
        self.store.update_attempt(
            approval.attempt_id, call_sid=sid or None, status=str(call.get("status", "queued"))
        )
        log.info("call queued", extra={"to": mask(approval.phone), "sid": sid})
        return DialResult(True, decision, call_sid=sid)
