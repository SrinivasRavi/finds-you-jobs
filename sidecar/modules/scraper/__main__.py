"""Scraper CLI — the silo dogfood entry point (ROADMAP §4 CLI convention).

Examples:
    uv run python -m sidecar.modules.scraper \
        --portals sidecar/modules/scraper/portals.example.toml \
        --out jobs.jsonl

    ... --title-allow "software engineer" --location-allow india --location-allow remote
    ... --dry-run          # print adapter claims per source; no network
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import adapters
from .config import load_portals
from .scraper import scan
from .types import ScraperError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scraper", description="finds-you-jobs Scraper (silo CLI)")
    ap.add_argument("--portals", required=True, type=Path, help="portals config (.toml/.json)")
    ap.add_argument("--out", type=Path, default=None, help="write JSONL here (default: stdout)")
    ap.add_argument(
        "--dry-run", action="store_true", help="print adapter claims per source; no network"
    )
    ap.add_argument("--title-allow", action="append", default=[], help="override title allowlist")
    ap.add_argument("--title-block", action="append", default=[], help="override title blocklist")
    ap.add_argument(
        "--location-allow", action="append", default=[], help="override location allowlist"
    )
    ap.add_argument(
        "--location-block", action="append", default=[], help="override location blocklist"
    )
    ap.add_argument(
        "--location-always-allow",
        action="append",
        default=[],
        help="override location always-allow (rescues multi-location postings)",
    )
    ap.add_argument("--max-age-days", type=int, default=None, help="freshness window (0 = off)")
    ap.add_argument(
        "--per-source-cap", type=int, default=None, help="cap rows per source (0 = uncapped)"
    )
    ap.add_argument("--timeout", type=int, default=None, help="per-request timeout seconds")
    args = ap.parse_args(argv)

    try:
        config = load_portals(args.portals)
        prefs = config.prefs
        if args.title_allow:
            prefs.title_allow = args.title_allow
        if args.title_block:
            prefs.title_block = args.title_block
        if args.location_allow:
            prefs.location_allow = args.location_allow
        if args.location_block:
            prefs.location_block = args.location_block
        if args.location_always_allow:
            prefs.location_always_allow = args.location_always_allow
        if args.max_age_days is not None:
            prefs.max_age_days = args.max_age_days
        if args.per_source_cap is not None:
            prefs.per_source_cap = args.per_source_cap
        if args.timeout is not None:
            prefs.timeout_s = args.timeout

        if args.dry_run:
            for entry in config.sources:
                resolved = adapters.resolve(entry)
                label = entry.url or entry.board
                if resolved is None:
                    print(f"UNRESOLVED  {label}")
                else:
                    _, key = resolved
                    print(f"{key:40s}  {label}")
            return 0

        result = scan(config, prefs)
    except ScraperError as e:
        print(f"scraper failed {e}", file=sys.stderr)
        return 1

    lines = "".join(json.dumps(job.to_dict(), ensure_ascii=False) + "\n" for job in result.jobs)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(lines)
        print(f"wrote {len(result.jobs)} job(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(lines)

    print("--- PER-SOURCE ---", file=sys.stderr)
    for key, report in result.per_source.items():
        print(
            f"{key:40s} fetched={report.fetched:<5d} kept={report.kept:<5d} "
            f"http_calls={report.usage.internal_calls} "
            f"latency_ms={report.usage.latency_ms or 0}",
            file=sys.stderr,
        )
        for err in report.errors:
            print(f"  ! {err}", file=sys.stderr)
    total_errors = sum(len(r.errors) for r in result.per_source.values())
    print(
        f"--- TOTAL --- jobs={len(result.jobs)} sources={len(result.per_source)} "
        f"errors={total_errors}",
        file=sys.stderr,
    )
    print(
        "--- USAGE --- "
        + json.dumps({k: asdict(r.usage) for k, r in result.per_source.items()}),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
