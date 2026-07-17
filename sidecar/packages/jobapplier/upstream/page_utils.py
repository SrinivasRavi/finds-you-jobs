# jobapplier.upstream.page_utils — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
#
# Trimmed fork of Skyvern @ 28db09cb (skyvern/webeye/utils/page.py). Only the
# SkyvernFrame subset the carried observation core calls is taken, plus the
# viewport-screenshot helper.
# Kept: load_js_script (repointed), the JS-evaluate path (SkyvernFrame.evaluate,
#   _evaluate_with_navigation_recovery, _dispatch_evaluate, _wait_for_navigation_settle,
#   _is_navigation_context_lost), SkyvernFrame.create_instance / __init__ / get_frame /
#   _set_enriched_element_tree_flag / build_tree_from_body, and the viewport screenshot
#   path (ScreenshotMode, _wait_for_screenshot_load_state, _page_screenshot_helper,
#   _current_viewpoint_screenshot_helper).
# Dropped: everything else on SkyvernFrame (scrolling/split screenshots, PIL image
#   merge, get_content, scroll/box/DOM-depth/select-option/etc. helpers, incremental
#   tree, page-ready waits) and the cursor-overlay machinery — none reached by the
#   observation core.
#
# Trims:
#   - structlog -> stdlib logging (see constants.get_logger()).
#   - OpenTelemetry (otel_trace, @traced, context attrs) -> removed.
#   - skyvern.config / SettingsManager -> module-level constants with the SAME
#     upstream defaults (named after each setting).
#   - skyvern_context removed: _set_enriched_element_tree_flag hardcodes the
#     enriched-tree flag to its default (off).
#   - main-world CDP eval hook removed: _dispatch_evaluate keeps only the direct
#     per-frame evaluate branch (the one that runs when no main-world prefix is
#     configured — none ever is here), dropping is_page_like / main_world_eval.
#   - PIL removed with the scrolling/split-screenshot paths (not on the viewport path).
#   - cursor-visualization gating removed from _page_screenshot_helper
#     (BROWSER_CURSOR_VISUALIZATION default-off; avoids carrying cursorOverlay.js).
#   - load_js_script repointed to this package's domUtils.js via __file__.
"""Playwright SkyvernFrame subset carried from Skyvern for observation."""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._errors import TimeoutError
from playwright.async_api import Frame, Page

from .constants import get_logger
from .exceptions import FailedToTakeScreenshot

LOG = get_logger()

# skyvern.config defaults, carried verbatim (trim: SettingsManager -> constants).
BROWSER_ACTION_TIMEOUT_MS = 5000
BROWSER_SCREENSHOT_TIMEOUT_MS = 20000
BROWSER_SCREENSHOT_LOAD_STATE_TIMEOUT_MS = 5000
BROWSER_SCRAPING_BUILDING_ELEMENT_TREE_TIMEOUT_MS = 60 * 1000  # 1 minute
ENABLE_EXP_ALL_TEXTUAL_ELEMENTS_INTERACTABLE = False


def load_js_script() -> str:
    # Repointed to this package's carried domUtils.js (upstream loaded it from
    # SKYVERN_DIR/webeye/scraper/domUtils.js).
    path = str(Path(__file__).parent / "domUtils.js")
    try:
        # TODO: Implement TS of domUtils.js and use the complied JS file instead of the raw JS file.
        # This will allow our code to be type safe.
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        LOG.exception("Failed to load the JS script", path=path)
        raise e


JS_FUNCTION_DEFS = load_js_script()

_NAVIGATION_RECOVERY_MAX_ATTEMPTS = 4
_NAVIGATION_SETTLE_TIMEOUT_MS = 3000


def _is_navigation_context_lost(error_msg: str) -> bool:
    if "Execution context was destroyed" in error_msg:
        return True
    return "ReferenceError" in error_msg and "is not defined" in error_msg


async def _dispatch_evaluate(frame: Page | Frame, expression: str, arg: Any | None) -> Any:
    # Trim: upstream routed page evaluations through a main-world CDP hook when a
    # per-context prefix was configured (anti-bot middleware). No prefix is ever
    # configured in the observation core, so the branch that ran was the direct
    # per-frame evaluate below; the main-world path and is_page_like are dropped.
    return await frame.evaluate(expression=expression, arg=arg)


async def _wait_for_navigation_settle(frame: Page | Frame, timeout_ms: float) -> None:
    if timeout_ms <= 0:
        return
    try:
        await frame.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightError:
        return


async def _wait_for_screenshot_load_state(page: Page, timeout_ms: float) -> None:
    # Best-effort readiness guard before capturing. 'domcontentloaded' fires far
    # earlier than 'load'; pages with streaming/long-polling/SSE/websockets or a
    # persistent spinner may never fire 'load', so a timeout here must be
    # non-fatal — the capture has its own (separate) timeout budget.
    if timeout_ms <= 0:
        return
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except (PlaywrightError, TimeoutError):
        LOG.warning("Page did not reach domcontentloaded before screenshot; capturing current state anyway")


