# voyager_py/tests/test_last_message_client.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""`client.inbox_last_messages` end-to-end, wire-cold (FR-NW-15, 2026-08-15):
the sweep's ONE GraphQL messenger-inbox read that replaced the dead
LEGACY_INBOX REST finder (headed DevTools ground truth).

  - the request matches the captured shape exactly: the paginated
    `messengerConversations` queryId, literal `variables=(…)` grammar,
    %3A-encoded mailbox urn (the self fsd_profile urn from `/me`), the plain
    graphql accept;
  - ONE messaging request per browser session (sweep): the parsed map is
    cached on the session, like the `/me` identity read;
  - failure honesty: 401 → AuthenticationError and 429 → RateLimited (sweep
    stops, nothing cached); any other failure → an EMPTY map cached for the
    sweep, so a failing endpoint is never re-hammered per contact;
  - the env-gated ALL-PATHS capture records the read (status, counts, error,
    redacted payload on the fetching probe) with zero identities in it.

Zero live LinkedIn traffic — the Playwright page/context are in-memory fakes
serving SYNTHETIC fixtures built to the captured shape.
"""

from __future__ import annotations

import json

import pytest

from sidecar.packages.referral_outreach.upstream.client import (
    PlaywrightLinkedinAPI,
    ProbeCapture,
)
from sidecar.packages.referral_outreach.upstream.errors import (
    AuthenticationError,
    RateLimited,
)

from .test_contact_sync_probe import (
    SELF_MEMBER,
    TARGET_MEMBER,
    _conversation,
    _inbox_payload,
    _message,
)

ME_PAYLOAD = {
    "data": {"*miniProfile": f"urn:li:fs_miniProfile:{SELF_MEMBER}", "plainId": 98765},
    "included": [{
        "$type": "com.linkedin.voyager.identity.shared.MiniProfile",
        "entityUrn": f"urn:li:fs_miniProfile:{SELF_MEMBER}",
        "objectUrn": "urn:li:member:98765",
        # The mailbox urn's source: the self fsd_profile urn.
        "dashEntityUrn": f"urn:li:fsd_profile:{SELF_MEMBER}",
    }],
}

# The contact replied last (newest-first, as captured).
REPLY_INBOX = _inbox_payload([_conversation(TARGET_MEMBER, [
    _message(TARGET_MEMBER, 1_700_000_002_000),
    _message(SELF_MEMBER, 1_700_000_001_000),
])])

GRAPHQL = "/voyager/api/voyagerMessagingGraphQL/graphql"


class _FakePage:
    """Serves canned JSON per URL substring through the client's `_FETCH_JS`
    evaluate call; records every fetched URL and its headers."""

    def __init__(self, routes: dict[str, tuple[int, dict]]):
        self.routes = routes
        self.fetched: list[str] = []
        self.headers_seen: list[dict] = []

    def evaluate(self, _js: str, args: list):
        _method, url, headers, _body, _timeout = args
        self.fetched.append(url)
        self.headers_seen.append(headers)
        # Longest key first — "/voyager/api/me" is a prefix of longer paths
        # and must not shadow them.
        for substr, (status, payload) in sorted(
            self.routes.items(), key=lambda kv: -len(kv[0])
        ):
            if substr in url:
                return {"status": status, "ok": status == 200,
                        "body": json.dumps(payload)}
        raise AssertionError(f"unexpected fetch: {url}")


class _FakeContext:
    def cookies(self):
        return [{"name": "JSESSIONID", "value": '"csrf-tok"'}]


class _FakeSession:
    def __init__(self, routes: dict[str, tuple[int, dict]]):
        self.page = _FakePage(routes)
        self.context = _FakeContext()

    def ensure_linkedin_origin(self) -> None:  # already "on origin" in tests
        return None

    def ensure_browser(self) -> None:  # the fake browser is always "up"
        return None


def _api(routes: dict[str, tuple[int, dict]]) -> PlaywrightLinkedinAPI:
    return PlaywrightLinkedinAPI(session=_FakeSession(routes))


def _graphql_fetches(api: PlaywrightLinkedinAPI) -> list[str]:
    return [u for u in api.page.fetched if GRAPHQL in u]


def test_inbox_get_matches_the_captured_request_shape(monkeypatch):
    """The PROVEN request (second live confirmation, 2026-08-15): the
    sync-token snapshot query with ONLY the mailbox urn — the paginated
    variant (PRIMARY_INBOX predicate + count + lastUpdatedBefore=now) came
    back an empty 200 live and must never return. Literal variables grammar
    (structural parens/colons NEVER percent-encoded), the mailbox urn's own
    colons %3A-encoded, the plain graphql accept."""
    monkeypatch.delenv("FYJ_LINKEDIN_CAPTURE_DIR", raising=False)
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (200, REPLY_INBOX)})
    out = api.inbox_last_messages()
    assert out == {TARGET_MEMBER: (
        TARGET_MEMBER, 1_700_000_002.0,
        "Thanks for reaching out, happy to chat!", "Alex Doe",
    )}
    url = _graphql_fetches(api)[0]
    assert "queryId=messengerConversations.0d5e6781bbee71c3e51c8843c6519f48" in url
    assert url.endswith(
        f"&variables=(mailboxUrn:urn%3Ali%3Afsd_profile%3A{SELF_MEMBER})"
    )
    # The empty-200 filters must be GONE — just the mailbox urn.
    assert "predicateUnions" not in url
    assert "count:" not in url
    assert "lastUpdatedBefore" not in url
    assert "%28" not in url and "%29" not in url and "%2C" not in url
    headers = api.page.headers_seen[-1]
    assert headers["accept"] == "application/graphql"


def test_one_inbox_request_per_session(monkeypatch):
    """The account-safety redesign: a whole sweep pays ONE messaging request
    (and one /me), no matter how many contacts ask."""
    monkeypatch.delenv("FYJ_LINKEDIN_CAPTURE_DIR", raising=False)
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (200, REPLY_INBOX)})
    first = api.inbox_last_messages()
    second = api.inbox_last_messages()
    assert first == second == {TARGET_MEMBER: (
        TARGET_MEMBER, 1_700_000_002.0,
        "Thanks for reaching out, happy to chat!", "Alex Doe",
    )}
    assert len(_graphql_fetches(api)) == 1
    me_calls = [u for u in api.page.fetched if u.endswith("/voyager/api/me")]
    assert len(me_calls) == 1


def test_capture_records_the_read_once_and_the_cache_after(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (200, REPLY_INBOX)})
    cap1 = ProbeCapture(api.session)
    api.inbox_last_messages(capture=cap1)
    cap1.write()
    cap2 = ProbeCapture(api.session)
    api.inbox_last_messages(capture=cap2)
    cap2.write()
    files = sorted(capture_dir.glob("contact-sync-probe-*.json"))
    assert len(files) == 2
    doc1 = json.loads(files[0].read_text())
    doc2 = json.loads(files[1].read_text())
    inbox1 = doc1["messaging"]["inbox"]
    assert inbox1["cached"] is False
    assert inbox1["status"] == 200 and inbox1["ok"] is True
    assert inbox1["conversations"] == 1 and inbox1["one_to_one"] == 1
    assert inbox1["mailbox_urn"].startswith("urn:li:fsd_profile:ID_")
    assert doc1["payload"] is not None  # the fetching probe carries the shape
    assert doc1["self_urns"]  # the /me identities, redacted
    inbox2 = doc2["messaging"]["inbox"]
    assert inbox2["cached"] is True and inbox2["status"] == 200
    assert doc2["payload"] is None  # cached probes don't duplicate the payload
    # Zero identities, zero message bodies, anywhere.
    blob = files[0].read_text()
    assert TARGET_MEMBER not in blob and SELF_MEMBER not in blob
    assert "98765" not in blob and "31337" not in blob
    assert "happy to chat" not in blob


def test_capture_off_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("FYJ_LINKEDIN_CAPTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (200, REPLY_INBOX)})
    api.inbox_last_messages()
    assert list(tmp_path.iterdir()) == []


def test_inbox_401_raises_and_is_never_cached(monkeypatch):
    monkeypatch.delenv("FYJ_LINKEDIN_CAPTURE_DIR", raising=False)
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (401, {})})
    with pytest.raises(AuthenticationError):
        api.inbox_last_messages()
    # A dead session is a sweep-stop signal, not a cacheable empty inbox.
    with pytest.raises(AuthenticationError):
        api.inbox_last_messages()
    assert len(_graphql_fetches(api)) == 2


def test_inbox_429_raises_ratelimited_and_captures(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (429, {})})
    cap = ProbeCapture(api.session)
    with pytest.raises(RateLimited):
        api.inbox_last_messages(capture=cap)
    cap.write()
    doc = json.loads(next(iter(capture_dir.glob("contact-sync-probe-*.json"))).read_text())
    inbox = doc["messaging"]["inbox"]
    assert inbox["status"] == 429 and inbox["error"] == "RateLimited"


def test_inbox_500_degrades_to_empty_map_cached_for_the_sweep(tmp_path, monkeypatch):
    """A transient server failure must not kill the sweep NOR be re-hammered
    once per contact: empty map, cached, error named in the capture. The next
    tick (hours away) is the retry."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    api = _api({"/voyager/api/me": (200, ME_PAYLOAD), GRAPHQL: (500, {})})
    cap = ProbeCapture(api.session)
    assert api.inbox_last_messages(capture=cap) == {}
    cap.write()
    assert api.inbox_last_messages() == {}  # cached — no second request
    assert len(_graphql_fetches(api)) == 1
    doc = json.loads(next(iter(capture_dir.glob("contact-sync-probe-*.json"))).read_text())
    inbox = doc["messaging"]["inbox"]
    assert inbox["status"] == 500 and inbox["ok"] is False
    assert inbox["error"] == "http_500"


def test_me_failure_skips_the_inbox_read_honestly(tmp_path, monkeypatch):
    """No mailbox urn from `/me` → no GraphQL request AT ALL (the route table
    would fail any), an empty map, and the capture names the skip."""
    capture_dir = tmp_path / "captures"
    monkeypatch.setenv("FYJ_LINKEDIN_CAPTURE_DIR", str(capture_dir))
    api = _api({"/voyager/api/me": (500, {})})
    cap = ProbeCapture(api.session)
    assert api.inbox_last_messages(capture=cap) == {}
    cap.write()
    assert _graphql_fetches(api) == []
    doc = json.loads(next(iter(capture_dir.glob("contact-sync-probe-*.json"))).read_text())
    inbox = doc["messaging"]["inbox"]
    assert inbox["skipped"] == "no_mailbox_urn" and inbox["status"] is None
