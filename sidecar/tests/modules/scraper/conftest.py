"""Shared scraper-test fixtures.

The F-M9 politeness machinery is real-time behavior: the jittered inter-page
pause would slow every pagination test, and the in-memory 429 cool-down table
is process-global state that would leak between tests. Zero the pause and
reset the table around every test; tests that exercise the machinery itself
patch in their own recorders.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sidecar.modules.scraper import http


@pytest.fixture(autouse=True)
def _fast_pause_and_fresh_cooldowns(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(http, "PAGE_PAUSE_RANGE_S", (0.0, 0.0))
    http._cooldowns.clear()
    yield
    http._cooldowns.clear()
