# calling-bot-twilio

`callgate` is a small service for appointment-style reminder calls on Twilio where consent is enforced in code. No consent record, no dial; that rule is built into the call graph rather than left to whoever writes the next feature. It also handles opt-outs by keypress and SMS, calling windows in the callee's own timezone, attempt caps, rate limits, Twilio webhook signatures and an audit trail.

It is not a dialer for lists, not a sales tool, and has no feature for hiding or spoofing caller ID. The repo also has `SKILL.md`, an agent skill for designing a calling bot (Twilio first, Vapi or Retell as alternatives) around the same rules.

Author: Art Akopian ([Banrox](https://banrox.com))

Nothing here is legal advice. See [docs/compliance-notes.md](docs/compliance-notes.md) and talk to counsel before calling anyone.

## How a call happens

```text
your app --POST /calls--> Gate.check --Approval--> TwilioClient.create_call --> Twilio
                             |                                                   |
                       deny + reasons                       POST /twilio/voice/reminder (signed)
                       (audited)                            POST /twilio/voice/gather   (signed)
                                                            POST /twilio/status         (signed)
```

`Gate.check` runs every rule in one database transaction and, only if all pass, records an attempt and returns an `Approval`. `TwilioClient.create_call` takes an `Approval` as its first argument and refuses anything else. `Approval` cannot be constructed outside `gate.py`. So the only way to reach Twilio's Calls API from this codebase is through the gate.

The gate checks, in order: outbound switched on (`OUTBOUND_ENABLED`, off by default), valid E.164, not on the do-not-call list, active consent for this number and purpose with evidence attached and not older than `CONSENT_MAX_AGE_DAYS`, inside the calling window in the timezone stored on the consent, under the per-number attempt cap and minimum gap, and a token from the global rate limiter. Each decision is audited with its reasons and a keyed hash of the number, not the number itself.

## Run it

Python 3.11+. The only dependency is `tzdata`.

```sh
python -m pip install -e '.[dev]'
make check                                   # ruff, content lint, 72 tests, no network

cp .env.example .env                         # fill in; never commit .env
callgate --db dev.db consent add +15555550142 --purpose appointment_reminder \
  --timezone America/Chicago --source web_form --evidence form:8841 --disclosure-version v3
callgate --db dev.db check +15555550142 --purpose appointment_reminder
callgate serve                               # wsgiref, development only
```

Request a call from your backend:

```sh
curl -X POST https://calls.example.com/calls \
  -H "Authorization: Bearer $CALLGATE_API_TOKEN" \
  -d '{"phone":"+15555550142","purpose":"appointment_reminder","appointment_id":"a-19","when_text":"Tuesday at 3 PM"}'
```

`202` means queued with Twilio. `409` comes back with `reasons` (for example `["outside_calling_hours"]`) and, where it applies, `retry_after`.

## Twilio setup

1. Use a test or subaccount first and numbers your team owns.
2. Point the number's voice status callback and the messaging webhook at `PUBLIC_BASE_URL/twilio/status` and `PUBLIC_BASE_URL/twilio/sms/inbound`.
3. `PUBLIC_BASE_URL` must be exactly the https URL Twilio calls. Signatures are computed over that URL; a TLS-terminating proxy that rewrites scheme or host makes every webhook fail validation. Validation tries the URL with and without the default port, as Twilio's SDKs do.
4. Leave Twilio's Advanced Opt-Out on for messaging. `callgate` records the opt-out on its side too, because Twilio's list does not cover voice.

## Design decisions

**Consent is append-only.** Granting adds a row with source, evidence pointer, disclosure version and timezone. Revoking stamps `revoked_at` and `revoked_via`. Nothing flips back. After an opt-out, a new opt-in has to be captured and recorded like the first one. An SMS `START` re-enables texting at the carrier but does not restore call consent here.

**Timezone comes from the person, not the area code.** Mobile numbers move with people. A missing or invalid timezone on a consent record is a denial, not a guess.

**Opt-out is checked twice.** Once in the gate, and again when Twilio fetches the script after the callee answers, because a STOP can land between dial and pickup.

**Retries only when Twilio clearly did not act.** The Calls API has no idempotency key, so a retry after a 500 or a read timeout can ring someone twice. `callgate` retries 429, 503 and refused connections with full-jitter backoff (honouring `Retry-After`), and marks anything ambiguous as `unknown` for the status callback to settle.

**The script says who and why first, and offers the way out every time.** It mentions only the appointment time, because the person who picks up may not be the person who consented. `MachineDetection` is on so answering machines are recorded in the attempt status; there is no voicemail drop.

**No Twilio SDK.** One REST call and one signature scheme are short enough to own. The signature code is pinned to vectors produced by the official `twilio-python` validator in `tests/test_signature.py`.

## Limitations

- `wsgiref` is not a production server. Run `callgate.app.App` under gunicorn or uWSGI behind TLS.
- SQLite with one connection. Fine for one instance; for more, move the store to Postgres and keep the gate check and the attempt insert in one transaction (`SELECT ... FOR UPDATE` on the number).
- The rate limiter is in-process. Several instances means several buckets.
- No scheduler. A `409` with `retry_after` is for your job queue to act on.
- No national DNC registry lookup. The internal list only covers people who opted out with you. Whether you need registry scrubbing depends on the call type; ask counsel.
- English script only, one voice, no speech recognition or LLM. Adding one is in `SKILL.md`, behind the same gate.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Banrox Inc.

Pull requests that weaken the consent gate, add caller ID spoofing, or add list dialing will be closed.
