# Contributing

Bug reports and patches are welcome. For anything larger than a small fix, open an issue first so we can agree on the shape before you write it.

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]'
make check
```

`make check` runs ruff (lint and format), mypy in strict mode over `src/`, the content lint in `scripts/check.py`, and pytest including the Hypothesis property tests. Nothing touches the network; Twilio is faked in `tests/conftest.py`. CI runs the same steps on Python 3.11 to 3.13.

## What a good patch looks like

- One change per pull request, with a test that fails before it and passes after.
- New gate rules go in `Gate._check`, return a reason string, and get a test for both the allow and the deny path.
- New attempt states need a rank in `store.STATUS_RANK`. Schema changes go at the end of `store.MIGRATIONS`; never edit an existing entry.
- Update `CHANGELOG.md` under "Unreleased".

## Things we will not merge

- Anything that weakens the consent gate, adds list dialing, or hides or spoofs caller ID.
- Real phone numbers, names or recordings in tests or examples. Use the NANP 555-01xx range and RFC 2606 domains. `scripts/check.py` enforces this.

## Commit messages

Short imperative subject in lower case (`gate: refund the token when the insert fails`), a blank line, then why.
