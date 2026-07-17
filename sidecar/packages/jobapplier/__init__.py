# finds-you-jobs — AGPL-3.0-only.
"""Applier package — the agentic job-application core.

A finds-you-jobs-owned facade and agent (package root, AGPL-3.0-only, written
new) over a trimmed, Skyvern-derived browser *observation* core under
``upstream/`` (also AGPL-3.0 — see ``provenance.md``). This slice (roadmap
commit 12 Slice A) ships observation only: ``observe(page)`` returns an
immutable ``Observation``. The agent loop, actions, and any fill/submit
capability land in the next commits (``docs/internal/applier.md`` §4).
"""

from __future__ import annotations

from .observe import Observation, ObservedElement, observe

__all__ = ["Observation", "ObservedElement", "observe"]
