# calling-bot-twilio

`callgate` is a small service for appointment-style reminder calls on Twilio where consent is enforced in code. No consent record, no dial; that rule is built into the call graph rather than left to whoever writes the next feature. It also handles opt-outs by keypress and SMS, calling windows in the callee's own timezone, attempt caps, rate limits, Twilio webhook signatures and a log of every gate decision.

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

`Gate.check` runs every rule in one database transaction and, only if all pass, records an attempt and returns an `Approval`. `TwilioClient.create_call` takes an `Approval` as its first argument and refuses anything else. An `Approval` carries an HMAC tag made with a key generated at process start, covers one number, purpose and attempt, is spent by the first `create_call`, and expires after 60 seconds. Building one by hand, editing the number on a real one, dialing twice with one, or keeping one around for later all raise `PermissionError`. So the only way to reach Twilio's Calls API through this codebase is through the gate.

That is a guard against mistakes and shortcuts in your own code, not a security boundary. Code in the same process can read the key, and anything that holds the Twilio auth token can call Twilio without `callgate` at all. Keep the token in the one service that runs the gate.

The gate checks, in order: outbound switched on (`OUTBOUND_ENABLED`, off by default), valid E.164, not on the do-not-call list, active consent for this number and purpose with evidence attached and not older than `CONSENT_MAX_AGE_DAYS`, inside the calling policy (allowed weekday, not a listed holiday, inside that day's window) in the timezone stored on the consent, under the per-number attempt cap and minimum gap, and a token from the global rate limiter. Each decision is logged with its reasons and a keyed hash of the number, not the number itself. With outbound on, the gate refuses to approve anything without an `AUDIT_HASH_KEY`, and `callgate serve` will not start without it.

## Run it

Python 3.11+. The only dependency is `tzdata`.

```sh
python -m pip install -e '.[dev]'
make check                                   # ruff, mypy, content lint, tests; no network

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

`202` means queued with Twilio. `409` comes back with `reasons` (for example `["outside_calling_hours"]`) and, where it applies, `retry_after`. Bodies over 64 KiB get `413`.

With `OUTBOUND_ENABLED=true`, `callgate serve` refuses to start unless `AUDIT_HASH_KEY` and `CALLGATE_API_TOKEN` are each at least 32 characters and `PUBLIC_BASE_URL` is https. Generate them with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.

## Twilio setup

1. Use a test or subaccount first and numbers your team owns.
2. Point the number's voice status callback and the messaging webhook at `PUBLIC_BASE_URL/twilio/status` and `PUBLIC_BASE_URL/twilio/sms/inbound`.
3. `PUBLIC_BASE_URL` must be exactly the https URL Twilio calls. Signatures are computed over that URL; a TLS-terminating proxy that rewrites scheme or host makes every webhook fail validation. Validation tries the URL with and without the default port, as Twilio's SDKs do.
4. Leave Twilio's Advanced Opt-Out on for messaging. `callgate` records the opt-out on its side too, because Twilio's list does not cover voice.

## Design decisions

**Consent is append-only.** Granting adds a row with source, evidence pointer, disclosure version and timezone, and marks older active rows for the same number and purpose as `superseded:<new id>`. A grant without evidence is refused. Revoking stamps `revoked_at` and `revoked_via`. Nothing flips back. The gate uses the newest record that passes every consent check, so a stray incomplete row (from an import, say) cannot hide a valid one. After an opt-out, a new opt-in has to be captured and recorded like the first one. An SMS `START` re-enables texting at the carrier but does not restore call consent here.

**Calling policy, not just a window.** `CALL_WINDOW_START`/`CALL_WINDOW_END` set the default daily window. `CALL_DAYS` limits the weekdays (`mon-fri`, `mon-sat`, ...), `CALL_WINDOW_SAT=10:00-17:00` and its siblings give one weekday its own window, and `CALL_HOLIDAYS` / `CALL_HOLIDAYS_FILE` list dates with no calls. All of it is evaluated in the callee's timezone, holidays included: July 3 in New York starts at midnight New York time. A refusal says which rule fired (`holiday`, `calling_day_not_allowed`, `outside_calling_hours`) and `retry_after` is the first minute the whole policy allows, found by walking forward through days and DST changes. No holiday list ships; [examples/holidays.example.txt](examples/holidays.example.txt) shows the format.

**Timezone comes from the person, not the area code.** Mobile numbers move with people. A missing or invalid timezone on a consent record is a denial, not a guess.

**Opt-out is checked three times, and there is still a gap.** In the gate; again by the dialer right before the REST request; and again when Twilio fetches the script after the callee answers. The middle check shrinks the window between the gate committing and Twilio accepting the call to one HTTP round trip, but it cannot close it. An opt-out that lands after Twilio has accepted the call still lets the phone ring once; the answer-time check then hangs up without delivering the reminder. If that matters for your call type, add a delay between approval and dial and re-check, or accept it in writing with counsel.

**Attempts count only if the phone may have rung.** An attempt is `pending` until Twilio answers the REST call. If Twilio refuses it outright (a 4xx, or `failed` in a status callback) it becomes `not_placed`/`failed` and does not count against `MAX_ATTEMPTS_PER_DAY` or the minimum gap. Ambiguous errors become `unknown` and do count, because the phone might have rung. If recording the attempt fails, the rate-limit token is given back.

**Status only moves forward.** Twilio signs webhooks but includes no nonce or timestamp, so a replayed callback is indistinguishable from a real one. Attempt statuses follow a one-way order (`pending`, `unknown`, `queued`, `ringing`, `in-progress`, then a final state), and a callback that would move an attempt backwards or sideways is ignored with a 200 so Twilio does not retry. `AnsweredBy` has its own column. A replayed keypress confirmation is recorded once per call.

**Retries only when Twilio clearly did not act.** The Calls API has no idempotency key, so a retry after a 500 or a read timeout can ring someone twice. `callgate` retries 429, 503 and refused connections with full-jitter backoff (honouring `Retry-After`), and marks anything ambiguous as `unknown` for the status callback to settle.

**The script says who and why first, and offers the way out every time.** It mentions only the appointment time, because the person who picks up may not be the person who consented. `MachineDetection` is on so answering machines are recorded in the attempt status; there is no voicemail drop.

**No Twilio SDK.** One REST call and one signature scheme are short enough to own. The signature code is pinned to vectors produced by the official `twilio-python` validator in `tests/test_signature.py`.

## Limitations

- `wsgiref` is not a production server. Run `callgate.app.App` under gunicorn or uWSGI behind TLS.
- SQLite with one connection and one lock. Fine for one instance; for more, move the store to Postgres and keep the gate check and the attempt insert in one transaction (`SELECT ... FOR UPDATE` on the number). Schema changes are applied on open and tracked in `PRAGMA user_version`.
- The decision log lives in the same database as everything else and is not tamper evident. Anyone who can write the file can edit it. Ship it to append-only storage if you need it as evidence.
- `callgate consent add` trusts whoever runs it. The evidence string is a pointer you supply; nothing checks it against the original opt-in. Restrict who can run the CLI against the production database.
- `/healthz` is unauthenticated and reports only whether the database answers.
- The rate limiter is in-process. Several instances means several buckets.
- No scheduler. A `409` with `retry_after` is for your job queue to act on.
- No national DNC registry lookup. The internal list only covers people who opted out with you. Whether you need registry scrubbing depends on the call type; ask counsel.
- English script only, one voice, no speech recognition or LLM. Adding one is in `SKILL.md`, behind the same gate.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Banrox Inc.

Pull requests that weaken the consent gate, add caller ID spoofing, or add list dialing will be closed.
