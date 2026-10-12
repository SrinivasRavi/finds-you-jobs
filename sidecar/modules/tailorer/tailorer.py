"""The Tailorer black box: tailor(master, job, ...) → TailorResult.

One bounded operation at the interface (ROADMAP section 4). Internals (engine, step
count, interim artifacts) are free to change without touching callers.

Storage stance (roadmap comment, resolved 2026-07-03): the module owns no
persistent storage. Interim artifacts, if any step needs them, live in a
per-operation scratch directory that is deleted when the operation returns
(pass keep_scratch=True to debug). The final artifact is returned as a value;
the caller (CLI now, app later) decides where it lives.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from sidecar.modules._shared.completion_retry import complete_and_parse

from .engine import ClaudeCliEngine, Engine
from .job_input import resolve_job
from .output_parse import parse_output
from .prompt import build_user_prompt, load_skill, load_writing_samples
from .types import TailorError, TailorResult, Usage


def tailor(
    master_md: str,
    job: str,
    guidance: str = "",
    writing_samples_dir: Path | None = None,
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> TailorResult:
    """Tailor `master_md` for `job` (raw JD text, a .md/.txt path, or a URL).

    `skill_md`, when provided, replaces the on-disk skill file as the system
    prompt (the app's user-editable-prompt override, section 5). None → the default.
    `cancelled`, when provided, is polled at the retry checkpoints (loop top,
    mid-backoff); True raises `CompletionCancelled` (cooperative Stop, F-M7)."""
    engine = engine or ClaudeCliEngine()
    jd_md = resolve_job(job)
    samples = load_writing_samples(writing_samples_dir)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(master_md, jd_md, guidance, samples)

    scratch = tempfile.TemporaryDirectory(prefix="fyj-tailor-")
    try:
        # v0 engine needs no interim files; the scratch dir is the seam future
        # multi-step internals (checkpoints, sub-agent handoffs) write into.
        (resume_md, notes), usage = complete_and_parse(
            engine, system_prompt, user_prompt, parse_output, TailorError, cancelled
        )
        return TailorResult(resume_md=resume_md, notes=notes, usage=Usage(**usage))
    finally:
        if not keep_scratch:
            scratch.cleanup()

