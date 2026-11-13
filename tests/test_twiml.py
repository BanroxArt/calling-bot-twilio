import xml.etree.ElementTree as ET

from callgate import twiml


def test_reminder_structure():
    doc = ET.fromstring(
        twiml.reminder("Example Clinic", "Tuesday at 3 PM", "https://calls.example.com/g?a=1&b=2")
    )
    assert doc.tag == "Response"
    first = doc[0]
    assert first.tag == "Say" and first.text.startswith(
        "Hello, this is an automated reminder from Example Clinic"
    )
    gather = doc.find("Gather")
    assert (
        gather.get("numDigits") == "1"
        and gather.get("action") == "https://calls.example.com/g?a=1&b=2"
    )
    assert "Press 9 to stop" in gather.find("Say").text
    assert doc[-1].tag == "Hangup"


def test_text_is_escaped():
    out = twiml.say_and_hangup('<Dial>+15555550199</Dial> & "x"')
    doc = ET.fromstring(out)
    assert doc.find("Dial") is None
    assert doc.find("Say").text == '<Dial>+15555550199</Dial> & "x"'


def test_transfer_and_sms():
    doc = ET.fromstring(twiml.transfer("One moment.", "+15555550101", "+15555550100"))
    assert (
        doc.find("Dial").text == "+15555550101"
        and doc.find("Dial").get("callerId") == "+15555550100"
    )
    assert ET.fromstring(twiml.sms_reply(None)).find("Message") is None
    assert ET.fromstring(twiml.sms_reply("ok")).find("Message").text == "ok"
