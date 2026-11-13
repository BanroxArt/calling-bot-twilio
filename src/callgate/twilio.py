"""Minimal Twilio REST client and webhook signature validation.

No SDK dependency. The REST surface used is one endpoint (create a call),
and the signature scheme is short enough to read in full, which matters for
the one piece of code that decides whether a request really came from Twilio.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from callgate.gate import Approval, redeem
from callgate.phone import mask

log = logging.getLogger(__name__)
API = "https://api.twilio.com/2010-04-01"
# Only retry when Twilio clearly did not act on the request. The Calls API
# has no idempotency key, so retrying an ambiguous 500 or a read timeout can
# place the same call twice. A reminder that arrives late beats two calls.
RETRY_STATUS = {429, 503}


# Signature validation -----------------------------------------------------


def compute_signature(auth_token: str, url: str, params: Mapping[str, str | list[str]]) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted key/value pairs))."""
    payload = url
    for key in sorted(params):
        values = params[key]
        for value in sorted(values if isinstance(values, list) else [values]):
            payload += key + value
    mac = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _port_variants(url: str) -> list[str]:
    # Twilio may sign the URL with or without the default port depending on
    # how the webhook was configured. Try both, as the official SDKs do.
    parts = urllib.parse.urlsplit(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    host = parts.hostname or ""
    with_port = parts._replace(netloc=f"{host}:{parts.port or default}")
    without = parts._replace(netloc=host)
    return list(
        dict.fromkeys([url, urllib.parse.urlunsplit(with_port), urllib.parse.urlunsplit(without)])
    )


def validate_signature(
    auth_token: str, url: str, params: Mapping[str, str | list[str]], signature: str | None
) -> bool:
    if not signature or not auth_token:
        return False
    return any(
        hmac.compare_digest(compute_signature(auth_token, u, params), signature)
        for u in _port_variants(url)
    )


# REST client ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


Transport = Callable[[str, str, bytes, Mapping[str, str]], HttpResponse]


def urllib_transport(
    method: str, url: str, body: bytes, headers: Mapping[str, str]
) -> HttpResponse:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read() or b"", dict(exc.headers or {}))


class TwilioError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: int | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        # True when the call may or may not have been created. Do not retry;
        # wait for the status callback or look the call up.
        self.ambiguous = ambiguous


@dataclass(frozen=True, slots=True)
class Backoff:
    attempts: int = 4
    base_s: float = 0.5
    cap_s: float = 8.0

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(self.cap_s, max(0.0, float(retry_after)))
            except ValueError:
                pass
        # Full jitter: spreads retries from many workers instead of syncing them.
        return random.uniform(0, min(self.cap_s, self.base_s * 2**attempt))


class TwilioClient:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        transport: Transport = urllib_transport,
        backoff: Backoff | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = API,
    ) -> None:
        self.account_sid = account_sid
        self._auth = "Basic " + base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self.transport = transport
        self.backoff = backoff or Backoff()
        self.sleep = sleep
        self.base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"TwilioClient(account_sid={self.account_sid[:6]}...)"

    def create_call(
        self,
        approval: Approval,
        *,
        from_number: str,
        twiml_url: str,
        status_callback: str,
        timeout_s: int = 25,
    ) -> dict[str, Any]:
        # Spends the approval: one approval, one call, even if this raises.
        redeem(approval)
        form = {
            "To": approval.phone,
            "From": from_number,
            "Url": twiml_url,
            "Method": "POST",
            "StatusCallback": status_callback,
            "StatusCallbackMethod": "POST",
            "Timeout": str(timeout_s),
            # Answering machines get the same short script; no voicemail drops
            # without a separate decision on whether that is allowed.
            "MachineDetection": "Enable",
        }
        events = ["initiated", "ringing", "answered", "completed"]
        body = urllib.parse.urlencode(
            list(form.items()) + [("StatusCallbackEvent", e) for e in events]
        )
        url = f"{self.base_url}/Accounts/{self.account_sid}/Calls.json"
        return self._post(url, body.encode(), approval)

    def _post(self, url: str, body: bytes, approval: Approval) -> dict[str, Any]:
        headers = {
            "Authorization": self._auth,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "callgate/0.1",
        }
        last: TwilioError | None = None
        for attempt in range(self.backoff.attempts):
            try:
                resp = self.transport("POST", url, body, headers)
            except ConnectionRefusedError:
                # Nothing was sent; safe to retry.
                last = TwilioError("connection refused")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                raise TwilioError(f"network error: {type(exc).__name__}", ambiguous=True) from exc
            else:
                if 200 <= resp.status < 300:
                    data: dict[str, Any] = json.loads(resp.body or b"{}")
                    return data
                err = _error(resp)
                if resp.status not in RETRY_STATUS:
                    if resp.status >= 500:
                        err.ambiguous = True
                    raise err
                last = err
                if attempt + 1 < self.backoff.attempts:
                    self.sleep(self.backoff.delay(attempt, resp.headers.get("Retry-After")))
                continue
            if attempt + 1 < self.backoff.attempts:
                self.sleep(self.backoff.delay(attempt))
        log.warning("twilio create_call gave up", extra={"to": mask(approval.phone)})
        raise last or TwilioError("unknown error")


def _error(resp: HttpResponse) -> TwilioError:
    try:
        data = json.loads(resp.body or b"{}")
    except ValueError:
        data = {}
    return TwilioError(
        str(data.get("message") or f"HTTP {resp.status}"), resp.status, data.get("code")
    )
