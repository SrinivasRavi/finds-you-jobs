# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Deterministic fakes for tests and the dev path — no model, no network.

``FakeApplyEngine`` replays a scripted list of replies (usually JSON tool
calls); tests drive the real loop + real executor + real Chromium against
local fixtures with it. Mirrors the FakeEngine pattern used by the networker
tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class FakeUsage:
    tokens_in: int = 10
    tokens_out: int = 5
    cost_usd: float | None = None


# A scripted step is either a literal reply or a callable computing the reply
# from the rendered user prompt (element ids are per-observation, so a test
# that wants to fill "Email" has to find its current eN in the prompt).
FakeStep = str | Callable[[str], str]


class FakeApplyEngine:
    """Replays scripted replies; raises if the script runs dry."""

    def __init__(self, replies: list[FakeStep]) -> None:
        self._replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, FakeUsage]:
        self.prompts.append((system_prompt, user_prompt))
        if not self._replies:
            raise AssertionError("FakeApplyEngine script exhausted")
        step = self._replies.pop(0)
        reply = step(user_prompt) if callable(step) else step
        return reply, FakeUsage()