class ScreenshotMode(StrEnum):
    LITE = "lite"
    DETAILED = "detailed"


async def _page_screenshot_helper(
    page: Page,
    file_path: str | None = None,
    full_page: bool = False,
    timeout: float = BROWSER_SCREENSHOT_TIMEOUT_MS,
) -> bytes:
    try:
        return await page.screenshot(
            path=file_path,
            timeout=timeout,
            full_page=full_page,
            animations="disabled",
        )
    except TimeoutError as timeout_error:
        LOG.info(
            f"Timeout error while taking screenshot: {str(timeout_error)}. Going to take a screenshot again with animation allowed."
        )
        return await page.screenshot(
            path=file_path,
            timeout=timeout,
            full_page=full_page,
            animations="allow",
        )


async def _current_viewpoint_screenshot_helper(
    page: Page,
    file_path: str | None = None,
    full_page: bool = False,
    timeout: float = BROWSER_SCREENSHOT_TIMEOUT_MS,
    mode: ScreenshotMode = ScreenshotMode.DETAILED,
) -> bytes:
    if page.is_closed():
        raise FailedToTakeScreenshot(error_message="Page is closed")

    # Capture page context for debugging screenshot issues
    url = page.url
    try:
        viewport = page.viewport_size
        viewport_info = f"{viewport['width']}x{viewport['height']}" if viewport else "unknown"
    except Exception:
        viewport_info = "unknown"

    try:
        if mode == ScreenshotMode.DETAILED:
            await _wait_for_screenshot_load_state(
                page, timeout_ms=BROWSER_SCREENSHOT_LOAD_STATE_TIMEOUT_MS
            )
        start_time = time.time()
        screenshot: bytes = b""
        if file_path:
            screenshot = await _page_screenshot_helper(
                page=page, file_path=file_path, full_page=full_page, timeout=timeout
            )
        else:
            screenshot = await _page_screenshot_helper(page=page, full_page=full_page, timeout=timeout)
        end_time = time.time()
        LOG.debug(
            "Screenshot taking time",
            screenshot_time=end_time - start_time,
            file_path=file_path,
        )
        return screenshot
    except TimeoutError as e:
        LOG.error(
            "Screenshot timeout",
            timeout_ms=timeout,
            url=url,
            viewport=viewport_info,
            full_page=full_page,
            mode=mode.value if hasattr(mode, "value") else str(mode),
            error=str(e),
        )
        raise FailedToTakeScreenshot(error_message=str(e)) from e
    except Exception as e:
        LOG.error(
            "Screenshot failed",
            url=url,
            viewport=viewport_info,
            full_page=full_page,
            error=str(e),
            exc_info=True,
        )
        raise FailedToTakeScreenshot(error_message=str(e)) from e


