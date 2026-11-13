# Example: consent-gated appointment reminder (fictional)

Example Co, a fictional service business, calls customers who asked for a reminder the day before a booked appointment. Not legal advice; the rules for your call type are a question for counsel.

## Before any call is placed

| Requirement | Where callgate enforces it |
| --- | --- |
| The person opted in to reminder calls at this number | `consent` row for `appointment_reminder`, checked in `Gate._check` |
| The opt-in records when, where, what text they saw | `source`, `evidence`, `disclosure_version`, `granted_at` |
| Opt-out works on the call and by text | keypress 9 in `/twilio/voice/gather`, STOP words in `/twilio/sms/inbound` |
| Calls only in the callee's daytime | `hours.py`, timezone stored on the consent record |
| Not called over and over | `MAX_ATTEMPTS_PER_DAY`, `MIN_GAP_MINUTES` |
| Who is calling is said first | first sentence of the script in `twiml.py` |

## Script

"Hello, this is an automated reminder from Example Co about an appointment you asked us to remind you about, on Tuesday at 3 PM. Press 1 to confirm. Press 2 to talk to a person. Press 9 to stop these calls."

Press 1 records a confirmation and hangs up. Press 2 transfers to `SUPPORT_NUMBER` if one is set, otherwise asks them to call back. Press 9 revokes every consent for the number, adds it to the do-not-call list and confirms on the same call. No input says goodbye and hangs up.

## Out of scope for this example

- Payment, debt or account-balance reminders. Those carry extra rules; involve counsel first.
- Dialing a purchased or scraped list.
- Changing or hiding the caller ID.
- Saying anything about the account beyond the appointment time to whoever answers.
