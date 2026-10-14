"""Per-kind concurrency policy (architecture section 5.3) — table-driven + tunable.

Defaults: AI kinds wait in lanes picked by `group_for` (1 per kind of work, or
1 shared); app scoring is capped by thresholds.llm_concurrency (default 4,
0 = unlimited); `scan` and the
networking ops are single-flight; `apply` runs APPLY_PARALLEL tabs BESIDE
the LLM pool (its packet wait depends on tailor); only the headed
`linkedin_login` is exclusive. Dispatch is priority-ordered so interactive
work never starves behind bulk fan-outs. `can_start` is a pure decision
function so the policy is unit-testable without touching threads or the DB.
Design and the 2 queue choices: docs/internal/queueing.md (audit A1).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

# The AI kinds the 2 queue choices lane (queueing.md section 5). `apply` calls a
# model too, but holds a browser, so it keeps its own lane of 1 in both.
AI_KINDS = frozenset({"score", "tailor", "cover", "draft", "extract"})
WRITING_KINDS = frozenset({"tailor", "cover", "draft"})
QUEUE_PER_KIND = "per_kind"
QUEUE_SHARED = "shared"
# Lanes other than scoring have no setting yet (maintainer, 2026-10-13: "for now
# only scoring has the parallelization"), so they keep today's default width.
FIXED_LANE_LIMIT = 4
# Applications filled at once, each in its own tab of the Applier window
# (applier.md Want 22, "something like 10 or 20"; applier/window.md section 4.7).
APPLY_PARALLEL = 10


@dataclass(frozen=True)
class ConcurrencyPolicy:
    groups: dict[str, str] = field(default_factory=dict)  # kind -> group name
    group_limits: dict[str, int] = field(default_factory=dict)  # group -> max in flight
    exclusive_kinds: frozenset[str] = frozenset()  # kinds that run alone
    ai_queue_mode: str = QUEUE_PER_KIND

    def group_for(self, kind: str, requested_by: str = "app") -> str:
        """The lane an op waits in. Shared: every AI kind in `llm`. Per kind: writing,
        the user's other requests, app scoring (`llm`, the Parallel AI calls cap) and
        app extracts each get their own lane."""
        if kind in AI_KINDS:
            if self.ai_queue_mode == QUEUE_SHARED:
                return "llm"
            if kind in WRITING_KINDS:
                return "ai_writing"
            if requested_by == "user":
                return "ai_requests"
            return "llm" if kind == "score" else kind
        return self.groups.get(kind, kind)

    def limit_for_group(self, group: str) -> int:
        return self.group_limits.get(group, 1)


DEFAULT_POLICY = ConcurrencyPolicy(
    groups={
        # AI kinds (`AI_KINDS`) are laned by `group_for`, not by this table.
        "scan": "scan",
        # Trash-TTL eviction (FR-SYS-04): zero-LLM, DB-only, single-flight.
        "cleanup_trash": "cleanup_trash",
        # APPLY_PARALLEL runs at once, 1 tab each — but NOT exclusive: the agentic
        # apply op WAITS for a still-generating tailored resume (applier-as-built.md
        # section 8.1), so the `tailor` op must be able to run beside it. Making apply
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
        # A user's queued page-view on the shared browser surface (2026-08-16).
        "view_page": "view_page",
    },
    group_limits={
        # 4 = DEFAULT_LLM_CONCURRENCY below; the boot wiring overrides from
        # thresholds.llm_concurrency (Settings → Scoring → Parallel AI calls).
        "llm": 4, "ai_writing": FIXED_LANE_LIMIT, "ai_requests": FIXED_LANE_LIMIT,
        "extract": 1, "scan": 1, "cleanup_trash": 1, "apply": APPLY_PARALLEL,
        "networker_discover": 1, "networker_send": 1,
        "linkedin_login": 1, "archive_stale_contacts": 1, "view_page": 1,
    },
    exclusive_kinds=frozenset({"linkedin_login"}),
)


# The op kinds that drive the shared browser lane (mirrors the frontend queue
# panel's tracked set). A user's `view_page` must never slot a navigation into
# the gap BETWEEN a running op's lane actions (discover and contact_sync take
# the lane several times per op), so admission — not just the lane — keeps a
# view and any other lane kind mutually exclusive; see `can_start` rule 3.
BROWSER_LANE_KINDS = frozenset(
    {"send", "discover", "contact_sync", "linkedin_search", "linkedin_login", "view_page"}
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


# User-tunable LLM parallelism (2026-07-17 dogfood). 0 = unlimited — the
# user owns the tradeoff (parallel spend, provider rate limits); the UI says
# so in words. Bounded values clamp to [1, 20] so a typo can't zero the pool.
DEFAULT_LLM_CONCURRENCY = 4
_UNLIMITED = 1_000_000


def llm_concurrency_from(thresholds: dict | None) -> int:
    raw = (thresholds or {}).get("llm_concurrency", DEFAULT_LLM_CONCURRENCY)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LLM_CONCURRENCY
    if value == 0:
        return _UNLIMITED
    return max(1, min(20, value))


def with_llm_limit(policy: ConcurrencyPolicy, limit: int) -> ConcurrencyPolicy:
    """A copy of `policy` with the llm group's cap replaced (dataclass-frozen)."""
    limits = dict(policy.group_limits)
    limits["llm"] = limit
    return replace(policy, group_limits=limits)


