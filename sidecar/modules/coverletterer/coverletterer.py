"""The CoverLetterer black box: cover(master, job, ...) → CoverResult.

One bounded operation at the interface (ROADMAP §4). Internals (engine, step
count, interim artifacts) are free to change without touching callers.
Independent of any tailored resume — the letter stands on the master alone
(FR-CL-01).

Storage stance (ROADMAP §4): the module owns no persistent storage. Interim
artifacts, if any step needs them, live in a per-operation scratch directory
that is deleted when the operation returns (pass keep_scratch=True to debug).
The final artifact is returned as a value; the caller (CLI now, app later)
decides where it lives.
"""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import MAX_ATTEMPTS, merge_usage

from .engine import ClaudeCliEngine, Engine
from .job_input import resolve_job
from .output_parse import parse_output
from .prompt import build_user_prompt, load_skill, load_writing_samples
from .types import CoverError, CoverResult, Usage


def cover(
    master_md: str,
    job: str,
    guidance: str = "",
    writing_samples_dir: Path | None = None,
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
) -> CoverResult:
    """Write a cover letter from `master_md` for `job` (raw JD text, a .md/.txt
    path, or a URL).

    `skill_md`, when provided, replaces the on-disk skill file as the system
    prompt (the app's user-editable-prompt override, §5). None → the default."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    samples = load_writing_samples(writing_samples_dir)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(master_md, jd_md, guidance, samples)

    scratch = tempfile.TemporaryDirectory(prefix="fyj-cover-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        #
        # A single completion is non-deterministic enough that an empty
        # response (EngineError) or a contract-drifted response (CoverError,
        # stage="parse") is worth one immediate re-ask before the whole
        # operation fails onto the user's Retry button. A deliberate JD-gate
        # refusal (stage="jd-gate") is NOT transient and must never be
        # retried. Every attempt that actually produced billable output —
        # even one that then failed to parse — is folded into the final usage
        # (cost honesty: a retry must never make a real spend vanish from the
        # ledger).
        billed: list[Usage] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw, usage = engine.complete(system_prompt, user_prompt)
            except EngineError:
                if attempt == MAX_ATTEMPTS:
                    raise
                continue
            try:
                cover_letter_md, notes = parse_output(raw)
            except CoverError as e:
                billed.append(usage)
                if e.stage != "parse" or attempt == MAX_ATTEMPTS:
                    raise
                continue
            billed.append(usage)
            # The skill writes {{DATE}} — the module, not the model, knows the date.
            cover_letter_md = cover_letter_md.replace("{{DATE}}", _dt.date.today().isoformat())
            return CoverResult(
                cover_letter_md=cover_letter_md, notes=notes, usage=Usage(**merge_usage(billed))
            )
        raise AssertionError("unreachable — loop always returns or raises")
    finally:
        if not keep_scratch:
            scratch.cleanup()


def dry_run_prompt(
    master_md: str,
    job: str,
    guidance: str = "",
    writing_samples_dir: Path | None = None,
) -> str:
    """Assemble and return the full prompt without any LLM call (CLI --dry-run)."""
    jd_md = resolve_job(job)
    samples = load_writing_samples(writing_samples_dir)
    return (
        "########## SYSTEM (skill) ##########\n"
        + load_skill()
        + "\n########## USER ##########\n"
        + build_user_prompt(master_md, jd_md, guidance, samples)
    )
