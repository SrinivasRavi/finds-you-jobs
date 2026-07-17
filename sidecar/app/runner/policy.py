"""Per-kind concurrency policy (architecture §5.3) — table-driven + tunable.

Defaults: LLM kinds (`score`/`tailor`/`cover`) share a pool of ≤ 2 in flight;
`scan` is single-flight; `apply` runs exclusively (nothing else concurrent).
`can_start` is a pure decision function so the policy is unit-testable without
touching threads or the DB.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConcurrencyPolicy:
    groups: dict[str, str] = field(default_factory=dict)  # kind -> group name
    group_limits: dict[str, int] = field(default_factory=dict)  # group -> max in flight
    exclusive_kinds: frozenset[str] = frozenset()  # kinds that run alone

    def group_for(self, kind: str) -> str:
        return self.groups.get(kind, kind)

    def limit_for_group(self, group: str) -> int:
        return self.group_limits.get(group, 1)


DEFAULT_POLICY = ConcurrencyPolicy(
    groups={
        "score": "llm",
        "tailor": "llm",
        "cover": "llm",
        "draft": "llm",  # the Networker's one LLM op shares the LLM pool (Track N3)
        "scan": "scan",
        # Trash-TTL eviction (FR-SYS-04): zero-LLM, DB-only, single-flight.
        "cleanup_trash": "cleanup_trash",
        # One apply run at a time — but NOT exclusive: the agentic apply op
        # WAITS for a still-generating tailored resume (applier.md §8.1), so
        # the `tailor` op must be able to run beside it. Making apply
        # exclusive dead-locked that wait until the packet timeout
        # (2026-07-17 dogfood: "Waiting for résumé" for 15 minutes).
        "apply": "apply",
        # Networking voyager ops (Track N3) are each single-flight: LinkedIn
        # automation must never fan out in parallel (account-safety, NFR-LI-*).
        "discover": "networker_discover",
        "send": "networker_send",
        # The headed login (Track N4) opens a visible browser the user drives —
        # exclusive so nothing else contends for it.
        "linkedin_login": "linkedin_login",
        "archive_stale_contacts": "archive_stale_contacts",
    },
    group_limits={
        "llm": 2, "scan": 1, "cleanup_trash": 1, "apply": 1,
        "networker_discover": 1, "networker_send": 1,
        "linkedin_login": 1, "archive_stale_contacts": 1,
    },
    exclusive_kinds=frozenset({"linkedin_login"}),
)


# Dispatch priority (lower dispatches first). The pump serves queued ops in
# (priority, enqueue order): a user watching an Apply panel or a tracker card
# must never sit behind a bulk score fan-out (2026-07-17 dogfood: an apply
# queued behind 13 scores). Background bulk work keeps FIFO among itself.
DISPATCH_PRIORITY: dict[str, int] = {
    "apply": 0,
    "linkedin_login": 0,
    "tailor": 1,   # the apply packet-wait depends on these landing promptly
    "cover": 1,
    "draft": 2,
    "send": 2,
    "extract": 3,
    "discover": 3,
    "score": 8,
    "scan": 9,
}
DEFAULT_DISPATCH_PRIORITY = 5


def dispatch_priority(kind: str) -> int:
    return DISPATCH_PRIORITY.get(kind, DEFAULT_DISPATCH_PRIORITY)


def can_start(kind: str, running_kinds: Iterable[str], policy: ConcurrencyPolicy) -> bool:
    """True if an operation of `kind` may start given what is already running.

    Rules, in order:
      1. If an exclusive operation is running, nothing else may start.
      2. If `kind` is exclusive, it may start only when nothing is running.
      3. Otherwise `kind` may start while its concurrency group is under limit.
    """
    running = list(running_kinds)
    if any(k in policy.exclusive_kinds for k in running):
        return False
    if kind in policy.exclusive_kinds:
        return len(running) == 0
    group = policy.group_for(kind)
    running_in_group = sum(1 for k in running if policy.group_for(k) == group)
    return running_in_group < policy.limit_for_group(group)
