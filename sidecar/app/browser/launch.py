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

from typing import Any

from ..logging_setup import get_logger


def stock_launch_kwargs() -> dict[str, Any]:
    """The launch configuration the broker uses, and the whole of it."""
    return {"headless": True, "channel": "chrome", "chromium_sandbox": True}


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
