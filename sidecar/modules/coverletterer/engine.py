"""LLM engines for the coverletterer black box.

v0 ships one engine: the shared `claude -p` subscription engine (see
`sidecar/modules/_shared/claude_engine.py`), wrapped into the coverletterer-typed
contract (`CoverError`, `Usage`). An API-keyed engine is a later drop-in behind
the same protocol (G7 checklist item 9).
"""

from __future__ import annotations

from typing import Protocol

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL
from sidecar.modules._shared.claude_engine import ClaudeCliEngine as _SharedClaudeCliEngine

from .types import Usage


class Engine(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        """One completion. Returns (text, usage). Raises EngineError on an
        engine failure, CoverError on a contract failure."""
        ...


class ClaudeCliEngine:
    """`claude -p` subprocess. Model pinned per run for reproducible parity."""

    def __init__(self, model: str = DEFAULT_MODEL, timeout_s: int = 600) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._inner = _SharedClaudeCliEngine(model=model, timeout_s=timeout_s)

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        # EngineError propagates on purpose (S-C2): the module's retry loop
        # catches it, and the runner's circuit breaker counts only EngineError.
        # Converting it here disabled both on the CLI path while the HTTP
        # engines, which raise it directly, kept them.
        text, u = self._inner.complete(system_prompt, user_prompt)
        return text, Usage(
            internal_calls=u.internal_calls,
            tokens_in=u.tokens_in,
            tokens_out=u.tokens_out,
            usd=u.usd,
            latency_ms=u.latency_ms,
            model=u.model,
        )
