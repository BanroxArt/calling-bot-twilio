# calling-bot-twilio

A pattern for building a reminder or notification call flow with Twilio only after the callee has consented. Consent is a hard gate: no consent record, no outbound dial. This is not a robocall engine, not a sales dialer, and not a tool for blasting purchased lists.

Author: Art Akopian, CEO of [Banrox](https://banrox.com)

## What it does

This repository is an agent skill. After you install it, ask your coding agent to help design or build an inbound or outbound voice agent that you own, using Twilio first, with Vapi or Retell as alternatives. The agent should:

- Put lawful consent, do-not-call, identity, recording notices, and quiet hours first
- Treat U.S. TCPA-style rules as a starting checklist, with a clear "not legal advice" caveat, and say when counsel must review the use case before going live
- Recommend a stack and a build sequence that keeps consent, DNC, and call records in a system the team owns
- Refuse help with unsolicited spam, robocall evasion, spoofing caller ID to deceive, or calling people who have not consented where consent is required
- Return a plan: use-case restatement, recommended stack, data model, script outline, test plan on numbers the team owns, and open legal questions

Vendors do not take the legal risk off the table. Using Twilio, Vapi, Retell, or a carrier does not make a non-compliant campaign compliant.

## Install

Copy `SKILL.md` into your agent's skills folder, for example:

```text
.cursor/skills/calling-bot-twilio/SKILL.md
```

or the equivalent path your agent documents. Restart or reload skills if your agent requires it.

## Use

Describe who is called or who calls in, why, what success is, and what the bot must never say. Have a documented consent record and a working opt-out before any outbound dial. Use test numbers the team owns. If the request is to blast a purchased list, hide the caller, or evade carrier or legal rules, the skill should refuse.

## Example

See [examples/consent-gated-reminder.md](examples/consent-gated-reminder.md) for a fictional, consent-gated reminder pattern, including a sample script and the checks that must pass before any call is placed. It is a product pattern, not legal advice, and not a script to place live calls.

## Limitations

- Not legal advice and not a complete TCPA manual. Laws change and differ by country, state, and call purpose. Counsel should review a use case before production.
- Not a robocaller, auto-dialer for unsolicited sales, or a way to spoof caller ID or evade do-not-call rules.
- Transactional or informational calls can have a different (still real) standard than marketing calls; having a number in a CRM is not automatically consent.
- Financial, debt, and healthcare calls have additional rules and should stop for counsel before anyone builds.
- Twilio account setup, webhook URLs, and production credentials are left to the operator. Do not commit secrets.

## License

The `LICENSE` file currently contains the MIT License text, with copyright holder Banrox Inc. and year 2026. This is a placeholder. The public licence is pending a final pick by Banrox and may change before the repository is published. Do not treat MIT as final until that decision is recorded.

## Checks

From the repository root:

```text
python3 scripts/check.py
```

This validates skill frontmatter, required sections, example files, local links, and a lint that public files do not include internal names or unsupported certification or traction claims.

## Contributing

Issues and patches are welcome after a public repository exists. Do not send requests to evade consent, calling, or carrier rules, and do not send exploit proof-of-concepts.

## Related skills

Sibling skills from the same author, each in its own repository:

- `fraud-prevention` — defensive fraud-program checklist
- `cybersecurity-baseline` — small-company security baseline (not a certification)
- `live-codebase` — two-week plan to make one service shippable, observable, and safely connected to live data
