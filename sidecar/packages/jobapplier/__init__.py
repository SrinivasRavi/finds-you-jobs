# finds-you-jobs — AGPL-3.0-only.
"""Applier package — the agentic job-application core.

A finds-you-jobs-owned facade and agent (package root, AGPL-3.0-only, written
new) over a trimmed, Skyvern-derived browser *observation* core under
``upstream/`` (also AGPL-3.0 — see ``provenance.md``).

The app talks to this package ONLY through the typed contract in ``types.py``
(`docs/internal/archived/applier-as-built.md` section 3.1): ``run_apply(page, ApplyRequest, engine,
sink, control) -> ApplyResult``. The tool vocabulary has no ``submit`` — the
P1 terminal success is ``ready_for_human`` and a human clicks Submit (section 8.4).
``submit_application`` sits deliberately outside that vocabulary: the agent
still cannot submit, and the app calls it once on the user's own Submit click.
No route calls it yet.
"""

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