def fit_to_pool(
    policy: ConcurrencyPolicy, pool_size: int, kinds: Iterable[str]
) -> ConcurrencyPolicy:
    """Cap scoring at the threads every other lane leaves free, so a full scoring
    lane can never hold a thread a click needs. Unlimited means exactly that
    many (queueing.md section 8, audit A19)."""
    others = {policy.group_for(k, by) for k in kinds for by in ("app", "user")} - {"llm"}
    reserved = sum(policy.limit_for_group(g) for g in others)
    ceiling = max(1, pool_size - reserved)
    return with_llm_limit(policy, min(policy.limit_for_group("llm"), ceiling))


def ai_queue_mode_from(thresholds: dict | None) -> str:
    """`thresholds.ai_queue_mode`; anything unknown reads as the default, per kind."""
    raw = (thresholds or {}).get("ai_queue_mode")
    return QUEUE_SHARED if raw == QUEUE_SHARED else QUEUE_PER_KIND


def with_queue_mode(policy: ConcurrencyPolicy, mode: str) -> ConcurrencyPolicy:
    return replace(policy, ai_queue_mode=mode)


def policy_from(thresholds: dict | None) -> ConcurrencyPolicy:
    """The default policy under the user's saved Parallel AI calls and queue choice."""
    capped = with_llm_limit(DEFAULT_POLICY, llm_concurrency_from(thresholds))
    return with_queue_mode(capped, ai_queue_mode_from(thresholds))


RunningOp = str | tuple[str, str]  # a kind, or (kind, requested_by)


def can_start(
    kind: str,
    running_ops: Iterable[RunningOp],
    policy: ConcurrencyPolicy,
    requested_by: str = "app",
) -> bool:
    """True if `kind` may start given what's running: nothing beside an exclusive op;
    an exclusive kind only when idle; `view_page` and other browser-lane kinds never
    together, either way (`send` and `discover` admission untouched); otherwise
    while its lane is under its limit. A bare kind in `running_ops` reads as `app`."""
    pairs = [(op, "app") if isinstance(op, str) else op for op in running_ops]
    running = [k for k, _ in pairs]
    if any(k in policy.exclusive_kinds for k in running):
        return False
    if kind in policy.exclusive_kinds:
        return len(running) == 0
    if kind == "view_page":
        if any(k in BROWSER_LANE_KINDS for k in running):
            return False
    elif kind in BROWSER_LANE_KINDS and "view_page" in running:
        return False
    group = policy.group_for(kind, requested_by)
    running_in_group = sum(1 for k, by in pairs if policy.group_for(k, by) == group)
    return running_in_group < policy.limit_for_group(group)
