"""Stock launch configuration for a core browser surface.

Deliberately unremarkable. Real installed Chrome through Playwright's own
defaults, the Chromium sandbox on, and the bundled Chromium build as the
fallback when no Chrome channel is installed. Nothing here shapes what a page
observes: no argv of ours, no headers, no identity or geometry overrides. That
is the point of this module — it is the stock baseline every later measurement
is taken against, so it stays greppable-clean and a switch that isn't here is a
switch nobody added by accident.

Playwright authors its own debugging transport when it launches a browser. That
is upstream's business, not ours, and this file never adds one.
"""

from __future__ import annotations

import tempfile
from typing import Any

from ..logging_setup import get_logger


def stock_launch_kwargs() -> dict[str, Any]:
    """The launch configuration the broker uses, and the whole of it."""
    return {"headless": True, "channel": "chrome", "chromium_sandbox": True}


# Automation quality-of-life switches Playwright sets by default and a person
# never would (Our Finding 23). Dropping them leaves the process argv reading
# like a hand-launched Chrome. `ignore_default_args` is an EXACT-string filter —
# Playwright runs `defaultArgs().filter(arg => ignore.indexOf(arg) === -1)` — so
# every entry has to be the switch verbatim; a prefix does not match.
#
# `--enable-automation` leads the list because the transport policy names it
# (`docs/internal/plugin-architecture.md` section 12.3) and because a future
# Playwright could add it back. It is a no-op on the pinned Playwright, which
# does not emit it (measured); the switch that actually clears
# `navigator.webdriver` is the blink flag in `minimal_launch_kwargs`, not this.
#
# Deliberately NOT listed: Playwright's `--disable-features=` / `--enable-features=`
# bundles. Their values are version-specific comma lists, and an exact-match
# filter against a value that shifts on a Playwright bump would silently STOP
# dropping them — a switch reappearing that nobody re-added, the opposite of what
# this module promises. They do not affect webdriver or UA coherence (measured),
# so they stay untouched rather than dropped by a brittle exact string.
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
    """The de-headlessed Chrome UA, read once from a throwaway stock launch.

    Chrome only reveals its exact version in the UA it reports, so we launch it,
    read `navigator.userAgent`, de-headless it, and throw the browser away. This
    is a real Chrome launch, so it runs on a surface thread, never the serving
    loop. The broker caches the result: one throwaway launch per process.
    """
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="fyj-ua-") as tmp:
        playwright = sync_playwright().start()
        try:
            context = open_persistent_context(playwright, tmp, **stock_launch_kwargs())
            try:
                page = context.pages[0] if context.pages else context.new_page()
                user_agent = str(page.evaluate("navigator.userAgent"))
            finally:
                context.close()
        finally:
            playwright.stop()
    return deheadless_user_agent(user_agent)


def minimal_launch_kwargs(user_agent: str) -> dict[str, Any]:
    """The guardrailed launch: stock Chrome minus the automation switches, plus a
    de-headlessed identity nailed on at the process level.

    The user agent is a LAUNCH flag, never a CDP override. The CDP override
    (`Emulation.setUserAgentOverride`) fixes the page but leaves the surface's
    service worker still reporting "HeadlessChrome" with its client-hint brands
    blanked to `[]`; the `--user-agent` flag is process-global, so it beats the
    worker into existence with a coherent identity too (measured, both surfaces).

    `--disable-blink-features=AutomationControlled` is what actually clears
    `navigator.webdriver` on the pinned Chrome: it is set by the headless run
    itself, not by `--enable-automation` (absent from Playwright's defaults), so
    the drop list alone leaves webdriver true. This one flag flips it, page and
    worker alike (measured). It is not a forbidden switch (section 12.3).

    Playwright's own debugging pipe is left exactly as Playwright authors it —
    neither added here nor listed to drop — because it is Playwright's transport.
    """
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


def _is_sandbox_launch_error(message: str) -> bool:
    # Conservative, mirroring the sync-Playwright path the referral package
    # already proved out: Chromium's sandbox-launch failures name the sandbox or
    # the missing kernel facility. Anything else is not a sandbox problem and
    # must not silently drop the sandbox.
    msg = message.lower()
    return "sandbox" in msg or "namespace" in msg


def open_persistent_context(playwright: Any, profile_dir: str, **kwargs: Any) -> Any:
    """Open a persistent context on `profile_dir` with the launch discipline the
    existing sync-Playwright path established.

    Two fallbacks, both narrow. The installed-Chrome channel drops to the
    bundled Chromium build when that channel is absent. The sandbox drops off
    only for a sandbox-launch failure, which in practice means a Linux host
    without unprivileged user namespaces; desktop macOS and Windows always keep
    it on.
    """
    log = get_logger()
    channel = kwargs.pop("channel", None)
    sandbox = bool(kwargs.pop("chromium_sandbox", True))

    def _open(*, use_channel: bool, use_sandbox: bool) -> Any:
        extra = {"channel": channel} if use_channel and channel else {}
        return playwright.chromium.launch_persistent_context(
            profile_dir, chromium_sandbox=use_sandbox, **extra, **kwargs
        )

    def _sandboxed(*, use_channel: bool) -> Any:
        try:
            return _open(use_channel=use_channel, use_sandbox=sandbox)
        except Exception as exc:  # noqa: BLE001 — non-sandbox failures re-raise
            if not sandbox or not _is_sandbox_launch_error(str(exc)):
                raise
            log.warning(
                "Chromium sandbox unavailable (%s) — retrying with it off; "
                "expected only on Linux without unprivileged user namespaces", exc
            )
            return _open(use_channel=use_channel, use_sandbox=False)

    try:
        return _sandboxed(use_channel=True)
    except Exception as exc:  # noqa: BLE001 — no installed Chrome → bundled build
        if not channel:
            raise
        log.info("installed Chrome unavailable (%s) — using bundled Chromium", exc)
        return _sandboxed(use_channel=False)
