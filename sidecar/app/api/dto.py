"""Pydantic DTOs — the HTTP boundary (architecture §4.2, §5.2 one-way rule).

DTO ↔ ORM conversion happens *here* and only here: models/dataclasses never
cross into the wire types, and Pydantic never leaks into `modules/`. These
shapes drive the OpenAPI → TS codegen, so drift is a build error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..db.models import EngineSettings, MasterProfile, Operation, UserPreferences

# ---------------------------------------------------------------------------
# Profile (database-design §3)
# ---------------------------------------------------------------------------


class ProfileDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resume_markdown: str
    version: int
    # Structured form-fill facts (FR-APP-01) — extracted by the `extract` op at
    # save, user-editable in Settings; null until extracted.
    application_profile: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ProfileUpsert(BaseModel):
    resume_markdown: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class PreferencesDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role_aliases: list[Any]
    locations: list[Any]
    freshness_days: int
    hard_excludes: dict[str, Any]
    hard_requires: dict[str, Any]
    soft_preferences: dict[str, Any]
    thresholds: dict[str, Any]
    portals_config: dict[str, Any]
    voyager_risk_marker_on: bool
    engine_routing: dict[str, Any]
    ui_state: dict[str, Any]


class PreferencesUpdate(BaseModel):
    role_aliases: list[Any] | None = None
    locations: list[Any] | None = None
    freshness_days: int | None = None
    hard_excludes: dict[str, Any] | None = None
    hard_requires: dict[str, Any] | None = None
    soft_preferences: dict[str, Any] | None = None
    thresholds: dict[str, Any] | None = None
    portals_config: dict[str, Any] | None = None
    voyager_risk_marker_on: bool | None = None
    engine_routing: dict[str, Any] | None = None
    ui_state: dict[str, Any] | None = None


class EngineSettingDTO(BaseModel):
    """Engine config sans secret — `has_key` reports presence, `key_hint` is a
    masked display fragment (e.g. `sk-…abc4`), never the key itself."""

    id: str
    engine: str
    base_url: str | None
    default_model: str | None
    enabled: bool
    has_key: bool
    key_hint: str | None


class SettingsDTO(BaseModel):
    preferences: PreferencesDTO
    engines: list[EngineSettingDTO]


class EngineVerifyRequest(BaseModel):
    """A provider-appropriate verify probe (FR-SET-06). `key` is sent for a
    verify-only check and is never persisted by this call."""

    provider: str
    key: str | None = None
    base_url: str | None = None
    model: str | None = None


class EngineVerifyResult(BaseModel):
    ok: bool
    detail: str
    provider: str
    # Onboarding branches on this: `not_found` (install the CLI) vs
    # `not_logged_in` (open a terminal, log in) vs `error` (show detail). Only
    # `claude-cli` uses the non-`ok`/`error` values; every other provider maps
    # ok→"ok" / not-ok→"error".
    status: Literal["ok", "not_found", "not_logged_in", "error"] = "ok"


class EngineSettingUpsert(BaseModel):
    """Save/replace a provider's config. Omitting `key` leaves any existing
    sealed key intact; sending `key` re-seals. The key never round-trips back."""

    provider: str
    key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    enabled: bool = True


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


def profile_dto(profile: MasterProfile) -> ProfileDTO:
    return ProfileDTO.model_validate(profile)


def preferences_dto(prefs: UserPreferences) -> PreferencesDTO:
    return PreferencesDTO.model_validate(prefs)


def engine_setting_dto(row: EngineSettings) -> EngineSettingDTO:
    return EngineSettingDTO(
        id=row.id,
        engine=row.engine,
        base_url=row.base_url,
        default_model=row.default_model,
        enabled=row.enabled,
        has_key=row.key_ref is not None or row.key_encrypted is not None,
        key_hint=row.key_ref,
    )


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
