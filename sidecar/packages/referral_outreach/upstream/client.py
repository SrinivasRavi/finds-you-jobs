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
#   - Voyager origin assertion (finds-you-jobs, 2026-08-14): `_fetch` asserts a
#     linkedin.com page origin (`session.ensure_linkedin_origin()`) before the
#     in-page fetch — a broker-backed surface can sit on `about:blank` (or
#     wherever the host's Browser tab last went), where the fetch can only fail.
#   - Live csrf-token derivation (finds-you-jobs, 2026-08-15): the csrf header
#     is re-read from the CURRENT cookie jar at fetch time (`_live_csrf_header`
#     in `_fetch`, then `document.cookie` inside `_FETCH_JS` as the last word)
#     instead of trusting the construction-time snapshot alone. On a cold broker
#     surface after a clean Chrome exit the jar carries no JSESSIONID (session
#     cookies are purged), so the snapshot is empty; the origin assertion's feed
#     navigation then mints a fresh one, and the fetch sent the new cookie with
#     the stale header — voyager's csrf check answers HTTP 403 (the 2026-08-14
#     cold-boot send failure). See `provenance.md`.
#   - Contact-sync messaging read + all-paths capture (finds-you-jobs,
#     2026-08-15): the read-only last-message probe is a GraphQL messenger
#     inbox read — `inbox_last_messages` fetches LinkedIn's own client's
#     `messengerConversations` sync-token snapshot ONCE per browser session
#     (one request per sync sweep, session-cached like the `/me` identity
#     read) and the per-contact probe answers from that map. Headed DevTools
#     capture proved the legacy LEGACY_INBOX REST finder dead (500/empty for
#     every recipient form; messaging moved to GraphQL) and, on the second
#     live confirmation, the paginated GraphQL variant empty too — retiring
#     the same-day intermediates recorded in `provenance.md`. `ProbeCapture`
#     is the env-gated, REDACTED, ALL-PATHS diagnostics instrument
#     (`FYJ_LINKEDIN_CAPTURE_DIR`, default off): one JSON per probed contact
#     on success and on every failure/skip, so a single live sweep pins where
#     each probe stopped without exposing identities or message bodies.
"""Voyager API client that runs fetch() inside the authenticated browser page,
inheriting all browser-injected headers exactly like a real XHR."""

from __future__ import annotations

import datetime as _dt
import functools
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote, urlencode

from .errors import (
    AuthenticationError,
    BrowserUnresponsiveError,
    ProfileInaccessibleError,
    RateLimited,
)
from .jobs import parse_job_search_response
from .url_utils import url_to_public_id
from .voyager import (
    InboxThread,
    parse_connection_degree,
    parse_inbox_last_messages,
    parse_linkedin_voyager_response,
    parse_self_member_urns,
    redact_urn_string,
    redact_voyager_payload,
    summarize_inbox_payload,
)

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


# Env gate for the probe capture (default OFF — unset means a normal run
# captures nothing). Set to a directory, one redacted JSON lands there per
# probed contact; the maintainer runs one live sync with it set and hands the
# files back, so the agent never touches LinkedIn itself.
CAPTURE_DIR_ENV = "FYJ_LINKEDIN_CAPTURE_DIR"


