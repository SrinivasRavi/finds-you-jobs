"""The CoverLetterer black box: cover(master, job, ...) to CoverResult.

1 bounded operation; internals can change freely. The letter stands on the
master alone (FR-CL-01). The module owns no storage: interim files live in a
per-operation scratch dir deleted on return (`keep_scratch=True` to debug)."""

from __future__ import annotations

import datetime as _dt
import tempfile
from collections.abc import Callable
from pathlib import Path

from sidecar.modules._shared.completion_retry import complete_and_parse
from sidecar.modules._shared.location_line import enforce_location, location_instruction

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
    cancelled: Callable[[], bool] | None = None,
    location: tuple[str, str, str] | None = None,
) -> CoverResult:
    """Write a cover letter from `master_md` for `job` (JD text, a path or a URL).
    `skill_md` replaces the skill file (the user's prompt override). `cancelled` is
    polled at retry checkpoints and raises `CompletionCancelled` (F-M7). `location`
    is (mode, posting place, master's location line), the tailored resume's setting."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    samples = load_writing_samples(writing_samples_dir)
    system_prompt = skill_md if skill_md is not None else load_skill()
    mode, posting, master = location or ("master", "", "")
    user_prompt = build_user_prompt(
        master_md, jd_md, guidance, samples,
        location_rule=location_instruction(mode, posting, master, document="cover letter"),
    )

    scratch = tempfile.TemporaryDirectory(prefix="fyj-cover-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        # A JD-gate refusal (stage "jd-gate") is deliberate, so it's never retried.
        (cover_letter_md, notes), usage = complete_and_parse(
            engine, system_prompt, user_prompt, parse_output, CoverError, cancelled
        )
        # The skill writes {{DATE}} — the module, not the model, knows the date.
        cover_letter_md = cover_letter_md.replace("{{DATE}}", _dt.date.today().isoformat())
        cover_letter_md = enforce_location(
            cover_letter_md, mode, posting, master, block_only=True
        )
        return CoverResult(cover_letter_md=cover_letter_md, notes=notes, usage=Usage(**usage))
    finally:
        if not keep_scratch:
            scratch.cleanup()

