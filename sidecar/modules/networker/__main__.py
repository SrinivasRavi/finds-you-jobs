"""Networker CLI — the silo dogfood entry point (ROADMAP §4 CLI convention).

Commands (each supports --dry-run — no LLM, no browser, no network):
  discover  --company NAME [--limit N]
  draft     --profile ID --job (TEXT|FILE|URL) [--master FILE] [--title ...]
            [--company ...] [--degree N] [--audience ...] [--guidance ...]
  send      --profile ID --message TEXT --degree N [--tier ...]
  quota     [--tier ...]

`draft --dry-run` prints the assembled prompt (playbook + grounding), no LLM.
`discover/send --dry-run` forwards --dry-run to the voyager worker (plan
only). A real send is a maintainer-only, tiny-volume action (Track N / G5) —
never point it at real contacts in automation.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .networker import discover, draft, dry_run_prompt, quota, send
from .playbooks import tag_audience
from .types import Audience, Contact, NetworkerError


def _build_contact(args: argparse.Namespace) -> Contact:
    audience = (
        Audience(args.audience) if args.audience
        else tag_audience(args.title or "", args.headline or "")
    )
    return Contact(
        public_identifier=args.profile,
        full_name=args.name or "",
        current_title=args.title or "",
        current_company=args.company or "",
        headline=args.headline or "",
        connection_degree=args.degree,
        audience=audience,
    )


def _print_json(obj) -> None:
    print(json.dumps(obj, default=str, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="networker", description="finds-you-jobs Networker (silo CLI)"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="discover potential referrers at a company")
    d.add_argument("--company", required=True)
    d.add_argument("--limit", type=int, default=10)
    d.add_argument("--dry-run", action="store_true")

    df = sub.add_parser("draft", help="draft a referral-ask message")
    df.add_argument("--profile", required=True, help="contact public_identifier")
    df.add_argument("--job", required=True, help="JD text, a .md/.txt path, or a URL")
    df.add_argument("--master", help="path to the seeker's master profile markdown")
    df.add_argument("--name")
    df.add_argument("--title", help="contact role title (drives audience tagging)")
    df.add_argument("--company")
    df.add_argument("--headline")
    df.add_argument("--degree", type=int, default=None, help="connection degree (1=warm)")
    df.add_argument("--audience", choices=[a.value for a in Audience], default=None)
    df.add_argument("--guidance", default="")
    df.add_argument("--dry-run", action="store_true", help="print the prompt, no LLM")

    s = sub.add_parser("send", help="send a drafted message via voyager")
    s.add_argument("--profile", required=True)
    s.add_argument("--message", required=True)
    s.add_argument("--degree", type=int, default=None, help="connection degree (1=warm→DM)")
    s.add_argument("--tier", choices=["new", "seasoned"], default=None)
    s.add_argument("--dry-run", action="store_true")

    q = sub.add_parser("quota", help="report live remaining caps from voyager")
    q.add_argument("--tier", choices=["new", "seasoned"], default=None)

    args = ap.parse_args(argv)

    try:
        if args.command == "discover":
            result = discover(args.company, limit=args.limit, dry_run=args.dry_run)
            _print_json({"company": result.company,
                         "contacts": [dataclasses.asdict(c) for c in result.contacts],
                         "usage": dataclasses.asdict(result.usage)})
            return 0

        if args.command == "draft":
            contact = _build_contact(args)
            master_md = Path(args.master).read_text() if args.master else ""
            if args.dry_run:
                print(dry_run_prompt(contact, args.job, master_md=master_md,
                                     guidance=args.guidance))
                return 0
            result = draft(contact, args.job, guidance=args.guidance, master_md=master_md)
            _print_json({
                "audience": result.audience.value, "warmth": result.warmth.value,
                "channel": result.channel.value, "char_count": result.char_count,
                "message": result.message, "notes": result.notes,
                "usage": dataclasses.asdict(result.usage),
            })
            return 0

        if args.command == "send":
            contact = Contact(public_identifier=args.profile, connection_degree=args.degree)
            result = send(args.message, contact, tier=args.tier, dry_run=args.dry_run)
            _print_json(dataclasses.asdict(result))
            return 0 if (result.sent or args.dry_run) else 1

        if args.command == "quota":
            _print_json(quota(tier=args.tier))
            return 0
    except NetworkerError as e:
        print(f"networker failed: {e}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
