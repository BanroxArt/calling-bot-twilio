import json

import pytest

from callgate.cli import main
from callgate.config import ConfigError, load

GOOD = {
    "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
    "TWILIO_AUTH_TOKEN": "x" * 32,
    "TWILIO_FROM_NUMBER": "+15555550100",
    "PUBLIC_BASE_URL": "https://calls.example.com/",
}


def test_load_good():
    s = load(GOOD)
    assert s.public_base_url == "https://calls.example.com"
    assert not s.outbound_enabled  # off unless explicitly turned on
    assert "x" * 32 not in repr(s)


@pytest.mark.parametrize(
    "patch",
    [
        {"TWILIO_AUTH_TOKEN": ""},
        {"TWILIO_ACCOUNT_SID": "nope"},
        {"TWILIO_FROM_NUMBER": "555-0100"},
        {"PUBLIC_BASE_URL": "http://calls.example.com"},
        {"CALL_WINDOW_START": "8am"},
        {"CALL_WINDOW_START": "22:00", "CALL_WINDOW_END": "07:00"},
    ],
)
def test_load_rejects(patch):
    with pytest.raises(ConfigError):
        load({**GOOD, **patch})


def test_cli_consent_and_check(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "c.db")
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    assert main(["--db", db, "check", "+15555550142", "--purpose", "appointment_reminder"]) == 1
    assert json.loads(capsys.readouterr().out)["reasons"] == ["no_consent"]

    rc = main(
        [
            "--db",
            db,
            "consent",
            "add",
            "+1 555 555 0142",
            "--purpose",
            "appointment_reminder",
            "--timezone",
            "UTC",
            "--source",
            "web_form",
            "--evidence",
            "form:77",
            "--disclosure-version",
            "v2",
        ]
    )
    assert rc == 0
    capsys.readouterr()
    rc = main(["--db", db, "check", "+15555550142", "--purpose", "appointment_reminder"])
    out = json.loads(capsys.readouterr().out)
    assert out["reasons"] in (["ok"], ["outside_calling_hours"])  # depends on wall clock
    assert rc == (0 if out["reasons"] == ["ok"] else 1)

    assert main(["--db", db, "consent", "revoke", "+15555550142"]) == 0
    assert json.loads(capsys.readouterr().out) == {"revoked": 1, "dnc": True}
    main(["--db", db, "check", "+15555550142", "--purpose", "appointment_reminder"])
    assert json.loads(capsys.readouterr().out)["reasons"] == ["do_not_call"]


def test_cli_rejects_bad_consent(tmp_path, capsys):
    db = str(tmp_path / "c.db")
    base = [
        "--db",
        db,
        "consent",
        "add",
        "+15555550142",
        "--purpose",
        "p",
        "--source",
        "s",
        "--disclosure-version",
        "v1",
    ]
    assert main([*base, "--timezone", "Mars/Base", "--evidence", "x"]) == 2
    assert main([*base, "--timezone", "UTC", "--evidence", "  "]) == 2