class ProbeCapture:
    """Env-gated, redacted, ALL-PATHS diagnostics for ONE contact-sync probe.

    `actions.get_contact_sync_state` creates one per probed contact and
    `write()` (in its `finally`) lands exactly one JSON file for that contact —
    on success AND on every failure or skip. The predecessor hook
    (`_capture_last_message_probe`, removed 2026-08-15) fired only on the
    messaging SUCCESS path, after the non-ok early return — so the live break
    (a non-ok conversations GET on every contact) produced zero files, and the
    instrument was blind to the exact failure it existed to diagnose. Every
    stage now records: the (redacted) contact id, whether the profile read
    yielded a target urn (namespace preserved), the messaging GET's HTTP
    status/ok and body shape, the parsed direction, and any exception type.

    Redaction: ONE shared id-map (`voyager.redact_urn_string` /
    `redact_voyager_payload`) across the public identifier, target/self urns,
    recipient and both payloads — urn NAMESPACES, structure, status codes and
    timestamps survive; identities and message text do not (same fragment ⇒
    same `ID_n`, so cross-references stay checkable). With the env unset every
    method is a no-op, and no recording may ever fail the probe (`_record`)."""

    _ENDPOINT = (
        "voyagerMessagingGraphQL/graphql?queryId=messengerConversations.<hash>"
        " (sync-token inbox snapshot, ONE request per sync sweep)"
    )

    def __init__(self, session=None):
        self._dir = os.environ.get(CAPTURE_DIR_ENV, "").strip()
        self.enabled = bool(self._dir)
        self._session = session
        if not self.enabled:
            return
        self._id_map: dict[str, str] = {}
        self._doc: dict[str, Any] = {
            "kind": "contact-sync-probe",
            "captured_at": None,
            "public_identifier": None,
            "profile": {"ok": None, "error": None, "target_urn": None, "degree": None},
            "self_urns": None,
            "messaging": {
                "endpoint": self._ENDPOINT,
                "skipped": None,       # "no_target_urn" — the probe never looked
                # The sweep's ONE inbox read (`inbox` is identical across the
                # sweep's captures; `cached` is False only on the probe that
                # actually fetched — that file also carries `payload`).
                "inbox": None,
                # Whether THIS contact had a matching 1:1 thread in the page.
                "thread_found": None,
                "error": None,
            },
            "parsed": None,
            "payload": None,
            "me_payload": None,
        }

    def _record(self, mutate: Callable[[], None]) -> None:
        """Run one recording mutation; a capture bug must never fail the probe."""
        if not self.enabled:
            return
        try:
            mutate()
        except Exception:  # noqa: BLE001 — diagnostics, never load-bearing
            logger.debug("probe-capture recording failed", exc_info=True)

    def _redact(self, value: str) -> str:
        return redact_urn_string(value, self._id_map)

    def record_contact(self, public_identifier: str) -> None:
        self._record(lambda: self._doc.update(
            public_identifier=self._redact(public_identifier or "")
        ))

    def record_profile(self, target_urn: str | None, degree: int | None) -> None:
        self._record(lambda: self._doc["profile"].update(
            ok=True, degree=degree,
            target_urn=self._redact(target_urn) if target_urn else None,
        ))

    def record_profile_cached(self, target_urn: str) -> None:
        """A thread-only probe: the urn came from the host's cache, so NO
        profile read happened this sweep (`cached_urn: True` distinguishes it
        from a live read in the capture)."""
        self._record(lambda: self._doc["profile"].update(
            ok=True, degree=None, cached_urn=True,
            target_urn=self._redact(target_urn) if target_urn else None,
        ))

    def record_self(self, self_urns: Sequence[str]) -> None:
        self._record(lambda: self._doc.update(
            self_urns=[self._redact(u) for u in self_urns]
        ))

    def record_messaging_skipped(self, reason: str) -> None:
        self._record(lambda: self._doc["messaging"].update(skipped=reason))

    def record_inbox(
        self,
        meta: dict,
        mailbox_urn: str,
        payload: Any = None,
        *,
        cached_read: bool,
    ) -> None:
        """The sweep's one inbox read as THIS probe saw it: the request's
        status/ok/error, the conversation counts, and whether this probe
        fetched it (`cached: False`, that file also carries the redacted
        payload) or reused the sweep's cached read."""
        def _mutate() -> None:
            self._doc["messaging"]["inbox"] = {
                "cached": cached_read,
                "mailbox_urn": self._redact(mailbox_urn) if mailbox_urn else None,
                **meta,
            }
            if payload is not None:
                self._doc["payload"] = redact_voyager_payload(payload, self._id_map)
        self._record(_mutate)

    def record_thread(self, found: bool) -> None:
        self._record(lambda: self._doc["messaging"].update(thread_found=found))

    def record_stage_error(self, stage: str, exc: BaseException) -> None:
        def _mutate() -> None:
            name = type(exc).__name__
            if stage == "profile":
                self._doc["profile"].update(ok=False, error=name)
            else:
                self._doc["messaging"].update(error=name)
        self._record(_mutate)

    def record_parsed(self, direction: str | None, sent_at: float | None) -> None:
        self._record(lambda: self._doc.update(
            parsed={"direction": direction, "sent_at": sent_at}
        ))

    def write(self) -> None:
        """Write the one capture file for this probe. Never raises."""
        if not self.enabled:
            return
        try:
            cached = getattr(self._session, "_fyj_self_identity", None)
            if isinstance(cached, tuple) and len(cached) == 2:
                self._doc["me_payload"] = redact_voyager_payload(cached[1], self._id_map)
            self._doc["captured_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            out_dir = Path(self._dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            path = out_dir / f"contact-sync-probe-{stamp}.json"
            path.write_text(json.dumps(self._doc, indent=2), encoding="utf-8")
            logger.info("contact-sync probe capture written: %s", path)
        except Exception:  # noqa: BLE001 — diagnostics must never break a probe
            logger.debug("contact-sync probe capture failed", exc_info=True)


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
        // Live csrf derivation (finds-you-jobs fork, 2026-08-15): the header
        // must equal the JSESSIONID cookie THIS fetch will carry, and any
        // Python-side value can predate a navigation that re-minted the cookie,
        // so it is read from document.cookie in the same JS turn as the fetch.
        // LinkedIn keeps JSESSIONID JS-readable (not HttpOnly) precisely so its
        // own client can do this. No cookie on the page → keep the passed
        // header (fixture pages, off-jar fallback).
        const m = document.cookie.match(/(?:^|;\\s*)JSESSIONID=("?)([^";]*)\\1/);
        if (m && m[2]) headers["csrf-token"] = m[2];
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

    def _live_csrf_header(self) -> dict:
        """The csrf-token from the CURRENT cookie jar, or `{}` when it has no
        JSESSIONID (an off-jar fixture page, or a jar read that failed) so the
        constructed snapshot stays in effect as the fallback.

        Why per-fetch and not per-client: the snapshot in `__init__` is taken
        BEFORE `_fetch`'s origin assertion may navigate, and that navigation can
        mint or rotate JSESSIONID (measured 2026-08-14: a cold broker surface
        after a clean Chrome exit carries no JSESSIONID at all — session cookies
        are purged — and the feed load mints a fresh one). A fetch that sends
        the new cookie with the old header fails voyager's csrf check as HTTP
        403, which the profile paths misreport as ProfileInaccessibleError."""
        try:
            cookies = {c["name"]: c["value"] for c in self.context.cookies()}
        except Exception:  # noqa: BLE001 — fall back to the constructed snapshot
            return {}
        jsessionid = cookies.get("JSESSIONID", "").strip('"')
        return {"csrf-token": jsessionid} if jsessionid else {}

    def _fetch(self, method: str, url: str, headers: dict, body: str | None = None):
        # The in-page fetch inherits the PAGE's origin: off linkedin.com it can
        # only fail (`TypeError: Failed to fetch`), so land the page there first
        # (free when already on-origin; see `AccountSession.ensure_linkedin_origin`).
        self.session.ensure_linkedin_origin()
        # AFTER the possible navigation above: refresh the csrf header from the
        # live jar (`_FETCH_JS` re-derives it from document.cookie as the last
        # word, closing the remaining in-flight window).
        headers = {**headers, **self._live_csrf_header()}
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

    @staticmethod
    def raise_if_throttled(res: _FetchResponse) -> None:
        """Turn LinkedIn's throttle/block statuses into `RateLimited`.

        Everything non-ok used to become a plain `OSError`, which `_retry_io`
        catches and RETRIES three times with backoff — so a 429 or a 999 made us
        re-request straight into an explicit refusal, and the pacer's 24 h
        backoff was never entered because `RateLimited` was raised nowhere in the
        codebase. `RateLimited` is a `VoyagerError`, not an `OSError`, so raising
        it here deliberately escapes the retry loop.

        999 is LinkedIn's own non-standard anti-bot status; 429 is the standard
        one. 503 is deliberately NOT here: LinkedIn serves transient 503s for
        ordinary shed load, and mapping one blip to a 24 h full-stop across every
        meter is the wrong penalty — the plain OSError path's bounded retry is
        the standard treatment for it.
        """
        if res.status in (429, 999):
            raise RateLimited(f"LinkedIn returned HTTP {res.status} (throttled/blocked)")

    @staticmethod
    def _raise_if_unauthorized(res: _FetchResponse, message: str) -> None:
        """Turn LinkedIn's 401 into `AuthenticationError` (session expired /
        invalid / blocked — the host prompts a re-login).

        Lives beside `raise_if_throttled` because the two checks are ORDERED at
        every call site: 401 first, throttle second. A 401 is a dead session, not
        a pace problem, and must never enter the 24 h backoff (nor be retried as
        an `OSError`). Each caller passes its own verbatim message — the endpoint
        that refused is part of what the host surfaces."""
        if res.status == 401:
            raise AuthenticationError(message)

    def _check_profile_response(self, res: _FetchResponse, public_identifier: str) -> None:
        self._raise_if_unauthorized(res, "LinkedIn API returned 401 Unauthorized.")
        self.raise_if_throttled(res)
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

    # The self-identity read behind the inbox mailbox urn. `/me` is the
    # lightest identity endpoint LinkedIn's own client hits constantly; one
    # read per browser session (cached below), not one per probed contact.
    ME_URL = "https://www.linkedin.com/voyager/api/me"

    def _self_member_urns(self) -> tuple[str, ...]:
        """The logged-in member's own identity URNs, every namespace `/me`
        spells them in — the source of the inbox read's mailbox urn (the self
        fsd_profile urn, `_self_mailbox_urn`). Cached on the SESSION object, so
        one browser session (a whole sync sweep) pays the read once, not once
        per contact.

        Best-effort by design: any failure returns `()` uncached — the probe
        itself must never fail because the identity read hiccuped (the inbox
        read then degrades to its honest `no_mailbox_urn` skip). A dead session
        is not detected here; the probe's own fetch raises the honest 401."""
        cached = getattr(self.session, "_fyj_self_identity", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0]:
            return cached[0]
        try:
            res = self.get(self.ME_URL)
            if not res.ok:
                logger.debug("self-identity /me read failed: HTTP %s", res.status)
                return ()
            me_json = res.json()
            urns = tuple(parse_self_member_urns(me_json))
        except Exception:  # noqa: BLE001 — identity is best-effort, never fatal
            logger.debug("self-identity /me read failed", exc_info=True)
            return ()
        if urns:
            self.session._fyj_self_identity = (urns, me_json)
        return urns

    # LinkedIn's messenger inbox, the way LinkedIn's OWN client reads it
    # (captured verbatim from the wire via headed DevTools, 2026-08-15). The
    # legacy `/messaging/conversations?keyVersion=LEGACY_INBOX&q=participants`
    # REST finder is DEAD — it answered 500/empty for every recipient form —
    # so the probe reads the GraphQL conversations snapshot instead.
    # Encoding rules (match the client exactly): the `variables=(…)` grammar's
    # parens/colons are LITERAL; only the mailbox urn's own colons are
    # %3A-encoded.
    _MESSENGER_GRAPHQL_URL = (
        "https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql"
    )
    # The SYNC-TOKEN query: called with ONLY the mailbox urn (no stored token)
    # it returns the full current inbox snapshot plus a newSyncToken, which we
    # ignore and never persist — each sweep calls fresh and gets the whole
    # snapshot again. Live-confirmed 2026-08-15: this exact form returned the
    # entire inbox; the PAGINATED variant (messengerConversations.9501…, with
    # the PRIMARY_INBOX predicate + count + lastUpdatedBefore=now) answered an
    # empty 200 — everything filtered out. The hashed queryId is tied to
    # LinkedIn's client build and WILL rotate; when the inbox read starts
    # failing, refresh it by re-capturing the request from a headed session's
    # DevTools Network panel (`FYJ_LINKEDIN_HEADED=1`) — inherent to riding
    # the private API.
    MESSENGER_CONVERSATIONS_QUERY_ID = (
        "messengerConversations.0d5e6781bbee71c3e51c8843c6519f48"
    )

    def _self_mailbox_urn(self) -> str:
        """The logged-in member's own fsd_profile urn — the GraphQL mailbox key
        — from the session-cached `/me` identity read ("" when unavailable)."""
        for urn in self._self_member_urns():
            if urn.startswith("urn:li:fsd_profile:"):
                return urn
        return ""

    def _inbox_url(self, mailbox_urn: str) -> str:
        variables = f"(mailboxUrn:{quote(mailbox_urn, safe='')})"
        return (
            f"{self._MESSENGER_GRAPHQL_URL}"
            f"?queryId={self.MESSENGER_CONVERSATIONS_QUERY_ID}&variables={variables}"
        )

    def inbox_last_messages(
        self, capture: ProbeCapture | None = None
    ) -> dict[str, InboxThread]:
        """The sweep's ONE messenger-inbox read (contact-sync, FR-NW-15):
        `{contact profile-id tail: InboxThread(last-sender tail, deliveredAt
        seconds, message text, other participant's display name)}` for every
        1:1 conversation in the current inbox snapshot (the sync-token query
        with no token — the full snapshot every call). Read-only, NEVER
        writes.

        Cached on the SESSION object like the `/me` identity read, so a whole
        sweep (one browser session, ≤ BATCH_LIMIT contacts) pays exactly ONE
        messaging request instead of one per contact — the account-safety
        redesign the dead per-contact finder forced (2026-08-15). Pragmatic
        limit, deliberately not over-engineered: the snapshot may not carry
        every historical thread, and a contact absent from it degrades to
        honest "no message data" (thread_found False; degree transitions
        still apply) — no token-following, no paging.

        Failure honesty: a 401 raises AuthenticationError and a 429/999 raises
        RateLimited (sweep-stop signals, never cached); ANY other failure —
        no mailbox urn from `/me`, a non-ok status, an unparseable body, a
        browser error — degrades to an EMPTY map cached for the rest of the
        sweep (no reply-based transitions this tick, degree transitions still
        apply, and nothing re-hammers a failing endpoint 20 times). There is
        deliberately no retry: the next tick, hours away, is the retry.
        `capture` records the read (status, counts, error, redacted payload on
        the fetching probe) into the owning probe's file."""
        if capture is not None:
            capture.record_self(self._self_member_urns())
        cached = getattr(self.session, "_fyj_inbox", None)
        if isinstance(cached, dict):
            if capture is not None:
                capture.record_inbox(
                    cached["meta"], cached["mailbox_urn"], cached_read=True
                )
            return cached["map"]

        meta: dict[str, Any] = {
            "skipped": None, "status": None, "ok": None, "error": None,
            "conversations": 0, "one_to_one": 0,
        }
        inbox_map: dict[str, InboxThread] = {}
        payload: Any = None
        mailbox_urn = self._self_mailbox_urn()
        stop_signal = False
        try:
            if not mailbox_urn:
                meta["skipped"] = "no_mailbox_urn"
                return inbox_map
            url = self._inbox_url(mailbox_urn)
            # LinkedIn's client requests its messaging GraphQL with the plain
            # graphql accept (the captured request), not the normalized one —
            # the normalized accept could re-shape the response away from the
            # captured nested form the parser is locked to.
            res = self.get(url, headers={"accept": "application/graphql"})
            meta["status"], meta["ok"] = res.status, res.ok
            self._raise_if_unauthorized(res, "Messaging GraphQL returned 401 Unauthorized.")
            self.raise_if_throttled(res)
            if not res.ok:
                meta["error"] = f"http_{res.status}"
                return inbox_map
            try:
                payload = res.json()
            except ValueError as exc:
                meta["error"] = type(exc).__name__
                return inbox_map
            inbox_map = parse_inbox_last_messages(payload)
            meta.update(summarize_inbox_payload(payload))
            return inbox_map
        except (AuthenticationError, RateLimited) as exc:
            # Sweep-stop signals: surface them and do NOT cache — the worker
            # ends the sweep here anyway (auth-stop / backoff).
            meta["error"] = type(exc).__name__
            stop_signal = True
            raise
        except Exception as exc:  # noqa: BLE001 — a read miss must not kill the sweep
            meta["error"] = type(exc).__name__
            logger.debug("inbox read failed", exc_info=True)
            return inbox_map
        finally:
            if capture is not None:
                capture.record_inbox(
                    meta, mailbox_urn, payload if not stop_signal else None,
                    cached_read=False,
                )
            if not stop_signal:
                self.session._fyj_inbox = {
                    "meta": meta, "map": inbox_map, "mailbox_urn": mailbox_urn,
                }

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
        self._raise_if_unauthorized(res, "Jobs search API returned 401 Unauthorized.")
        self.raise_if_throttled(res)
        if not res.ok:
            raise OSError(f"Jobs search API error {res.status}: {res.text()[:500]}")
        return parse_job_search_response(res.json())


def resolve_degree(
    api: PlaywrightLinkedinAPI,
    parsed: dict | None,
    public_identifier: str,
    *,
    best_effort: bool,
) -> int | None:
    """The contact's connection degree: the parsed profile's own value, else one
    bounded TOPCARD fallback call.

    FullProfileWithEntities omits the relationship for some profiles (verified
    live 2026-07-08: valilenk → null while stasg7 → 3), so every degree consumer
    needs the same second call — the fallback used to be pasted at three sites
    that had already drifted apart on failure handling:

      `best_effort=True`  — discovery's bulk enrichment: a failed fallback
                            degrades to None (unknown degree ⇒ cold warmth) and
                            the loop keeps going; one profile must never kill a
                            whole page of candidates.
      `best_effort=False` — the single-contact action paths (status, contact
                            sync): the failure IS the result, so it propagates
                            and the op reports it (a 401 must reach the host as a
                            dead session, not as "degree unknown").
    """
    degree = (parsed or {}).get("connection_degree")
    if degree is not None:
        return degree
    if not best_effort:
        return api.get_connection_degree(public_identifier)
    try:
        return api.get_connection_degree(public_identifier)
    except Exception:  # noqa: BLE001 — degree is best-effort, never fatal
        return None
