"""Stock launch configuration for a core browser surface.

Deliberately unremarkable: installed Chrome through Playwright's defaults, the
sandbox on, the bundled Chromium as fallback. No argv, headers, identity or
geometry of ours, so it's the clean baseline every measurement is taken
against. Playwright's own debugging transport is upstream's; this adds none."""

from __future__ import annotations

import tempfile
from typing import Any

from ..logging_setup import get_logger


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
    reveals its exact version only there. A real launch, so it runs on a surface
    thread; the broker caches it, 1 launch per process."""
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


def _is_sandbox_launch_error(message: str) -> bool:
    # Conservative, mirroring the sync-Playwright path the referral package
    # already proved out: Chromium's sandbox-launch failures name the sandbox or
    # the missing kernel facility. Anything else is not a sandbox problem and
    # must not silently drop the sandbox.
    msg = message.lower()
    return "sandbox" in msg or "namespace" in msg


def open_persistent_context(playwright: Any, profile_dir: str, **kwargs: Any) -> Any:
    """Open a persistent context on `profile_dir`. 2 narrow fallbacks: the Chrome
    channel drops to bundled Chromium when absent, and the sandbox drops only on a
    sandbox-launch failure (a Linux host without user namespaces)."""
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
