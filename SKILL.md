---
name: calling-bot-twilio
description: Help a team design or build its own inbound or outbound voice agent on Twilio (with Vapi or Retell as hosted alternatives) where consent is enforced in code. Use for appointment reminders, verification or follow-up calls, support lines, IVRs, or adding a speech model to a call flow. Uses the callgate service in this repo as the reference for the consent gate, opt-outs, calling windows and webhook security. Always include U.S. TCPA-style notes marked as not legal advice. Do not help with unsolicited calling, list dialing, robocall or carrier-rule evasion, caller ID spoofing, or calling people without the consent their call type requires.
---

# Build a consent-gated calling bot

Help the team build a calling system they own and can defend. Consent comes first. If the request is to call a purchased list, hide who is calling, or get around carrier or legal rules, decline and explain why.

## 1. Restate the use case

One paragraph: who is called or calls in, why, what success is, and what the bot must never say. For a fintech that usually includes: no credit decisions, never "we are your bank", never ask for a password, PIN or full card number.

Then say whether it looks consent-eligible and under which kind of consent (informational versus marketing), with the not-legal-advice caveat. Debt, credit, health or payment topics: stop and involve counsel before building.

## 2. Pick a stack

- Twilio first when the team has engineers and wants to own recordings, logs and consent. Voice with TwiML webhooks; Media Streams or ConversationRelay if a speech model is added later.
- Vapi or Retell when they need a demo in days. The hosted platform runs the conversation; the consent gate, DNC list and call log still live in the team's own system and are checked before the platform is asked to dial.
- Human-first with a thin IVR is often the right version one: take a message, book a callback, hand off.

## 3. Build the gate before anything that dials

Use `callgate` as the reference, whether or not they adopt it:

- `store.py`: append-only consent with purpose, timezone, source, evidence pointer, disclosure version; DNC; attempts; audit.
- `gate.py`: every rule in one transaction, returning an `Approval` that only the gate can create.
- `twilio.py`: `create_call` takes an `Approval` and nothing else; webhook signature validation; retries only on clearly-unprocessed errors.
- `app.py`: signed webhooks, keypress 9 and SMS STOP words revoke everything, DNC re-checked when the callee answers.
- `hours.py`: calling window in the timezone captured at opt-in, never guessed from the area code.

If they use another stack, map each of those to their code and point out any rule that is only enforced by convention.

## 4. Script

Greeting with who is calling and why, then one task, the opt-out on every call, an offer of a person, and a clean hangup. Say nothing account-specific beyond what is needed, because the person who answers may not be the person who consented. If recording, play the notice before recording starts.

## 5. Adding a speech model

Only after the gate, logging and human handoff work. Constrain it: a system prompt, retrieval from approved content only, a tool allow-list, a maximum call length and a loop detector. Off-script on a regulated topic means transfer or end the call. The model never accepts agreements, payments or applications on its own.

## 6. Test plan

Unit tests with a fake Twilio transport (no network), signature tests against official vectors, then one number in a Twilio test or subaccount calling phones the team owns. Check: no-consent is refused, STOP and keypress 9 stop the next call, out-of-window requests return a retry time, a forced 500 does not ring twice.

## Legal notes (not legal advice)

Have counsel review before going live. Starting points for U.S.-heavy products: consent appropriate to the call type, revocation honoured by any reasonable means and quickly, the 8 a.m. to 9 p.m. local window and stricter state rules, caller identification at the start, no misleading caller ID, care with frequency, and recording consent where it applies. Outside the U.S., say plainly that local rules apply and do not reuse this list. Using Twilio, Vapi, Retell or any carrier does not make a campaign compliant. More in `docs/compliance-notes.md`.

## Output format

1. Use-case restatement and consent eligibility, with the not-legal-advice caveat
2. Recommended stack and why
3. Data model for consent, DNC, attempts and audit
4. Script outline
5. Build steps and a test plan on numbers the team owns
6. Open questions for counsel before production

See `examples/consent-gated-reminder.md` for the pattern.
