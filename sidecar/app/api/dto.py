"""Pydantic DTOs — the HTTP boundary (architecture §4.2, §5.2 one-way rule).

DTO ↔ ORM conversion happens *here* and only here: models/dataclasses never
cross into the wire types, and Pydantic never leaks into `modules/`. These
shapes drive the OpenAPI → TS codegen, so drift is a build error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..db.models import Operation

# ---------------------------------------------------------------------------
# Operations (architecture §5.3 — the ledger surface)
# ---------------------------------------------------------------------------


class OperationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    state: str
    input_snapshot: dict[str, Any]
    result_ref: dict[str, Any] | None
    usage: dict[str, Any] | None
    error: str | None
    engine: str | None
    model: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class OperationAccepted(BaseModel):
    id: str
    kind: str
    state: str


class CostTotalsDTO(BaseModel):
    """All-time cost totals for the Analytics cost tiles (FR-SET-07 / US-LOG-01 #2).

    Live-ledger sum + the pruned-ops aggregate, so the figures are lifetime totals
    that survive ledger retention — not just the retained ~250 ops. `by_kind` maps
    each operation kind to its all-time usd spend."""

    usd: float
    tokens_in: int
    tokens_out: int
    operations: int
    failed: int
    by_kind: dict[str, float]


def operation_dto(op: Operation) -> OperationDTO:
    return OperationDTO.model_validate(op)


def cost_totals_dto(totals: dict[str, Any]) -> CostTotalsDTO:
    """Build the all-time cost DTO from a repo cost aggregate (repos.CostTotals)."""
    return CostTotalsDTO(
        usd=float(totals.get("usd", 0.0)),
        tokens_in=int(totals.get("tokens_in", 0)),
        tokens_out=int(totals.get("tokens_out", 0)),
        operations=int(totals.get("operations", 0)),
        failed=int(totals.get("failed", 0)),
        by_kind={k: float(v) for k, v in (totals.get("by_kind") or {}).items()},
    )
