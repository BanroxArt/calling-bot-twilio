# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/) once it reaches 1.0.

## [Unreleased]

### Security

- `Approval` used a module-level sentinel that any code could import to build one. Approvals are now HMAC-tagged with a per-process key, single use, and expire after 60 seconds. The README now says plainly that this guards against mistakes, not against code in the same process.
- Status callbacks could move an attempt backwards (a replayed `ringing` after `completed`). Attempt statuses now follow a one-way order and stale callbacks are ignored. A replayed keypress confirmation is recorded once.
- With outbound calling on, a blank `AUDIT_HASH_KEY` silently degraded the decision log to masked numbers. `callgate serve` now refuses to start without a 32-character audit key and API token, and the gate denies with `no_audit_key`.
- `/healthz` no longer reports whether outbound calling is enabled.

### Fixed

- A call Twilio refused outright still counted against the person's daily cap. Attempts start as `pending`; `not_placed` and `failed` attempts do not count, `unknown` ones still do.
- The rate-limit token was lost if recording the attempt failed. It is now given back.
- Consent lookup looked only at the newest unrevoked row, so an evidence-less newer row hid valid consent. The gate now uses the newest row that passes every check, `add_consent` refuses blank evidence, and a new grant supersedes older active rows.
- The dialer re-checks opt-out and consent right before dialing and cancels with `not_placed` if either changed.
- `next_window_start` could return a time outside the window when the window start fell in a DST gap.
- Oversized request bodies were truncated and then failed with a misleading 400 or 403. They now get 413.
- `at=0.0` was treated as "now" in the store.
- `AnsweredBy` is stored in its own column instead of being appended to the status.
- `callgate consent revoke` printed a traceback for an invalid number.
- Numeric settings and `OUTBOUND_ENABLED` are validated at startup.

### Added

- Calling policy: `CALL_DAYS` (allowed weekdays), `CALL_WINDOW_<DAY>` per-weekday windows and `CALL_HOLIDAYS` / `CALL_HOLIDAYS_FILE` (local dates in the callee's timezone). Denials name the rule (`holiday`, `calling_day_not_allowed`, `outside_calling_hours`) and `retry_after` is the first minute the full policy allows. `hours.CallingPolicy` is usable on its own.
- Schema migrations tracked in `PRAGMA user_version`.
- mypy (strict) in CI, a `py.typed` marker, and Hypothesis property tests for calling windows across DST, phone normalisation, the token bucket and the status order.
- SECURITY.md, CONTRIBUTING.md and this file.

## [0.1.0] - 2026-11-12

First public version: consent store, outbound gate, Twilio REST client with conservative retries, webhook signature validation, keypress and SMS opt-out, calling windows in the callee's timezone, CLI.