class SkyvernFrame:
    @staticmethod
    async def evaluate(
        frame: Page | Frame,
        expression: str,
        arg: Any | None = None,
        timeout_ms: float = BROWSER_ACTION_TIMEOUT_MS,
    ) -> Any:
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                return await _dispatch_evaluate(frame, expression, arg)
        except PlaywrightError as e:
            error_msg = str(e)
            if not _is_navigation_context_lost(error_msg):
                raise
            return await SkyvernFrame._evaluate_with_navigation_recovery(
                frame=frame,
                expression=expression,
                arg=arg,
                timeout_ms=timeout_ms,
                initial_error=error_msg,
            )
        except RuntimeError as e:
            # `evaluate_in_main_world` raises RuntimeError on Runtime.evaluate
            # exception payloads; only navigation-context-lost text recovers here.
            error_msg = str(e)
            if not _is_navigation_context_lost(error_msg):
                raise
            return await SkyvernFrame._evaluate_with_navigation_recovery(
                frame=frame,
                expression=expression,
                arg=arg,
                timeout_ms=timeout_ms,
                initial_error=error_msg,
            )
        except asyncio.TimeoutError:
            # Re-raised and handled by the caller (scrape retries / failure classification),
            # so this is not the failure boundary; log without a traceback at warning.
            LOG.warning("Skyvern timed out trying to analyze the page", expression=expression)
            raise TimeoutError("Skyvern timed out trying to analyze the page")

    @staticmethod
    async def _evaluate_with_navigation_recovery(
        frame: Page | Frame,
        expression: str,
        arg: Any | None,
        timeout_ms: float,
        initial_error: str,
    ) -> Any:
        # Multi-hop SSO/OIDC flows (especially response_mode=form_post) can destroy
        # the JS execution context several times in a row as the page auto-submits
        # through redirects. Wait for the page to settle between attempts instead
        # of racing the next navigation. The whole recovery shares one monotonic
        # deadline so retries can't compound into many multiples of timeout_ms.
        per_attempt_seconds = timeout_ms / 1000
        loop = asyncio.get_running_loop()
        deadline = loop.time() + per_attempt_seconds * _NAVIGATION_RECOVERY_MAX_ATTEMPTS

        def _remaining_seconds() -> float:
            return max(0.0, deadline - loop.time())

        last_error_msg = initial_error
        for attempt in range(1, _NAVIGATION_RECOVERY_MAX_ATTEMPTS + 1):
            if _remaining_seconds() <= 0:
                LOG.warning(
                    "Skyvern timed out trying to analyze the page after navigation recovery",
                    expression=expression,
                )
                raise TimeoutError("Skyvern timed out trying to analyze the page")

            LOG.warning(
                "JS execution context lost (likely due to page navigation), re-injecting domUtils.js and retrying",
                attempt=attempt,
                expression=expression[:200],
                error=last_error_msg[:200],
            )
            settle_ms = min(_NAVIGATION_SETTLE_TIMEOUT_MS, _remaining_seconds() * 1000)
            await _wait_for_navigation_settle(frame, timeout_ms=settle_ms)

            inject_budget = min(per_attempt_seconds, _remaining_seconds())
            if inject_budget <= 0:
                LOG.error(
                    "Skyvern timed out trying to analyze the page after navigation recovery",
                    expression=expression,
                )
                raise TimeoutError("Skyvern timed out trying to analyze the page")
            try:
                async with asyncio.timeout(inject_budget):
                    # Same dispatch helper so a prefixed Page re-injects
                    # JS_FUNCTION_DEFS via Runtime.evaluate (preserving the marker).
                    await _dispatch_evaluate(frame, JS_FUNCTION_DEFS, None)
            except asyncio.TimeoutError:
                LOG.exception(
                    "Skyvern timed out trying to analyze the page during domUtils.js re-injection",
                    expression=expression,
                )
                raise TimeoutError("Skyvern timed out trying to analyze the page")
            except (PlaywrightError, RuntimeError) as inject_err:
                last_error_msg = str(inject_err)
                if attempt == _NAVIGATION_RECOVERY_MAX_ATTEMPTS or not _is_navigation_context_lost(last_error_msg):
                    LOG.warning(
                        "Re-injection of domUtils.js also failed, page may still be navigating",
                        attempts=attempt,
                    )
                    raise
                continue

            retry_budget = min(per_attempt_seconds, _remaining_seconds())
            if retry_budget <= 0:
                LOG.error(
                    "Skyvern timed out trying to analyze the page after navigation recovery",
                    expression=expression,
                )
                raise TimeoutError("Skyvern timed out trying to analyze the page")
            try:
                async with asyncio.timeout(retry_budget):
                    return await _dispatch_evaluate(frame, expression, arg)
            except asyncio.TimeoutError:
                LOG.exception("Skyvern timed out on retry after JS context re-injection", expression=expression)
                raise TimeoutError("Skyvern timed out trying to analyze the page")
            except (PlaywrightError, RuntimeError) as retry_err:
                last_error_msg = str(retry_err)
                if attempt == _NAVIGATION_RECOVERY_MAX_ATTEMPTS or not _is_navigation_context_lost(last_error_msg):
                    raise

        # The loop either returns or raises; this is unreachable but keeps mypy happy.
        raise PlaywrightError(last_error_msg)

    @classmethod
    async def create_instance(cls, frame: Page | Frame) -> SkyvernFrame:
        instance = cls(frame=frame)
        await cls.evaluate(frame=instance.frame, expression=JS_FUNCTION_DEFS)
        if ENABLE_EXP_ALL_TEXTUAL_ELEMENTS_INTERACTABLE:
            await instance.evaluate(
                frame=instance.frame, expression="() => window.GlobalEnableAllTextualElements = true"
            )
        return instance

    def __init__(self, frame: Page | Frame) -> None:
        self.frame = frame

    def get_frame(self) -> Page | Frame:
        return self.frame

    async def _set_enriched_element_tree_flag(self) -> None:
        # Trim: skyvern_context removed; the enriched element tree defaults off.
        enriched_enabled = False
        await self.evaluate(
            frame=self.frame,
            expression="([enabled]) => { window.GlobalEnableEnrichedElementTree = enabled; }",
            arg=[enriched_enabled],
        )

    async def build_tree_from_body(
        self,
        frame_name: str | None,
        frame_index: int,
        must_included_tags: list[str] | None = None,
        timeout_ms: float = BROWSER_SCRAPING_BUILDING_ELEMENT_TREE_TIMEOUT_MS,
    ) -> tuple[list[dict], list[dict]]:
        must_included_tags = must_included_tags or []
        await self._set_enriched_element_tree_flag()
        js_script = "async ([frame_name, frame_index, must_included_tags]) => await buildTreeFromBody(frame_name, frame_index, must_included_tags)"
        return await self.evaluate(
            frame=self.frame,
            expression=js_script,
            timeout_ms=timeout_ms,
            arg=[frame_name, frame_index, must_included_tags],
        )
