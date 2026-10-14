# finds-you-jobs — AGPL-3.0-only.
"""Applier package: the agentic job-application core.

An AGPL-3.0-only facade and agent over a trimmed, Skyvern-derived observation
core in `upstream/` (provenance.md). The app uses only `types.py`'s contract
(applier-as-built.md section 3.1): `run_apply(page, ApplyRequest, engine, sink,
control) -> ApplyResult`. The tool vocabulary has no submit: success is
`ready_for_human` (8.4). `submit_application` sits outside it and runs once on
the user's own Submit click."""

from __future__ import annotations

from .classifier import classify
from .executor import UrlPolicy
from .loop import ApplyEngine, run_apply
from .observe import Observation, ObservedElement, observe
from .submit import SubmitOutcome, submit_application
from .types import (
    ApplyControl,
    ApplyError,
    ApplyEvent,
    ApplyEventSink,
    ApplyEventType,
    ApplyPhase,
    ApplyRequest,
    ApplyResult,
    ApplyStatus,
    ArtifactRef,
    Blocker,
    FieldOutcome,
    PageState,
    Usage,
)

__all__ = [
    "ApplyControl",
    "ApplyEngine",
    "ApplyError",
    "ApplyEvent",
    "ApplyEventSink",
    "ApplyEventType",
    "ApplyPhase",
    "ApplyRequest",
    "ApplyResult",
    "ApplyStatus",
    "ArtifactRef",
    "Blocker",
    "FieldOutcome",
    "Observation",
    "ObservedElement",
    "PageState",
    "SubmitOutcome",
    "UrlPolicy",
    "Usage",
    "classify",
    "observe",
    "run_apply",
    "submit_application",
]
