---
name: calling-bot-twilio
description: Help a team design or build their own inbound or outbound voice agent using Twilio, with Vapi or Retell as alternatives. Use when the user wants an AI or scripted calling bot, appointment reminders, support lines, verification calls, or an IVR. Always include consent and U.S. TCPA-style notes. Do not help with unsolicited spam, robocall evasion, spoofing caller ID to deceive, or calling people who have not consented where consent is required.
---

# Build your own calling bot (Twilio, with alternatives)

Help the team build a calling system they own. Consent comes first. If the request is to blast a purchased list, hide the caller, or evade carrier or legal rules, refuse.

## Scope

- Inbound: a phone number that answers, routes, or talks with a script or a speech model, then hands off to a human.
- Outbound: reminders, verification, or follow-up **to people who have a documented, appropriate relationship and consent** for that kind of call.
- Voicemail drops, SMS follow-ups, and call recording are in scope only with the right notices and consent.

## Legal and consent notes (not legal advice)

Laws change and differ by country, state, and call purpose. Tell the user to have counsel review their use case before going live. The points below are a starting checklist for U.S.-heavy products, not a complete TCPA manual.

- **Get a lawful basis before you dial or text.** For many marketing or promotional calls and texts to mobile numbers in the United States, prior express written consent is required. Transactional or informational calls can have a different (still real) standard. Do not assume “we have their number in the CRM” is enough.
- **Honor official and internal do-not-call lists.** Maintain a company-specific DNC list and process opt-outs immediately. Recurring programs need a way for someone to revoke consent.
- **Identify the caller.** Say who is calling and, when required, why, at the start of the call. Do not spoof caller ID to impersonate a person, a bank, or a government agency.
- **Recording and automated voices.** Many U.S. states require one-party or all-party consent to record. Automated or prerecorded voices have extra rules. Play or speak a recording notice when you record.
- **Time-of-day and frequency.** Calling at unreasonable hours, or repeatedly after a refusal, is how programs get into trouble even when the first consent was valid.
- **Vendors do not take the legal risk off the table.** Using Twilio, Vapi, Retell, or a carrier does not make a non-compliant campaign compliant.
- **Financial, debt, and healthcare calls** have additional rules. If the bot will discuss debt, credit, or health, stop and involve counsel before building.

If the user is outside the U.S., say clearly that local telecom, privacy, and marketing-call rules apply, and do not copy the U.S. checklist as if it were universal.

## Architecture choices

### Twilio-first (good default when the team wants to own the rails)

Typical pieces:

- A Twilio number or SIP trunk
- Twilio Voice for the call leg (TwiML, Media Streams, or the Voice SDK)
- Your backend (the system of record for consent, user, and conversation state)
- Optional: a speech-to-text / LLM / text-to-speech loop, or a simpler button-and-prompt IVR
- Optional: Twilio SMS or email for confirmations, never as a silent substitute for consent

Recommend this path when the team has engineers, cares about owning recordings and logs, and already uses Twilio or wants one vendor for SMS and voice.

### Alternatives (mention, do not oversell)

- **Vapi** and **Retell** (and similar voice-agent platforms): faster to a demo, hosted conversation stack, still need your consent layer and a compliant caller identity. Good for a prototype; you still own the policy.
- **In-house SIP + open-source STT/TTS**: more control, more operational work. Only suggest this if the team already has telephony experience.
- **Human-first with a thin IVR**: often the right first version. A bot that takes a message and books a callback beats a chatty agent with no handoff.

Always keep **consent, DNC, recording flags, and conversation logs in a system the team owns**, even if the voice model is hosted.

## Build sequence

1. Write the use case in one paragraph: who is called or who calls in, why, what success is, and what the bot must never say (for a fintech: no credit decision, no “we are your bank,” no request for a password or a full card number).
2. Implement the consent check as a hard gate. No consent record, no outbound dial.
3. Stand up one number in a non-production Twilio (or alternative) account. Use test numbers the team owns.
4. Build a short script: greeting, identity, purpose, consent or recording notice, one task, offer a human, clean hangup.
5. Add logging: call SID, timestamps, consent version, recording pointer, transcript if any, and the outcome. Redact secrets and payment data from transcripts.
6. Add a live handoff to a person, or a documented voicemail path, before any public launch.
7. Only then add an LLM. Constrain it with a system prompt, retrieval from approved content, and a tool allow-list. If it is off-script on a regulated topic, it must transfer or end the call.

## Safety and product quality

- Set a maximum call length and a loop detector (bots that repeat themselves should transfer or hang up).
- Do not let the model accept a legal agreement, a payment, or a credit application on its own unless counsel has approved that path.
- Load-test and set rate limits so a bug cannot autodial a list.
- Keep a kill switch (disable outbound in one flag).
- Review a sample of recordings every week at the start.

## Output format

1. Use-case restatement and whether it looks consent-eligible (with the “not legal advice” caveat)
2. Recommended stack (Twilio, Vapi/Retell, or human-first) and why
3. Data model for consent, DNC, and call records
4. Script outline
5. Build steps and a test plan on numbers the team owns
6. Open legal questions to take to counsel before production
