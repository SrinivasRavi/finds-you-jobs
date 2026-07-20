"""Bounded retry support for one LLM completion + parse step, shared by the
score/tailor/cover black boxes (extracted at the third consumer, per the M1
playbook — see `skill_md.py`'s own extraction note).

A single non-deterministic completion occasionally comes back empty (a
provider hiccup, or a reasoning model spending its whole token budget on
hidden reasoning) or drifts off the module's strict `===...===` output
contract. Both are worth one immediate re-ask before the whole operation
fails and the user has to notice a FAILED row and click Retry by hand. A
deliberate business-logic outcome (the cover module's JD-gate `REFUSED:`) is
NOT a transient failure and must never be retried — that decision stays in
each module's own retry predicate, not here.

Retries mean more than one billed completion can precede a success; `usd`
being an honest ledger (never swallowed, never guessed) means every billed
attempt's cost/tokens must be counted, not just the winning one — that's what
`merge_usage` is for. A completion that raises before returning has no usage
to bill (the `Engine.complete()` contract only returns usage on success), so
only attempts that *produced output* (whether or not it parsed) contribute.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

# 1 initial attempt + 2 retries. LLM output is non-deterministic enough that a
# same-prompt re-ask clears most transient empty-content/parse-contract misses;
# past that it's very likely a persistent problem worth surfacing, not masking.
MAX_ATTEMPTS = 3


class _UsageLike(Protocol):
    internal_calls: int
    tokens_in: int | None
    tokens_out: int | None
    usd: float | None
    latency_ms: int | None
    model: str | None


def merge_usage(usages: Sequence[_UsageLike]) -> dict[str, Any]:
    """Sum a list of billed attempts into one usage record (as a dict — the
    caller constructs its own module-typed `Usage` from it). A field stays
    `None` only when every attempt reported `None` for it (an honest "still
    unknown", never a fabricated 0); otherwise missing values count as 0 so
    one attempt's known cost is never blanked out by a sibling's gap."""

    def _sum(attr: str) -> float | None:
        values = [getattr(u, attr) for u in usages]
        if all(v is None for v in values):
            return None
        return sum(v or 0 for v in values)

    return {
        "internal_calls": sum(u.internal_calls for u in usages),
        "tokens_in": _sum("tokens_in"),
        "tokens_out": _sum("tokens_out"),
        "usd": _sum("usd"),
        "latency_ms": _sum("latency_ms"),
        # The winning attempt's model — retries always target the same routed
        # engine/model, so this is never actually a mix in practice.
        "model": usages[-1].model,
    }
