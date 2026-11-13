"""Place one reminder call: gate, dial, record."""

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
            status = "unknown" if exc.ambiguous else "failed"
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
