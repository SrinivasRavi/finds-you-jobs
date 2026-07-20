"""Scorer CLI — the silo dogfood entry point (ROADMAP §4 CLI convention).

Examples:
    uv run python -m sidecar.modules.scorer \
        --master sidecar/fixtures/masters/master_resume_1.md \
        --job sidecar/fixtures/jds/text/J01-glean-backend-bangalore.md

    ... --job https://job-boards.greenhouse.io/gleanwork/jobs/4006731005
    ... --dry-run          # print the assembled prompt, no LLM call
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL

from .engine import ClaudeCliEngine
from .scorer import dry_run_prompt, score
from .types import ScoreError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scorer", description="finds-you-jobs Scorer (silo CLI)")
    ap.add_argument("--master", required=True, type=Path, help="master resume .md")
    ap.add_argument("--job", required=True, help="JD: raw text, .md/.txt path, or URL")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"claude CLI model (default: {DEFAULT_MODEL})"
    )
    ap.add_argument("--timeout", type=int, default=600, help="engine timeout seconds")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write score+reasons+breakdown here (default: stdout)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print assembled prompt; no LLM call")
    args = ap.parse_args(argv)

    master_md = args.master.read_text(encoding="utf-8")

    try:
        if args.dry_run:
            print(dry_run_prompt(master_md, args.job))
            return 0
        result = score(
            master_md,
            args.job,
            engine=ClaudeCliEngine(model=args.model, timeout_s=args.timeout),
        )
    except ScoreError as e:
        print(f"scorer failed {e}", file=sys.stderr)
        return 1

    report = (
        f"Score: {result.score}/100\n\n## Reasons\n"
        + "".join(f"- {r}\n" for r in result.reasons)
        + f"\n## Breakdown\n{result.breakdown_md}\n"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)
    print(f"--- USAGE --- {asdict(result.usage)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
