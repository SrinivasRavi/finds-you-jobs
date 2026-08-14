"""Covers: the exception router (section 11, Our Claim 12).

A router, not a decision-maker. Recognized worker errors map deterministically to
their coded handler; the model is never called on that path. An unrecognized
error hard-stops, captures the DOM and a screenshot, and the model returns
exactly ONE diagnosis label from a fixed set — never an action.

ZERO real LLM, ZERO real browser: the engine is a canned fake, the surface is a
fixture whose `evaluate_isolated`/`screenshot` are set by the test.
"""

from __future__ import annotations

from concurrent.futures import Future

import pytest

from sidecar.app.registry.exception_router import (
    MODEL_DIAGNOSES,
    UNKNOWN,
    RoutedException,
    classify,
    route,
)
from sidecar.packages.referral_outreach.upstream import errors as up

from ..modules.networker.fakes import FakeEngine

# Each known upstream error class → the coded handler label it must route to.
KNOWN_CASES = [
    (up.AuthenticationError("401"), "auth"),
    (up.ProfileInaccessibleError("403"), "profile_inaccessible"),
    (up.SkipProfile("skip"), "skip_profile"),
    (up.ReachedConnectionLimit("weekly cap"), "connection_limit"),
    (up.RateLimited("999"), "rate_limited"),
    (up.BrowserUnresponsiveError("watchdog"), "browser_unresponsive"),
    (up.CapExceeded("self-cap"), "cap_exceeded"),
]


class FakeSurface:
    """A stand-in surface exposing the two captures the unknown path reads."""

    def __init__(
        self,
        *,
        dom: str = "<html><body>an unfamiliar page</body></html>",
        screenshot: bytes = b"\x89PNG\r\n\x1a\n-fake-image-bytes-",
    ) -> None:
        self._dom = dom
        self._screenshot = screenshot
        self.evaluated: list[str] = []

    def evaluate_isolated(self, expression: str) -> Future:
        self.evaluated.append(expression)
        future: Future = Future()
        future.set_result(self._dom)
        return future

    def screenshot(self) -> Future:
        future: Future = Future()
        future.set_result(self._screenshot)
        return future


# --- known errors: coded handlers, never the model -------------------------


@pytest.mark.parametrize(("exc", "label"), KNOWN_CASES)
def test_classify_maps_each_known_error(exc: Exception, label: str) -> None:
    assert classify(exc) == label


@pytest.mark.parametrize(("exc", "label"), KNOWN_CASES)
def test_route_known_error_uses_coded_handler_not_the_model(
    exc: Exception, label: str
) -> None:
    engine = FakeEngine()
    routed = route(exc, surface=FakeSurface(), engine=engine)
    assert routed.diagnosis == label
    assert routed.handled_by == "coded"
    assert routed.is_unknown is False
    # The coded handler acts; the model is never consulted for a recognized state.
    assert engine.seen == []
    # No evidence captured for a recognized state — that's the unknown path only.
    assert routed.dom is None
    assert routed.screenshot is None


def test_classify_unknown_error_returns_none() -> None:
    assert classify(ValueError("something new")) is None


# --- the unknown path: capture + one model diagnosis, never an action ------


def test_unknown_captures_dom_and_nonempty_screenshot() -> None:
    surface = FakeSurface()
    engine = FakeEngine(raw="LABEL: interface_drift\nRATIONALE: the Connect button moved")
    routed = route(ValueError("mystery modal"), surface=surface, engine=engine)

    assert routed.is_unknown is True
    assert routed.handled_by == "model"
    # DOM captured through the isolated world (invisible to the page).
    assert surface.evaluated == ["document.documentElement.outerHTML"]
    assert routed.dom == surface._dom
    # Screenshot captured, and its bytes are non-empty.
    assert routed.screenshot is not None
    assert len(routed.screenshot) > 0


def test_unknown_model_returns_exactly_one_diagnosis_never_an_action() -> None:
    engine = FakeEngine(raw="LABEL: interface_drift\nRATIONALE: a selector stopped matching")
    routed = route(RuntimeError("weird state"), surface=FakeSurface(), engine=engine)

    # Exactly one diagnosis, always from the fixed set.
    assert routed.diagnosis in MODEL_DIAGNOSES
    assert routed.diagnosis == "interface_drift"
    # The model was consulted exactly once, on the unknown path only.
    assert len(engine.seen) == 1
    # Never an action: the routed result carries a label + rationale, no callable
    # and no action field of any kind (Our Claim 12).
    assert not hasattr(routed, "action")
    assert isinstance(routed.rationale, str)


def test_unknown_out_of_set_label_clamps_to_unknown() -> None:
    """An answer outside the fixed vocabulary can never widen it."""
    engine = FakeEngine(raw="LABEL: click_the_button\nRATIONALE: just do it")
    routed = route(RuntimeError("x"), surface=FakeSurface(), engine=engine)
    assert routed.diagnosis == UNKNOWN


def test_unknown_without_engine_diagnoses_unknown_and_still_captures() -> None:
    """No engine → the stop still stands and the evidence is still captured; only
    the label is `unknown`."""
    surface = FakeSurface()
    routed = route(RuntimeError("x"), surface=surface, engine=None)
    assert routed.diagnosis == UNKNOWN
    assert routed.is_unknown is True
    assert routed.dom == surface._dom
    assert routed.screenshot is not None


def test_unknown_survives_a_surface_with_no_captures() -> None:
    """A missing surface never crashes the hard stop; captures come back None."""
    engine = FakeEngine(raw="LABEL: transient\nRATIONALE: blank render")
    routed = route(RuntimeError("x"), surface=None, engine=engine)
    assert routed.diagnosis == "transient"
    assert routed.dom is None
    assert routed.screenshot is None


def test_routed_exception_has_no_action_surface() -> None:
    """Structural guarantee: nothing the router hands back can be executed."""
    fields = RoutedException.__dataclass_fields__
    assert "action" not in fields
    assert set(fields) == {"diagnosis", "handled_by", "rationale", "dom", "screenshot"}
