# finds-you-jobs — AGPL-3.0-only.
"""Tool-vocabulary strictness (applier.md §4.2): parse_action accepts exactly
the schema and nothing else — and the schema contains no submit."""

import pytest

from sidecar.packages.jobapplier.actions import TOOLS, parse_action
from sidecar.packages.jobapplier.types import DisallowedActionError


def test_no_submit_tool_exists() -> None:
    # The P1 safety line: a prompt mistake cannot expose a submit capability
    # because the vocabulary itself has no such tool (§4.2).
    assert "submit" not in TOOLS
    with pytest.raises(DisallowedActionError, match="unknown tool"):
        parse_action('{"tool": "submit"}')


def test_valid_fill_parses() -> None:
    action = parse_action('{"tool": "fill", "element_id": "e3", "value": "Ada"}')
    assert action.tool == "fill"
    assert action.args == {"element_id": "e3", "value": "Ada"}


def test_reply_with_prose_around_json_parses() -> None:
    action = parse_action('Sure — here is my action:\n{"tool": "click", "element_id": "e1"}')
    assert action.tool == "click"


@pytest.mark.parametrize(
    "reply",
    [
        "I would click the button",  # no JSON at all
        '{"tool": "fill", "element_id": "e3"}',  # missing required value
        '{"tool": "fill", "element_id": "e3", "value": "x", "css": "#a"}',  # extra arg
        '{"tool": "eval", "js": "alert(1)"}',  # no eval tool
        '{"tool": "wait", "seconds": 600}',  # unbounded wait
        '{"tool": "wait", "seconds": -1}',
        '{"tool": "scroll", "direction": "sideways"}',
        '{"tool": "fill", "element_id": "e1", "value": ' + '"' + "x" * 5000 + '"}',
    ],
)
def test_disallowed_replies_rejected(reply: str) -> None:
    with pytest.raises(DisallowedActionError):
        parse_action(reply)


def test_report_blocked_optional_field_label() -> None:
    action = parse_action(
        '{"tool": "report_blocked", "kind": "ungrounded_field", '
        '"detail": "no salary fact", "field_label": "Desired salary"}'
    )
    assert action.args["field_label"] == "Desired salary"
