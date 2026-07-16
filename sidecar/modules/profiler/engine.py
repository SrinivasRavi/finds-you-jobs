"""LLM engines for the profiler black box — same seam as the scorer/tailorer:
a narrow `Engine` protocol; `claude -p` (shared subprocess engine) is the dev
default, the app routes BYOK engines behind the same protocol."""

from __future__ import annotations

from typing import Protocol

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL, EngineError
from sidecar.modules._shared.claude_engine import ClaudeCliEngine as _SharedClaudeCliEngine

from .types import ProfileError, Usage


class Engine(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        """One completion. Returns (text, usage). Raises ProfileError on failure."""
        ...


class ClaudeCliEngine:
    """`claude -p` subprocess wrapped into the profiler-typed contract."""

    def __init__(self, model: str = DEFAULT_MODEL, timeout_s: int = 300) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._inner = _SharedClaudeCliEngine(model=model, timeout_s=timeout_s)

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        try:
            text, u = self._inner.complete(system_prompt, user_prompt)
        except EngineError as e:
            raise ProfileError("engine", str(e)) from e
        return text, Usage(
            internal_calls=u.internal_calls,
            tokens_in=u.tokens_in,
            tokens_out=u.tokens_out,
            usd=u.usd,
            latency_ms=u.latency_ms,
            model=u.model,
        )
