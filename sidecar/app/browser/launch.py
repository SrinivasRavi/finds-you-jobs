"""Stock launch configuration for a core browser surface.

Deliberately unremarkable: installed Chrome through Playwright's defaults, the
sandbox on, the bundled Chromium as fallback. No argv, headers, identity or
geometry of ours, so it's the clean baseline every measurement is taken
against. Playwright's own debugging transport is upstream's; this adds none."""

from __future__ import annotations

import tempfile
from typing import Any

from .host import get_host, launch


def stock_launch_kwargs() -> dict[str, Any]:
    """The launch configuration the broker uses, and the whole of it."""
    return {"headless": True, "channel": "chrome", "chromium_sandbox": True}


# Automation switches Playwright sets that a person never would (Our Finding 23).
# `ignore_default_args` matches exact strings, so each entry is verbatim.
# `--enable-automation` is named by the transport policy (plugin-architecture.md
# section 12.3); it's a no-op on the pinned Playwright. The version-specific
# feature bundles stay: an exact match would silently stop working on a bump.
_AUTOMATION_SWITCHES_TO_DROP = [
    "--enable-automation",
    "--disable-background-networking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-sync",
    "--metrics-recording-only",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--enable-blink-features=IdleDetection",
    "--export-tagged-pdf",
    "--disable-search-engine-choice-screen",
]


def deheadless_user_agent(user_agent: str) -> str:
    """A headless Chrome UA rewritten as its headful twin. Chrome's default UA
    carries "HeadlessChrome"; a real install carries "Chrome"."""
    return user_agent.replace("HeadlessChrome", "Chrome")


def resolve_user_agent() -> str:
    """The de-headlessed Chrome UA, read from a throwaway stock launch, since Chrome
    reveals its exact version only there. A real launch on the host, waited on from
    a surface thread; the broker caches it, 1 launch per process."""
    host = get_host()

    async def _read() -> str:
        with tempfile.TemporaryDirectory(prefix="fyj-ua-") as tmp:
            pw = await host.playwright()
            context = await launch(pw.chromium, tmp, **stock_launch_kwargs())
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                return str(await page.evaluate("navigator.userAgent"))
            finally:
                await context.close()

    return deheadless_user_agent(host.run(_read))


def minimal_launch_kwargs(user_agent: str) -> dict[str, Any]:
    """The guardrailed launch: stock Chrome minus the automation switches, plus a
    de-headlessed UA as a launch flag (a CDP override leaves the service worker
    saying HeadlessChrome). `--disable-blink-features=AutomationControlled` is what
    clears `navigator.webdriver` (measured; not forbidden, section 12.3).
    Playwright's debugging pipe is left as Playwright authors it."""
    return {
        "headless": True,
        "channel": "chrome",
        "chromium_sandbox": True,
        "ignore_default_args": list(_AUTOMATION_SWITCHES_TO_DROP),
        "args": [
            f"--user-agent={user_agent}",
            "--disable-blink-features=AutomationControlled",
        ],
    }
