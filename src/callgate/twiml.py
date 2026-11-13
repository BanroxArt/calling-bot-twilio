"""TwiML builders. Every string goes through ElementTree, so nothing a caller
or a database row contains can inject markup into the call script."""

from __future__ import annotations

import xml.etree.ElementTree as ET

VOICE = {"voice": "Polly.Joanna", "language": "en-US"}


def _doc(root: ET.Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def _say(parent: ET.Element, text: str) -> None:
    ET.SubElement(parent, "Say", VOICE).text = text


def reminder(caller_name: str, when_text: str, gather_url: str) -> str:
    """The whole outbound script.

    Who is calling and why come first, the opt-out is offered every time,
    and nothing account-specific is said beyond the appointment time, because
    we do not know who picked up.
    """
    r = ET.Element("Response")
    _say(
        r,
        f"Hello, this is an automated reminder from {caller_name} about an appointment "
        f"you asked us to remind you about, on {when_text}.",
    )
    g = ET.SubElement(
        r,
        "Gather",
        {"input": "dtmf", "numDigits": "1", "timeout": "6", "action": gather_url, "method": "POST"},
    )
    _say(g, "Press 1 to confirm. Press 2 to talk to a person. Press 9 to stop these calls.")
    _say(r, "We did not get a choice. Goodbye.")
    ET.SubElement(r, "Hangup")
    return _doc(r)


def say_and_hangup(text: str) -> str:
    r = ET.Element("Response")
    _say(r, text)
    ET.SubElement(r, "Hangup")
    return _doc(r)


def transfer(text: str, number: str, caller_id: str) -> str:
    r = ET.Element("Response")
    _say(r, text)
    ET.SubElement(r, "Dial", {"callerId": caller_id, "timeout": "20"}).text = number
    return _doc(r)


def sms_reply(text: str | None) -> str:
    r = ET.Element("Response")
    if text:
        ET.SubElement(r, "Message").text = text
    return _doc(r)
