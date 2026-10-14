"""The shared Applier window's lifecycle (docs/internal/applier/window.md section 4.4):
the idle close and a starting run never interleave, and no profile outlives its window."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sidecar.app.registry.applier_window import ApplierWindow


def test_a_run_starting_as_the_idle_close_fires_keeps_its_tab() -> None:
    """The idle close stopped Playwright under a window a new run had just launched,
    and deleted that window's profile, so the run's tab died at birth."""
    window = ApplierWindow(headless=True)
    try:
        window.run(window.open_tab("first"))
        window.run(window.close_tab("first"))

        async def race() -> object:
            _, page = await asyncio.gather(window._close_if_idle(), window.open_tab("second"))
            return page

        page = window.run(race())
        assert not page.is_closed()  # type: ignore[attr-defined]
        window.run(page.goto("about:blank"))  # type: ignore[attr-defined]
        assert window._profile is not None and Path(window._profile).is_dir()
    finally:
        window.shutdown()


def test_a_window_the_user_quit_leaves_no_profile_behind() -> None:
    window = ApplierWindow(headless=True)
    try:
        window.run(window.open_tab("first"))
        first_profile = window._profile
        assert first_profile is not None
        window.run(window._context.close())  # the user quits the window
        window.run(window.close_tab("first"))
        window.run(window.open_tab("second"))  # the next run launches a new one
        assert window._profile != first_profile
        assert not Path(first_profile).exists()
    finally:
        window.shutdown()
