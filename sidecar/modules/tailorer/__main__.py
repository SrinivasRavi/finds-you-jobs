"""Tailorer CLI — the silo dogfood entry point (ROADMAP §4 CLI convention).

Examples:
    uv run python -m sidecar.modules.tailorer \
        --master sidecar/fixtures/masters/master_resume_1.md \
        --job sidecar/fixtures/jds/text/J01-glean-backend-bangalore.md \
        --out out/glean-backend.tailored.md

    ... --job https://job-boards.greenhouse.io/gleanwork/jobs/4006731005
    ... --guidance "lead with the identity/auth work"
    ... --dry-run          # print the assembled prompt, no LLM call
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL

from .engine import ClaudeCliEngine
from .tailorer import dry_run_prompt, tailor
from .types import TailorError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tailorer", description="finds-you-jobs Tailorer (silo CLI)")
    ap.add_argument("--master", required=True, type=Path, help="master resume .md")
    ap.add_argument("--job", required=True, help="JD: raw text, .md/.txt path, or URL")
    ap.add_argument("--guidance", default="", help="optional per-job guidance (US-TL-02)")
    ap.add_argument("--writing-samples", type=Path, default=None, help="dir of style samples")
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"claude CLI model (default: {DEFAULT_MODEL})"
    )
    ap.add_argument("--timeout", type=int, default=600, help="engine timeout seconds")
    ap.add_argument("--out", type=Path, default=None, help="write resume here (default: stdout)")
    ap.add_argument("--dry-run", action="store_true", help="print assembled prompt; no LLM call")
    args = ap.parse_args(argv)

    master_md = args.master.read_text()

    try:
        if args.dry_run:
            print(dry_run_prompt(master_md, args.job, args.guidance, args.writing_samples))
            return 0
        result = tailor(
            master_md,
            args.job,
            guidance=args.guidance,
            writing_samples_dir=args.writing_samples,
            engine=ClaudeCliEngine(model=args.model, timeout_s=args.timeout),
        )
    except TailorError as e:
        print(f"tailorer failed {e}", file=sys.stderr)
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.resume_md + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(result.resume_md)

    print("\n--- NOTES ---", file=sys.stderr)
    for n in result.notes:
        print(f"- {n}", file=sys.stderr)
    print(f"--- USAGE --- {asdict(result.usage)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
