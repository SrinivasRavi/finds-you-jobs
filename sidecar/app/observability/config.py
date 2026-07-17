"""Parse the observability block out of `UserPreferences.ui_state`.

The observability settings (content logging + OTLP export opt-in + retention)
live under `ui_state` alongside theme — there is no separate observability table
in P1 (US-SYS-05: the SDK owns its own log; the *config* is a handful of prefs).
One parser, shared by app startup (main.py) and the Settings-change reconfigure
path (routes.py), so the two never drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .setup import DEFAULT_RETENTION_DAYS


@dataclass
class ObservabilitySettings:
    content_logging: bool = False
    otlp_enabled: bool = False
    otlp_endpoint: str = ""
    otlp_headers: dict[str, str] = field(default_factory=dict)
    retention_days: int = DEFAULT_RETENTION_DAYS


def observability_config(ui_state: dict[str, Any] | None) -> ObservabilitySettings:
    """Extract the observability settings from a prefs `ui_state` dict.

    Defaults are the safe, no-network baseline: content logging off, OTLP export
    off, 30-day retention. A malformed/partial `ui_state` degrades to defaults —
    observability config must never crash the sidecar.
    """
    ui = ui_state or {}
    raw_headers = ui.get("otlp_headers")
    headers: dict[str, str] = {}
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}

    try:
        retention = int(ui.get("retention_days", DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        retention = DEFAULT_RETENTION_DAYS

    return ObservabilitySettings(
        content_logging=bool(ui.get("content_logging", False)),
        otlp_enabled=bool(ui.get("otlp_enabled", False)),
        otlp_endpoint=str(ui.get("otlp_endpoint", "") or ""),
        otlp_headers=headers,
        retention_days=retention,
    )
