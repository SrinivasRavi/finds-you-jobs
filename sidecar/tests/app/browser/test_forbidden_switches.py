"""Covers: the transport policy's forbidden-switch guardrail
(`docs/internal/plugin-architecture.md` section 12.3).

The guardrailed launch never authors a debugging port, a debugging pipe, an
explicit --headless, or --enable-automation into its own args; --enable-automation
IS listed to drop from Playwright's defaults; and the de-headlessed identity
rides a --user-agent launch flag. Belt and braces: the source of every module
that drives the surface is grepped for the forbidden literals, scoped to what OUR
code authors (the accepted rescoping of section 12.3's "greps the driver").
"""

from __future__ import annotations

from pathlib import Path

from sidecar.app.api import screencast_ws as screencast_ws_module
from sidecar.app.browser import broker as broker_module
from sidecar.app.browser import launch as launch_module
from sidecar.app.browser import minimal_launch_kwargs

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Each re-acquires an automation artifact the surface otherwise avoids. The
# debugging pipe is the one accepted residue and Playwright's own to author, so
# it is not on the list we forbid OURSELVES from writing (it never appears in our
# args), and it is what the source grep proves our code never authors.
FORBIDDEN_IN_ARGS = (
    "--remote-debugging-port",
    "--remote-debugging-pipe",
    "--headless",
    "--enable-automation",
)
FORBIDDEN_IN_SOURCE = (
    "Runtime.enable",
    "--remote-debugging-port",
    "--remote-debugging-pipe",
)


def test_the_launch_args_author_no_forbidden_switch() -> None:
    args = minimal_launch_kwargs(CHROME_UA)["args"]
    for switch in FORBIDDEN_IN_ARGS:
        assert not any(arg.startswith(switch) for arg in args), switch


def test_enable_automation_is_dropped_from_the_defaults() -> None:
    assert "--enable-automation" in minimal_launch_kwargs(CHROME_UA)["ignore_default_args"]


def test_the_user_agent_flag_carries_a_de_headlessed_chrome() -> None:
    args = minimal_launch_kwargs(CHROME_UA)["args"]
    ua_flags = [arg for arg in args if arg.startswith("--user-agent=")]
    assert ua_flags == [f"--user-agent={CHROME_UA}"]
    assert "HeadlessChrome" not in ua_flags[0]
    assert "Chrome/" in ua_flags[0]


def test_no_driver_module_authors_a_forbidden_literal() -> None:
    for module in (launch_module, broker_module, screencast_ws_module):
        path = Path(str(module.__file__))
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_IN_SOURCE:
            assert needle not in text, f"{needle} in {path.name}"
