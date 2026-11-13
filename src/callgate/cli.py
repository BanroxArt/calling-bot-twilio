"""callgate command line: manage consent and DNC, check the gate, serve."""

from __future__ import annotations

import argparse
import json
import sys
from wsgiref.simple_server import make_server

from callgate import config
from callgate.app import App
from callgate.dialer import Dialer
from callgate.gate import Gate
from callgate.hours import TimezoneError, zone
from callgate.phone import PhoneError, normalize
from callgate.store import Store
from callgate.twilio import TwilioClient


def _store(args: argparse.Namespace) -> Store:
    return Store(args.db)


def cmd_consent_add(args: argparse.Namespace) -> int:
    try:
        phone = normalize(args.phone)
        zone(args.timezone)
    except (PhoneError, TimezoneError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not args.evidence.strip():
        print("evidence is required: where and how the person agreed", file=sys.stderr)
        return 2
    cid = _store(args).add_consent(
        phone, args.purpose, args.timezone, args.source, args.evidence, args.disclosure_version
    )
    print(json.dumps({"consent_id": cid}))
    return 0


def cmd_consent_revoke(args: argparse.Namespace) -> int:
    try:
        phone = normalize(args.phone)
    except PhoneError as exc:
        print(exc, file=sys.stderr)
        return 2
    s = _store(args)
    n = s.revoke_all(phone, "cli")
    s.add_dnc(phone, "cli")
    print(json.dumps({"revoked": n, "dnc": True}))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = config.load(require_twilio=False)
    gate = Gate(settings, _store(args))
    d = gate.preview(args.phone, args.purpose)
    print(
        json.dumps(
            {
                "allowed": d.allowed,
                "reasons": list(d.reasons),
                "retry_after": d.retry_after.isoformat() if d.retry_after else None,
            }
        )
    )
    return 0 if d.allowed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    settings = config.load()
    store = Store(settings.database_path)
    gate = Gate(settings, store)
    client = TwilioClient(settings.account_sid, settings.auth_token)
    app = App(settings, store, Dialer(settings, store, gate, client))
    print(
        f"listening on {settings.host}:{settings.port}, outbound "
        f"{'ENABLED' if settings.outbound_enabled else 'disabled'}",
        file=sys.stderr,
    )
    make_server(settings.host, settings.port, app).serve_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="callgate")
    p.add_argument("--db", default="callgate.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consent", help="record or revoke consent")
    csub = c.add_subparsers(dest="action", required=True)
    add = csub.add_parser("add")
    add.add_argument("phone")
    add.add_argument("--purpose", required=True)
    add.add_argument("--timezone", required=True, help="IANA name, e.g. America/Chicago")
    add.add_argument("--source", required=True, help="where it was captured, e.g. web_form")
    add.add_argument(
        "--evidence", required=True, help="pointer to the record: form id, signed doc id"
    )
    add.add_argument(
        "--disclosure-version", required=True, help="version of the text they agreed to"
    )
    add.set_defaults(func=cmd_consent_add)
    rev = csub.add_parser("revoke")
    rev.add_argument("phone")
    rev.set_defaults(func=cmd_consent_revoke)

    chk = sub.add_parser("check", help="would the gate allow a call right now")
    chk.add_argument("phone")
    chk.add_argument("--purpose", required=True)
    chk.set_defaults(func=cmd_check)

    srv = sub.add_parser("serve", help="run the webhook server (wsgiref, for development)")
    srv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc: int = args.func(args)
    return rc
