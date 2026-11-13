# Example: consent-gated payment reminder (fictional)

Do not use this to place calls without consent. U.S. calling and TCPA rules are legal questions for counsel. This file is a product pattern, not legal advice.

## Required before any call is placed

1. The person opted in, in writing, to this kind of call, at this number.
2. The opt-in record stores time, channel, language, and what they agreed to hear.
3. There is a working opt-out (spoken keypress and a written channel).
4. Quiet hours are enforced in the callee's time zone.
5. The script identifies who is calling and why in the first sentence.

## Sample spoken script (after consent)

"This is a reminder from Example Co about an upcoming payment you asked us to call about. It is not a collection call from a third party. Press 1 to hear the due date we have on file. Press 2 to stop these calls."

## What this example does not do

- It does not dial a purchased list
- It does not spoof a caller ID
- It does not claim to be a bank or a government agency
- It does not discuss another person's account with whoever picks up

Twilio account setup, webhook URLs, and production credentials are left to the operator. Do not commit secrets.
