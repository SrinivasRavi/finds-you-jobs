"""The Scorer black box: score(master, job) → ScoreResult.

One bounded operation at the interface (ROADMAP §4). Internals (engine, step
count, interim artifacts) are free to change without touching callers.

Storage stance (ROADMAP §4): the module owns no persistent storage. Interim
artifacts, if any step needs them, live in a per-operation scratch directory
that is deleted when the operation returns (pass keep_scratch=True to debug).
The final artifact is returned as a value; the caller (CLI now, app later)
decides where it lives.
"""

from __future__ import annotations

import tempfile

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import MAX_ATTEMPTS, merge_usage

from .engine import ClaudeCliEngine, Engine
from .job_input import resolve_job
from .output_parse import parse_output
from .prompt import build_user_prompt, load_skill
from .types import ScoreError, ScoreResult, Usage


def score(
    master_md: str,
    job: str,
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
) -> ScoreResult:
    """Score `master_md` against `job` (raw JD text, a .md/.txt path, or a URL).

    `skill_md`, when provided, replaces the on-disk skill file as the system
    prompt (the app's user-editable-prompt override, §5). None → the default."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(master_md, jd_md)

    scratch = tempfile.TemporaryDirectory(prefix="fyj-score-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        #
        # A single completion is non-deterministic enough that an empty
        # response (EngineError) or a contract-drifted response (ScoreError,
        # stage="parse") is worth one immediate re-ask before the whole
        # operation fails onto the user's Retry button. Every attempt that
        # actually produced billable output — even one that then failed to
        # parse — is folded into the final usage (cost honesty: a retry must
        # never make a real spend vanish from the ledger).
        billed: list[Usage] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw, usage = engine.complete(system_prompt, user_prompt)
            except EngineError:
                if attempt == MAX_ATTEMPTS:
                    raise
                continue
            try:
                value, reasons, breakdown_md = parse_output(raw)
            except ScoreError as e:
                billed.append(usage)
                if e.stage != "parse" or attempt == MAX_ATTEMPTS:
                    raise
                continue
            billed.append(usage)
            return ScoreResult(
                score=value,
                reasons=reasons,
                breakdown_md=breakdown_md,
                usage=Usage(**merge_usage(billed)),
            )
        raise AssertionError("unreachable — loop always returns or raises")
    finally:
        if not keep_scratch:
            scratch.cleanup()


def dry_run_prompt(master_md: str, job: str) -> str:
    """Assemble and return the full prompt without any LLM call (CLI --dry-run)."""
    jd_md = resolve_job(job)
    return (
        "########## SYSTEM (skill) ##########\n"
        + load_skill()
        + "\n########## USER ##########\n"
        + build_user_prompt(master_md, jd_md)
    )
