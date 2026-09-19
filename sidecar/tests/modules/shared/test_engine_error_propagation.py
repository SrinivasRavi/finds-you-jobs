"""Covers: S-C2 — the module CLI engine wrappers must not swallow EngineError.

All 4 LLM modules wrap the shared `claude -p` engine into their own typed
contract. Each used to convert `EngineError` into its module error, which
silently disabled 2 things that only ever key on `EngineError`: the module's
own bounded retry loop, and the runner's circuit breaker
(`sidecar/app/runner/runner.py`, `circuit.py`). The HTTP engines raise
`EngineError` directly and always had both, so this pins the parity.
"""

from __future__ import annotations

import pytest

from sidecar.modules._shared.claude_engine import EngineError, EngineUsage
from sidecar.modules.coverletterer import engine as cover_engine
from sidecar.modules.networker import engine as networker_engine
from sidecar.modules.scorer import engine as scorer_engine
from sidecar.modules.tailorer import engine as tailorer_engine

_WRAPPERS = [
    pytest.param(scorer_engine, id="scorer"),
    pytest.param(tailorer_engine, id="tailorer"),
    pytest.param(networker_engine, id="networker"),
    pytest.param(cover_engine, id="coverletterer"),
]


class _Boom:
    def complete(self, system_prompt: str, user_prompt: str):
        raise EngineError("claude CLI exited 1: rate limited, retry later")


@pytest.mark.parametrize("module", _WRAPPERS)
def test_cli_wrapper_lets_engine_error_through(module) -> None:
    eng = module.ClaudeCliEngine()
    eng._inner = _Boom()
    with pytest.raises(EngineError, match="rate limited, retry later"):
        eng.complete("system", "user")


@pytest.mark.parametrize("module", _WRAPPERS)
def test_cli_wrapper_still_passes_usage_through(module) -> None:
    """The conversion removal must not disturb the success path's usage."""

    class _Ok:
        def complete(self, system_prompt: str, user_prompt: str):
            return "text", EngineUsage(
                internal_calls=1, tokens_in=7, tokens_out=3, usd=0.02, model="m"
            )

    eng = module.ClaudeCliEngine()
    eng._inner = _Ok()
    text, usage = eng.complete("system", "user")
    assert text == "text"
    assert usage.tokens_in == 7
    assert usage.usd == pytest.approx(0.02)
    assert usage.model == "m"
