"""The Tailorer black box: tailor(master, job, ...) to TailorResult.

1 bounded operation; internals can change freely. The module owns no storage:
interim files live in a per-operation scratch dir deleted on return
(`keep_scratch=True` to debug)."""

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
    """Tailor `master_md` for `job` (JD text, a path or a URL). `skill_md` replaces
    the skill file; `cancelled` raises `CompletionCancelled` at retry checkpoints
    (F-M7)."""
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

