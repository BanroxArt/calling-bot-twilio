"""E.164 phone number handling.

Deliberately strict. Numbers are stored and compared in E.164 only, so the
consent record, the do-not-call list and the dial request can never disagree
about formatting. Parsing national formats belongs at the edge (signup
form), not in the gate.
"""

from __future__ import annotations

import hashlib
import hmac
import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_STRIP = re.compile(r"[\s().-]")


class PhoneError(ValueError):
    pass


def normalize(raw: str, default_country: str | None = "1") -> str:
    """Return E.164. Accepts separators; a bare 10 digit number gets ``+1`` only if asked."""
    if not isinstance(raw, str):
        raise PhoneError("phone must be a string")
    s = _STRIP.sub("", raw.strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if default_country == "1" and len(s) == 10 and s.isdigit():
            s = "+1" + s
        elif default_country == "1" and len(s) == 11 and s.startswith("1") and s.isdigit():
            s = "+" + s
        else:
            raise PhoneError("phone must be E.164 (+ and country code)")
    if not E164.match(s):
        raise PhoneError("not a valid E.164 number")
    return s


def mask(e164: str) -> str:
    """For logs: country code and last two digits only."""
    if len(e164) < 6:
        return "***"
    return f"{e164[:2]}***{e164[-2:]}"


def digest(e164: str, key: bytes) -> str:
    """Keyed hash, for audit rows that must be joinable without holding the number."""
    return hmac.new(key, e164.encode(), hashlib.sha256).hexdigest()[:24]
