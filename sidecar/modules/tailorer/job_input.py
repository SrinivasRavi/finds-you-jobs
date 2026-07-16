"""Resolve the job input: markdown text, a local file path, or a URL.

The mechanics live in `sidecar/modules/_shared/job_input.py` (extracted
2026-07-05 at the second consumer, the Scorer, per the M1 playbook); this file
keeps the tailorer-typed contract (`TailorError`).
"""

from __future__ import annotations

from sidecar.modules._shared.job_input import JobInputError
from sidecar.modules._shared.job_input import resolve_job as _shared_resolve_job

from .types import TailorError


def resolve_job(job: str) -> str:
    """Return the JD as markdown/plain text.

    `job` may be: raw JD text, a path to a .md/.txt file, or an http(s) URL.
    """
    try:
        return _shared_resolve_job(job)
    except JobInputError as e:
        raise TailorError(e.stage, e.message) from e
