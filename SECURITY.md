# Security policy

## Reporting a problem

Please report security problems privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for anything that could let a call go out without consent or after an opt-out.

Include what you ran, what you expected and what happened. Use fake numbers from the 555-01xx range; do not send real phone numbers or call recordings.

You should get an acknowledgement within five working days. Fixes land on `main` with a regression test and a CHANGELOG entry that credits you unless you ask not to be named.

## Supported versions

This is a 0.x reference project. Only the latest commit on `main` gets fixes.

## In scope

- Any path from this package to Twilio's Calls API that skips `Gate.check`, or a way to reuse, alter or keep an `Approval` past its limits.
- Calls placed after an opt-out was recorded, outside the calling window, or past the attempt cap, beyond the documented check-to-dial gap.
- Webhook handling that accepts unsigned requests or lets a replayed callback change recorded state.
- Secrets or full phone numbers reaching logs or the decision log.

## Out of scope

- Code in the same process reading the approval key, or code holding the Twilio auth token calling Twilio directly. The README says the approval is not a boundary against that.
- Whether a given call type is legal where you operate. That is a question for counsel.
