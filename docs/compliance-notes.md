# Compliance notes

Not legal advice. Rules differ by country, state, call purpose and technology, and they change. This is a list of what the code takes into account and what it leaves to you and your counsel, written for U.S.-heavy products. Outside the U.S., local telecom, privacy and marketing rules apply and this list does not.

## What the TCPA and FCC rules touch here

- Consent before calling. Calls to mobile numbers using an artificial or prerecorded voice generally need the called party's prior express consent; marketing calls of that kind need prior express written consent. Informational calls such as appointment reminders sit under a different standard than marketing, but they still need consent. That is why every consent record is per purpose and carries a pointer to its evidence and the version of the disclosure the person agreed to.
- Revocation. People can revoke consent by any reasonable means, and the FCC's revocation rules require honouring it quickly. `callgate` treats keypress 9 and the standard SMS keywords (STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, OPTOUT, REVOKE) as revocation of every purpose for that number, effective immediately. If people can also revoke by email, support chat or in your app, wire those into `Store.revoke_all`.
- Time of day. The federal limit for telephone solicitations is between 8 a.m. and 9 p.m. at the called party's location. The default window matches that. Several states are stricter (narrower hours, no Sundays or holidays); set `CALL_WINDOW_START` and `CALL_WINDOW_END` from counsel's advice, restrict weekdays with `CALL_DAYS`, give a weekday its own window with `CALL_WINDOW_SAT` (and the other day names), and list no-call dates in `CALL_HOLIDAYS_FILE`. One policy applies to every callee. If you call into states with different rules, set it to the strictest of them; per-state policies are not built in.
- Identification. Prerecorded and artificial voice calls must identify the caller at the start. The script does that in its first sentence. Caller ID must not be spoofed to mislead; `TWILIO_FROM_NUMBER` should be a number you own and that calls back to you.
- Frequency. Repeated calls after a refusal are where otherwise valid programs get into trouble. Defaults are two attempts per 24 hours with a two hour gap.
- Recording. This code does not record calls. If you add recording, many states require all-party consent, and the notice has to be played before recording starts.

## Higher-risk call types

Debt collection, credit, healthcare and anything involving payment details carry additional rules (FDCPA and Regulation F for collections, HIPAA for health information). Do not adapt this code for them without counsel. The script deliberately says nothing beyond an appointment time.

## Vendors do not move the risk

Twilio, Vapi, Retell or any carrier being involved does not make a non-compliant program compliant. Twilio's own acceptable use policy also applies to your account.

## What to keep

- Consent records with evidence, for as long as counsel says (often the statute of limitations, which for the TCPA is four years).
- The audit table: every gate decision with reasons, every opt-out with channel, and any dial cancelled because an opt-out arrived between check and dial. It is a record you keep, not tamper-evident evidence; see the README limitations.
- Attempts with Twilio call SIDs, final status and answering-machine result.

## The check-to-dial gap

An opt-out that arrives after Twilio has accepted a call cannot stop that call from ringing. `callgate` re-checks right before dialing and again at answer time, so the person hears no reminder, but the phone may ring once. Decide with counsel whether that is acceptable for your call type, and say so in your written procedures.
