# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""The constrained tool vocabulary (docs/internal/applier.md §4.2).

These are the ONLY things the model can ask for. There is deliberately no
``submit`` here — it must not exist in the P1 schema so that no prompt
mistake can ever expose a submit capability. There is also no JavaScript
eval, no CSS/XPath injection, no filesystem path, no cookie access: an
action references an opaque per-observation element id and a value, nothing
else.

``parse_action`` is strict: the model's reply must be one JSON object with a
known ``tool`` and exactly the arguments that tool takes. Anything else is a
``DisallowedActionError`` — the loop reports it and re-prompts rather than
guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .types import DisallowedActionError

# tool name -> (required args, optional args)
TOOLS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "click": (frozenset({"element_id"}), frozenset()),
    "navigate": (frozenset({"url"}), frozenset()),
    "scroll": (frozenset({"direction"}), frozenset({"amount"})),
    "wait": (frozenset({"seconds"}), frozenset({"reason"})),
    "fill": (frozenset({"element_id", "value"}), frozenset()),
    "select": (frozenset({"element_id", "option"}), frozenset()),
    "check": (frozenset({"element_id"}), frozenset()),
    "upload_artifact": (frozenset({"element_id", "artifact_id"}), frozenset()),
    "finish": (frozenset({"reason"}), frozenset()),
    "report_blocked": (frozenset({"kind", "detail"}), frozenset({"field_label"})),
}

_MAX_VALUE_LEN = 4000  # a form answer, not an essay dump
_MAX_WAIT_S = 15.0


@dataclass(frozen=True)
class Action:
    """One validated tool call."""

    tool: str
    args: dict[str, str | float]


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def render_tool_schema() -> str:
    """The tool list as shown to the model (prompt.py embeds it)."""
    lines = []
    for name, (required, optional) in TOOLS.items():
        args = ", ".join(sorted(required))
        if optional:
            args += ", [" + ", ".join(sorted(optional)) + "]"
        lines.append(f"- {name}({args})")
    return "\n".join(lines)


def parse_action(reply: str) -> Action:
    """Parse + validate one model reply into an Action. Strict by design."""
    match = _JSON_BLOCK.search(reply)
    if match is None:
        raise DisallowedActionError("reply contained no JSON action object")
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise DisallowedActionError(f"action was not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DisallowedActionError("action must be a JSON object")

    tool = raw.get("tool")
    if not isinstance(tool, str) or tool not in TOOLS:
        raise DisallowedActionError(f"unknown tool {tool!r}")
    required, optional = TOOLS[tool]

    args: dict[str, str | float] = {}
    for key, value in raw.items():
        if key == "tool":
            continue
        if key not in required and key not in optional:
            raise DisallowedActionError(f"{tool} does not take argument {key!r}")
        if key in {"seconds", "amount"}:
            try:
                args[key] = float(value)
            except (TypeError, ValueError) as exc:
                raise DisallowedActionError(f"{key} must be a number") from exc
        else:
            if not isinstance(value, str):
                raise DisallowedActionError(f"{key} must be a string")
            if len(value) > _MAX_VALUE_LEN:
                raise DisallowedActionError(f"{key} exceeds {_MAX_VALUE_LEN} chars")
            args[key] = value
    missing = required - args.keys()
    if missing:
        raise DisallowedActionError(f"{tool} missing required {sorted(missing)}")

    if tool == "wait":
        seconds = float(args["seconds"])
        if not (0 < seconds <= _MAX_WAIT_S):
            raise DisallowedActionError(f"wait seconds must be in (0, {_MAX_WAIT_S}]")
    if tool == "scroll" and args["direction"] not in {"up", "down"}:
        raise DisallowedActionError("scroll direction must be 'up' or 'down'")
    return Action(tool=tool, args=args)
