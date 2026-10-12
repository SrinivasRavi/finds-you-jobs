"""The Python constants the frontend keeps its own copy of, published into the
OpenAPI schema as literal types (audit finding 11, `simplification.md` D-F16).

`pnpm codegen` turns each into a TypeScript literal type, and each frontend
copy is declared with that type, so changing a value on 1 side and not the
other is a compile error. Sets become unions, read with `Record<U, true>`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL
from sidecar.modules.scorer.weights import JEV_WEIGHTS, PRESET_LOCATION_PENALTY, PRESETS

from .doc_extract import SUPPORTED_SUFFIXES
from .documents import ALLOWED_TYPES
from .lifecycle import LIFECYCLE_DEFAULTS
from .registry.engine_config import CLI_PROVIDERS
from .registry.operations import CANCELLABLE_RUNNING_KINDS
from .registry.persistence import SCORING_MODES
from .runner.policy import BROWSER_LANE_KINDS


def _literal(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {k: _literal(v) for k, v in value.items()},
            "required": sorted(value),
            "additionalProperties": False,
        }
    kind = "string" if isinstance(value, str) else "number"
    return {"type": kind, "enum": [value]}


def _union(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(values)}


def shared_constants_schema() -> dict[str, Any]:
    fields = {
        "preset_location_penalty": _literal(PRESET_LOCATION_PENALTY),
        "preset_weights": _literal(PRESETS),
        "jev_weights": _literal(JEV_WEIGHTS),
        "lifecycle_defaults": _literal(LIFECYCLE_DEFAULTS),
        "claude_cli_default_model": _literal(DEFAULT_MODEL),
        "scoring_mode": _union(SCORING_MODES),
        "score_weight_preset": _union(PRESETS),
        "cancellable_running_kind": _union(CANCELLABLE_RUNNING_KINDS),
        "browser_lane_kind": _union(BROWSER_LANE_KINDS),
        "cli_provider": _union(CLI_PROVIDERS),
        "attach_types": _literal(ALLOWED_TYPES),
        "extract_suffix": _union(SUPPORTED_SUFFIXES),
    }
    return {
        "type": "object",
        "properties": fields,
        "required": sorted(fields),
        "additionalProperties": False,
    }


def register_shared_constants(openapi_schema: dict[str, Any]) -> None:
    components = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    components.setdefault("SharedConstants", shared_constants_schema())
