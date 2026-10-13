# voyager_py/tests/test_input_dynamics_on_lane.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: S311 — seeded PRNGs make the typing/scroll schedules reproducible.
"""Phase 4: human-input dynamics end to end on the broker's lane, against a
local fixture, never the network.

The unit tests can't reach the greenlet-thread coupling, so this runs through
`run_on_lane`: `human_type` renders the exact message with trusted keydowns,
`read_profile` fires real wheel events (Our Finding 6), and
`Pacer.wait_before_send` sleeps a ledger-derived gap, asserted with a pinned
delay. Both use Playwright's page API, opening no second CDP session. Anchors:
`run_on_lane`, NFR-LI-01."""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from sidecar.app.browser import BrowserBroker, BrowserLaunchError  # noqa: E402
from sidecar.packages.referral_outreach.upstream import pacing  # noqa: E402
from sidecar.packages.referral_outreach.upstream import scroll_dynamics as sd  # noqa: E402
from sidecar.packages.referral_outreach.upstream.pacing import (  # noqa: E402
    Pacer,
    resolve_profile,
)
from sidecar.packages.referral_outreach.upstream.session import human_type  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures"
SURFACE_SLUG = "input-dynamics"

# A de-headlessed Chrome UA, so the broker never spends a throwaway launch to
# resolve one (the pattern `test_broker_lane_connect_note.py` uses).
FAKE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Deliberately exercises the paths that plain lowercase never touches: an
# uppercase run held under one Shift ("ML"), shifted punctuation (':' and '!'),
# an unshifted apostrophe, digits, and repeated letters ("really") that make
# rollover available. The rendered value must survive every one of them.
MESSAGE = "Hi Ana, I'd really value 15 mins re: the ML role!"


async def _goto(surface, uri: str) -> None:
    """Point the surface at a LOCAL file on its own lane, bypassing the
    display-geometry gate `navigate()` enforces (a `page.goto` of a file:// URL
    needs no geometry, and this is not the network)."""
    await asyncio.wrap_future(
        surface.run_on_lane(
            lambda s: s.page.goto(uri, wait_until="domcontentloaded")
        )
    )


async def _evaluate(surface, expression: str):
    return await asyncio.wrap_future(
        surface.run_on_lane(lambda s: s.page.evaluate(expression))
    )


async def test_input_dynamics_run_on_the_broker_lane(tmp_path: Path) -> None:
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    surface = broker.surface(SURFACE_SLUG)
    try:
        try:
            await surface.wait_ready(timeout_seconds=60)
        except BrowserLaunchError as exc:  # pragma: no cover — CI without Chrome
            pytest.skip(f"real Chrome unavailable: {exc}")

        await _goto(surface, (_FIXTURES / "input_dynamics_surface.html").as_uri())

        # (1 + 3) Type the message on the lane. The inter-key WAITS are zeroed
        # (`sleep` is a no-op) so the test is quick, but every keyboard.down/up
        # and the rollover/Shift ORDER still dispatch for real on the surface
        # thread — the order is what determines the rendered text, not the waits.
        await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: human_type(
                    s.page.locator("#note"),
                    MESSAGE,
                    rng=random.Random(0x4A1),
                    sleep=lambda _s: None,
                )
            )
        )

        # (1) Character for character, including the shifted punctuation and the
        # rollover-prone runs.
        rendered = await _evaluate(surface, "document.querySelector('#note').value")
        assert rendered == MESSAGE

        # (1) The typed text is really on screen: the surface can be captured.
        shot = await asyncio.wrap_future(surface.screenshot())
        assert isinstance(shot, bytes) and len(shot) > 0

        # (3) Every keydown the page saw was a trusted event.
        keydown_count = await _evaluate(surface, "window.__keydownCount")
        untrusted = await _evaluate(surface, "window.__untrustedKeydowns")
        assert keydown_count > 0
        assert untrusted == 0

        # (2) Reading-scroll fires real wheel events on the lane. The notch count
        # is random per run; a no-op sleep keeps the cascade instant. The
        # load-bearing claim is Our Finding 6: we emit wheel events AT ALL.
        notches = await asyncio.wrap_future(
            surface.run_on_lane(
                lambda s: sd.read_profile(
                    s.page, rng=random.Random(0x5C0), sleep=lambda _s: None
                )
            )
        )
        assert notches > 0
        wheel_count = await _evaluate(surface, "window.__wheelCount")
        assert wheel_count > 0
    finally:
        broker.shutdown()


# --- (4) inter-send pacing: the gap is drawn from the ledger and slept ----------
# The real gap is floor plus Weibull capped at 900 s, so a 30 to 90 s band check
# fails about 19% of draws. These pin the draw and check what was slept, proving
# the mechanism deterministically.


def test_wait_before_send_sleeps_a_ledger_drawn_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the Weibull draw so the owed gap is a pure function of the ledger.
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 42.0)
    pacer = Pacer(resolve_profile(None), state_dir=tmp_path)
    sent_at = 1_000_000.0
    pacer.record_invite(now=sent_at)  # the ONLY thing the gap is measured from

    # 10 s after the send, the gap owed is (last_send + 42) - now = 32 s exactly,
    # and that is what gets slept — proving it comes off the ledger timestamp.
    slept: list[float] = []
    waited = pacer.wait_before_send(now=sent_at + 10.0, sleep=slept.append)
    assert waited == pytest.approx(32.0)
    assert slept == [pytest.approx(32.0)]

    # And it really sleeps wall-clock time: pin a tiny gap and let the real clock
    # run. One-sided lower bound with margin, so a busy CI can never flake it.
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 0.05)
    fresh = Pacer(resolve_profile(None), state_dir=tmp_path / "wall")
    now = 2_000_000.0
    fresh.record_invite(now=now)
    start = time.monotonic()
    real_wait = fresh.wait_before_send(now=now, sleep=time.sleep)
    elapsed = time.monotonic() - start
    assert real_wait == pytest.approx(0.05)
    assert elapsed >= 0.03


def test_wait_before_send_does_not_sleep_when_no_send_is_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pacing, "send_delay_seconds", lambda: 42.0)

    # No prior send: nothing to space from, so nothing is slept.
    empty = Pacer(resolve_profile(None), state_dir=tmp_path / "empty")
    slept: list[float] = []
    assert empty.wait_before_send(now=1_000_000.0, sleep=slept.append) == 0.0
    assert slept == []

    # A send long enough ago that the whole gap has already elapsed: still no
    # sleep, because the owed wait clamps at zero.
    elapsed_pacer = Pacer(resolve_profile(None), state_dir=tmp_path / "elapsed")
    sent_at = 1_000_000.0
    elapsed_pacer.record_invite(now=sent_at)
    slept.clear()
    assert elapsed_pacer.wait_before_send(now=sent_at + 100.0, sleep=slept.append) == 0.0
    assert slept == []
