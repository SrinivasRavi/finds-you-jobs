"""LLM engines for the tailorer black box.

v0 ships one engine: the `claude` CLI in print mode, driven as a subprocess.
Deliberate choice for the silo phase (ROADMAP §5 M1): it runs on the
maintainer's Claude subscription — the same account and model career-ops runs
on — so parity comparisons are apples-to-apples (roadmap grill Q9).
An API-keyed engine is a later drop-in behind the same protocol.

The subprocess mechanics live in `sidecar/modules/_shared/claude_engine.py`
(extracted 2026-07-03 at the second consumer, per the M1 playbook); this file
keeps the tailorer-typed contract (`TailorError`, `Usage`).

Internals of this module may become multi-call/multi-step without touching the
tailor() interface (black-box rule, ROADMAP §3.2).
"""

from __future__ import annotations

from typing import Protocol

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL, EngineError
from sidecar.modules._shared.claude_engine import ClaudeCliEngine as _SharedClaudeCliEngine

from .types import TailorError, Usage


class Engine(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        """One completion. Returns (text, usage). Raises TailorError on failure."""
        ...


class ClaudeCliEngine:
    """`claude -p` subprocess. Model pinned per run for reproducible parity."""

    def __init__(self, model: str = DEFAULT_MODEL, timeout_s: int = 600) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._inner = _SharedClaudeCliEngine(model=model, timeout_s=timeout_s)

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        try:
            text, u = self._inner.complete(system_prompt, user_prompt)
        except EngineError as e:
            raise TailorError("engine", str(e)) from e
        return text, Usage(
            internal_calls=u.internal_calls,
            tokens_in=u.tokens_in,
            tokens_out=u.tokens_out,
            usd=u.usd,
            latency_ms=u.latency_ms,
            model=u.model,
        )
