"""Covers: the Applier's 4 safety guards in decide.py (backlog S-A9), each a
pure function over 1 field, table-driven, no browser.

  _is_password_or_otp  a credential or one-time code is never filled
  _is_hidden           a field the user can't see is never filled
  _is_trap             a honeypot, captcha or payload field is never filled
  _ungrounded          no answer is invented: a required field with no
                       grounded answer stops the run and says so
"""

from __future__ import annotations

from typing import Any

import pytest

from sidecar.packages.jobapplier.decide import (
    DecideState,
    Field,
    _field_required,
    _hay,
    _is_hidden,
    _is_password_or_otp,
    _is_trap,
    _trap_hay,
    _ungrounded,
)
from sidecar.packages.jobapplier.observe import ObservedElement


def _field(
    label: str = "", *, interactable: bool = True, extra: dict[str, str] | None = None, **attrs: Any
) -> Field:
    """A text field built the way `_collect_fields` builds one."""
    el = ObservedElement(
        element_id="e1",
        unique_id="u1",
        tag="input",
        interactable=interactable,
        frame_index=0,
        label=label,
        attributes={"type": "text", **attrs, **(extra or {})},
    )
    return Field(
        key="k#0",
        kind="text",
        label=label,
        attr_hay=_hay(el),
        trap_hay=_trap_hay(el, label),
        required=_field_required((el,), label),
        options=(),
        elements=(el,),
    )


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (_field("Password", type="password"), True),
        (_field("Code", autocomplete="one-time-code"), True),
        (_field("Code", name="otp"), True),
        (_field("Verification code", id="verification-code"), True),
        (_field("Email", name="email", type="email"), False),
        (_field("Phone", name="phone"), False),
    ],
)
def test_password_and_one_time_code_fields_are_refused(field: Field, expected: bool) -> None:
    assert _is_password_or_otp(field) is expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (_field("x", type="hidden"), True),
        (_field("x", extra={"aria-hidden": "true"}), True),
        (_field("x", interactable=False), True),
        (_field("x", style="display: none"), True),
        (_field("x", style="visibility:hidden;"), True),
        (_field("First name", name="first_name"), False),
    ],
)
def test_fields_the_user_cannot_see_are_refused(field: Field, expected: bool) -> None:
    assert _is_hidden(field) is expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        (_field("", name="hp_website"), True),
        (_field("Leave this field blank", name="extra"), True),
        (_field("", name="honeypot"), True),
        # A captcha or a parsed-resume payload is a trap even when required.
        (_field("", name="g-recaptcha-response", required=True), True),
        (_field("", name="resume-text", required=True), True),
        # Tiebreak: a visible required field is never a honeypot.
        (_field("Website", name="hp_website", required=True), False),
        (_field("Email", name="email"), False),
    ],
)
def test_traps_are_refused(field: Field, expected: bool) -> None:
    assert _is_trap(field) is expected


def test_a_required_field_with_no_grounded_answer_stops_the_run() -> None:
    state = DecideState()
    action = _ungrounded(_field("Salary expectation", name="salary", required=True), state)
    assert action is not None and action.tool == "report_blocked"
    assert action.args["kind"] == "ungrounded_field"
    assert action.args["field_label"] == "Salary expectation"
    assert (state.declined, state.reported) == (1, 1)


def test_an_optional_field_with_no_grounded_answer_is_left_blank() -> None:
    state = DecideState()
    assert _ungrounded(_field("Pronouns", name="pronouns"), state) is None
    assert (state.declined, state.reported) == (1, 0)
