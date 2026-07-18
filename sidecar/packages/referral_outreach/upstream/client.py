# voyager_py/client.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked from OpenOutreach `linkedin/api/client.py` @ a7a9101. Changes:
#   - tenacity dependency replaced by a small hand-rolled exponential-backoff
#     retry (`_retry_io`) so the fork carries no extra runtime dep. Same policy:
#     3 attempts, exponential wait, retry only on OSError (incl. the watchdog's
#     BrowserUnresponsiveError), reraise on exhaustion.
#   - Django-side imports dropped; the profile parser + url helpers are the
#     local forked modules.
"""Voyager API client that runs fetch() inside the authenticated browser page,
inheriting all browser-injected headers exactly like a real XHR."""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

from .errors import AuthenticationError, BrowserUnresponsiveError, ProfileInaccessibleError
from .jobs import parse_job_search_response
from .url_utils import url_to_public_id
from .voyager import parse_connection_degree, parse_last_message, parse_linkedin_voyager_response

logger = logging.getLogger("voyager_py.client")

VOYAGER_REQUEST_TIMEOUT_MS = 30_000


def _retry_io(attempts: int = 3, base: float = 2.0, cap: float = 30.0) -> Callable:
    """Retry a call up to `attempts` times on OSError with exponential backoff.
    Replaces upstream's tenacity decorator with zero added deps."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except OSError:
                    if attempt >= attempts:
                        raise
                    logger.debug("retry %s/%s after backoff %.1fs", attempt, attempts, delay)
                    time.sleep(min(delay, cap))
                    delay *= 2
            return None  # unreachable

        return wrapper

    return deco


class _FetchResponse:
    """Thin wrapper around the dict returned by page.evaluate(fetch(...))."""

    __slots__ = ("status", "ok", "_text")

    def __init__(self, raw: dict):
        self.status: int = raw["status"]
        self.ok: bool = raw["ok"]
        self._text: str = raw["body"]

    def json(self) -> Any:
        return json.loads(self._text)

    def text(self) -> str:
        return self._text


class PlaywrightLinkedinAPI:
    def __init__(self, session, timeout_ms: int = VOYAGER_REQUEST_TIMEOUT_MS):
        self.session = session
        self.page = session.page
        self.context = session.context
        self.timeout_ms = timeout_ms

        cookies = self.context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies}
        jsessionid = cookies_dict.get("JSESSIONID", "").strip('"')

        self.headers = {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "csrf-token": jsessionid,
            "x-li-lang": "en_US",
            "x-restli-protocol-version": "2.0.0",
        }

    _FETCH_JS = """([method, url, headers, body, timeoutMs]) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const init = {method, headers, credentials: "include",
                      signal: controller.signal};
        if (body !== null) init.body = body;
        return fetch(url, init).then(async r => {
            clearTimeout(timer);
            return {status: r.status, ok: r.ok, body: await r.text()};
        });
    }"""

    def _run_with_watchdog(self, label: str, fn):
        """Close the browser context if Playwright hangs, so the caller raises
        BrowserUnresponsiveError (an OSError) and the retry can try again."""
        deadline_s = 2 * self.timeout_ms / 1000
        fired = threading.Event()

        def _kill():
            fired.set()
            logger.error("Browser watchdog fired on %s — closing context", label)
            try:
                self.page.context.close()
            except Exception:
                logger.debug("context.close() raised inside watchdog", exc_info=True)

        timer = threading.Timer(deadline_s, _kill)
        timer.daemon = True
        timer.start()
        try:
            result = fn()
        except Exception as exc:
            if fired.is_set():
                raise BrowserUnresponsiveError(
                    f"Browser unresponsive after {int(deadline_s)}s on {label}"
                ) from exc
            raise
        finally:
            timer.cancel()
        if fired.is_set():
            raise BrowserUnresponsiveError(
                f"Browser unresponsive after {int(deadline_s)}s on {label}"
            )
        return result

    def _fetch(self, method: str, url: str, headers: dict, body: str | None = None):
        raw = self._run_with_watchdog(
            f"{method} {url}",
            lambda: self.page.evaluate(self._FETCH_JS, [method, url, headers, body, self.timeout_ms]),
        )
        return _FetchResponse(raw)

    def get(self, url: str, *, headers: dict | None = None, params: dict | None = None):
        h = {**self.headers, **(headers or {})}
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._fetch("GET", url, h)

    def post(self, url: str, *, headers: dict | None = None, data: str | None = None):
        h = {**self.headers, **(headers or {})}
        return self._fetch("POST", url, h, body=data)

    def _check_profile_response(self, res: _FetchResponse, public_identifier: str) -> None:
        if res.status == 401:
            raise AuthenticationError("LinkedIn API returned 401 Unauthorized.")
        if res.status in (403, 404):
            raise ProfileInaccessibleError(f"{public_identifier} (HTTP {res.status})")
        if not res.ok:
            raise OSError(f"LinkedIn API error {res.status}: {res.text()[:500]}")

    @_retry_io()
    def get_profile(self, public_identifier: str | None = None, profile_url: str | None = None):
        if not public_identifier and profile_url:
            public_identifier = url_to_public_id(profile_url)
        if not public_identifier:
            raise ValueError("Need public_identifier or profile_url")

        params = {
            "decorationId": (
                "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-91"
            ),
            "memberIdentity": public_identifier,
            "q": "memberIdentity",
        }
        full_url = "https://www.linkedin.com/voyager/api/identity/dash/profiles"
        res = self.get(full_url, params=params)
        self._check_profile_response(res, public_identifier)
        data = res.json()
        extracted = parse_linkedin_voyager_response(data, public_identifier=public_identifier)
        return extracted, data

    TOPCARD_DECORATION = (
        "com.linkedin.voyager.dash.deco.identity.profile.TopCardSupplementary-120"
    )

    @_retry_io()
    def get_connection_degree(self, public_identifier: str) -> int | None:
        res = self.get(
            "https://www.linkedin.com/voyager/api/identity/dash/profiles",
            params={
                "decorationId": self.TOPCARD_DECORATION,
                "memberIdentity": public_identifier,
                "q": "memberIdentity",
            },
        )
        self._check_profile_response(res, public_identifier)
        return parse_connection_degree(res.json())

    @_retry_io()
    def get_last_message(self, target_urn: str) -> dict:
        """Read-only 1:1 thread probe (contact-sync, FR-NW-15): the last message's
        direction + timestamp with `target_urn`. NEVER writes.

        Returns `{"direction": "me"|"them"|None, "sent_at": epoch_seconds|None}`.
        Any non-OK / unparseable response degrades to both-None (no transition) —
        this is a best-effort read; a miss must never crash the sync tick."""
        res = self.get(
            "https://www.linkedin.com/voyager/api/messaging/conversations",
            params={
                "keyVersion": "LEGACY_INBOX",
                "q": "participants",
                "recipients": f"List({target_urn})",
            },
        )
        if res.status == 401:
            raise AuthenticationError("Messaging API returned 401 Unauthorized.")
        if not res.ok:
            # A read miss (no thread, 404, transient) is not fatal — no history.
            return {"direction": None, "sent_at": None}
        direction, sent_at = parse_last_message(res.json(), target_urn)
        return {"direction": direction, "sent_at": sent_at}

    # LinkedIn's own logged-in jobs-search endpoint (derived by observing the
    # web client — see jobs.py). The REST `voyagerJobsDashJobCards` collection
    # (q=jobSearch) is used over the graphql queryId variant: no hashed queryId
    # to track, free-text `seoLocation` (no geoId lookup), and the normalized
    # data/included shape our client already speaks.
    _JOB_SEARCH_URL = "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards"
    _JOB_SEARCH_DECORATION = (
        "com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollection-220"
    )

    @_retry_io()
    def search_jobs(
        self, keywords: str, location: str = "", *, start: int = 0, count: int = 25
    ) -> dict:
        """One page of logged-in job search → `{"jobs": [...], "total": int}`.

        `keywords` and `location` are the user's own role alias + location (the
        same inputs the guest adapter uses). `location` is free text via
        `seoLocation` — LinkedIn resolves it server-side, so no geoId call is
        needed. Read-only: this never writes to the account (no search-history
        POST, unlike the SPA)."""
        loc_clause = (
            f",locationUnion:(seoLocation:(location:{location}))" if location.strip() else ""
        )
        query = (
            f"(origin:JOB_SEARCH_PAGE_OTHER_ENTRY,keywords:{keywords}{loc_clause}"
            f",spellCorrectionEnabled:true)"
        )
        # The voyager `query=(…)` grammar is not URL-encoded by LinkedIn's own
        # client beyond the value tokens; build the URL directly rather than via
        # urlencode (which would percent-encode the parentheses/colons the API
        # requires literally). Only the free-text tokens need encoding.
        from urllib.parse import quote

        safe_query = query.replace(keywords, quote(keywords, safe=""))
        if location.strip():
            safe_query = safe_query.replace(location, quote(location, safe=""))
        url = (
            f"{self._JOB_SEARCH_URL}?decorationId={self._JOB_SEARCH_DECORATION}"
            f"&count={count}&q=jobSearch&query={safe_query}&start={start}"
        )
        res = self.get(url)
        if res.status == 401:
            raise AuthenticationError("Jobs search API returned 401 Unauthorized.")
        if not res.ok:
            raise OSError(f"Jobs search API error {res.status}: {res.text()[:500]}")
        return parse_job_search_response(res.json())
