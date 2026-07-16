# voyager_py/actions.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked from OpenOutreach @ a7a9101, merged into one module:
#   - send_connection_request + selectors  ← linkedin/actions/connect.py
#   - get_connection_status + selectors     ← linkedin/actions/status.py
#   - send_dm (direct-thread UI + API path)  ← linkedin/actions/{send_dm,message}.py
#                                              + linkedin/api/messaging/{send,utils}.py
# The load-bearing IP — LinkedIn's brittle, A/B-tested selector chains and the
# no-note connect flow — is preserved verbatim. Adapted: Django `dump_page_html`
# and the `ProfileState` enum are dropped (we return plain strings); the DB Lead
# lookups are gone (URN is resolved live via the forked client).
"""Connection status, connection-request send, and DM send — the three live
LinkedIn write/read actions the worker drives. Selectors are upstream's."""

from __future__ import annotations

import json
import logging
import os
import uuid
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError

from .client import PlaywrightLinkedinAPI
from .errors import AuthenticationError, ReachedConnectionLimit, SkipProfile
from .session import AccountSession, goto_page, human_type

logger = logging.getLogger("voyager_py.actions")

# --- connection status strings (replace upstream's ProfileState enum) ---
STATUS_CONNECTED = "connected"      # 1st-degree
STATUS_PENDING = "pending"          # invite already sent, awaiting accept
STATUS_QUALIFIED = "qualified"      # connectable (Connect button present)

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/thread/new/"

# ── Selector chains (verbatim from upstream connect.py / status.py / message.py) ──
CONNECT_SELECTORS = {
    "weekly_limit": 'div[class*="ip-fuse-limit-alert__warning"]',
    "invite_to_connect": (
        '[aria-label*="Invite"][aria-label*="to connect"]:visible, '
        'a:has(span:text-is("Connect")):visible, '
        'button:has(span:text-is("Connect")):visible, '
        # SDUI (server-driven-UI) profile, Connect-primary layout: the top-card
        # Connect is an <a> that navigates to LinkedIn's custom-invite compose. It
        # carries no role and no aria-label, so every classic probe above misses
        # it — match its two durable semantic markers instead (href to the
        # custom-invite preload + the ConnectButton componentkey). 2026-07-13 live
        # regression, debug 20260713T093728-connect-no-affordance.
        'a[href*="custom-invite"]:visible, '
        'a[componentkey^="ConnectButton"]:visible'
    ),
    "error_toast": 'div[data-test-artdeco-toast-item-type="error"]',
    "more_button": (
        'button[aria-label="More"]:visible, '
        'button[id*="overflow"]:visible, '
        'button[aria-label*="More actions"]:visible, '
        'button:has(span:text-is("More")):visible'
    ),
    "connect_option": (
        'div[role="button"][aria-label^="Invite"][aria-label*=" to connect"], '
        'div[role="button"]:text-is("Connect"), '
        # EXACT text only ("Connect") — a substring/has-text match here is
        # case-insensitive and catches "Remove connection" on an
        # already-connected profile's More menu (2026-07-12 live near-miss:
        # the probe clicked it; only the unanswered confirm dialog saved the
        # connection). aria-label keeps the capital-C prefix match ("Connect
        # with …" never appears on Remove/Connections items).
        '[role="menuitem"][aria-label^="Connect"], '
        '[role="menuitem"]:text-is("Connect"), '
        'li:text-is("Connect"), '
        'span[role="button"]:text-is("Connect"), '
        # SDUI overflow menu (2026-07-13 live regression): the Connect item is an
        #   <a href="/preload/custom-invite/…" componentkey="ConnectButton…">
        #     <svg id="connect-small">…</svg><span><span>Connect</span></span></a>
        # — no role, no aria-label, so all classic probes above miss it and the
        # send fails with "no Connect affordance found" even though the menu is
        # open with a visible Connect (debug 20260713T093728). Match the durable
        # href/componentkey/icon markers + exact anchor text. `:text-is("Connect")`
        # stays EXACT so it never catches "Remove connection" (the 2026-07-12
        # near-miss) — the anchor's normalized text is exactly "Connect".
        'a[href*="custom-invite"], '
        'a[componentkey^="ConnectButton"], '
        'a:has(svg#connect-small), '
        'a:text-is("Connect")'
    ),
    # Send-without-a-note control on the invite surface. Layout-agnostic: the
    # classic modal renders a <button>; the SDUI invite surface renders the same
    # semantic control as a <button>/<a>/[role=button] with a componentkey. Cover
    # all three so the note-less send survives the server-driven-UI variant that
    # broke the affordance probe (2026-07-13).
    "send_now": (
        'button:has-text("Send now"), button[aria-label*="Send without"], '
        'button[aria-label*="Send invitation"], '
        '[role="button"]:has-text("Send now"), a:has-text("Send now"), '
        '[componentkey*="SendInvite"], [componentkey*="sendInvite"]'
    ),
    "add_note": (
        'button[aria-label*="Add a note"], button:has-text("Add a note"), '
        '[role="button"]:has-text("Add a note"), a:has-text("Add a note")'
    ),
    # LinkedIn churns this control (2026-07-12 live failure: none of the classic
    # textarea shapes matched). Cover: the classic textareas, any textarea inside
    # the connect dialog, and the contenteditable box newer builds use.
    "note_textarea": (
        'textarea[name="message"], textarea#custom-message, textarea[id*="message"], '
        '[role="dialog"] textarea, '
        '[role="dialog"] div[contenteditable="true"][role="textbox"], '
        'div[contenteditable="true"][aria-label*="message" i], '
        # SDUI invite surface: the note field may be a bare contenteditable textbox
        # (no [role=dialog] wrapper) or a page-level textarea when Connect navigates
        # to the custom-invite compose rather than opening a modal.
        'div[contenteditable="true"][role="textbox"], '
        'textarea[aria-label*="note" i], textarea[aria-label*="message" i]'
    ),
    # Submit-the-invitation control on the note-compose surface. Must NEVER match
    # the note-less "Send now" button (that would send without the drafted note):
    # the generic fallback uses :text-is("Send") — EXACT text, so "Send now" is
    # excluded — after the specific "Send invitation" aria/componentkey matches.
    "send_invitation": (
        'button[aria-label*="Send invitation"], '
        '[role="button"][aria-label*="Send invitation"], '
        'a:has-text("Send invitation"), '
        '[componentkey*="SendInvite"], [componentkey*="sendInvite"], '
        'button:text-is("Send"), [role="button"]:text-is("Send"), a:text-is("Send")'
    ),
    # LinkedIn's free tier caps custom invite notes; past the cap, "Add a note"
    # opens a Premium UPSELL dialog instead of the note box (2026-07-12 live
    # capture: "You're out of free custom notes… Try Premium"). Detect + dismiss.
    "premium_upsell": (
        'div[role="dialog"]:has-text("out of free custom notes"), '
        'div[role="dialog"]:has-text("with Premium")'
    ),
    "upsell_dismiss": (
        'div[role="dialog"] button[aria-label*="Dismiss"], '
        'div[role="dialog"] button[aria-label*="Close"]'
    ),
    "pending_button": '[aria-label*="Pending"]:visible, button:has(span:text-is("Pending")):visible',
    # Positive proof the invite actually went out (post-send verification). After a
    # real send LinkedIn shows a confirmation toast ("Invitation sent", "Your
    # invitation to … was sent", "Invitation sent to …"). Paired with the Pending
    # affordance below, this is how we AVOID recording a false "sent" when a
    # click landed but the invite never submitted (2026-07-13: the SDUI
    # custom-invite surface reported success without an invite reaching LinkedIn).
    "invite_sent_toast": (
        'div[data-test-artdeco-toast-item]:has-text("nvitation sent"), '
        'div[data-test-artdeco-toast-item]:has-text("nvitation to"), '
        '[role="alert"]:has-text("nvitation sent"), '
        'div:has-text("Invitation sent to")'
    ),
    # A Message affordance present with NO Connect anywhere ⇒ already a 1st-degree
    # connection (Message-primary layout). Used only to distinguish "already
    # connected" from a genuine selector miss — never clicked.
    "message_button": (
        'button[aria-label*="Message"]:visible, '
        'a[aria-label*="Message"]:visible, '
        'button:has(span:text-is("Message")):visible'
    ),
}

TOP_CARD_SELECTORS = [
    "section:has(div.top-card-background-hero-image)",
    "section[data-member-id]",
    "section.artdeco-card:has(> div.pv-top-card)",
    'section:has(> div[class*="pv-top-card"])',
    'section[componentkey*="com.linkedin.sdui.profile.card"]',
]

MESSAGE_SELECTOR_CHAINS = {
    "compose_input": [
        'div[role="textbox"][aria-label*="Write a message"]',
        'div[role="textbox"][aria-label*="message"i]',
        'div[class*="msg-form__contenteditable"]',
        'div[contenteditable="true"]',
    ],
    "compose_send": [
        'button[type="submit"][class*="msg-form"]',
        'button[class*="send-btn"]',
        'button[class*="send-button"]',
        'form button[type="submit"]',
        'button[type="submit"]',
    ],
}


def _action_scope(page):
    """The top-card section when a known wrapper selector matches, else the whole
    page. Both a Locator and a Page expose `.locator(...)`, so the button probes
    below run against either transparently.

    This is the fix for the "Top Card section not found" SkipProfile: LinkedIn's
    server-driven-UI profile keeps re-shaping the top-card wrapper, so we no longer
    HARD-require it — we scope to it when present (tighter, fewer false matches)
    and fall back to page-wide search when it isn't. The action buttons
    (Connect / Message / More) are what actually matter.
    """
    for selector in TOP_CARD_SELECTORS:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    return page


def _probe_skip_message(reason: str, tried: list[str], debug_path: str = "") -> str:
    """A SkipProfile message that names EXACTLY which probes were tried (maintainer
    ask) and, on a genuine miss, the local debug-capture path to diagnose from."""
    msg = f"send-connection skipped: {reason}; probes tried: [{', '.join(tried)}]"
    if debug_path:
        msg += f"; debug capture: {debug_path}"
    return msg


def find_and_click_connect(page, *, wait_ms: int = 3000, capture=None) -> str:
    """Probe a loaded profile's action buttons in order and click Connect.

    Ordered probes (each recorded, so a failure names them all):
      1. `pending`          — an existing invite ⇒ typed SkipProfile (don't re-invite).
      2. `top-card-connect` — a primary Connect button (Connect-primary layout).
      3. `more-menu-connect`— Connect inside the "More"/overflow menu, the layout in
                              the maintainer's screenshot (Message-primary and
                              Follow-primary both hide Connect here).
      4. `message-primary`  — Message present but no Connect anywhere ⇒ already a
                              1st-degree connection ⇒ typed SkipProfile.
    Returns the winning probe name on success. Raises a typed SkipProfile (naming
    every probe) otherwise. `capture()` (optional) is invoked only on the genuine
    no-affordance miss and should return a debug-capture path string.

    Operates on a Playwright `page` (not the whole session) so the selector logic
    is testable against LOCAL fixture HTML with real Chromium — no live LinkedIn.
    """
    scope = _action_scope(page)
    tried: list[str] = []

    tried.append("pending")
    if scope.locator(CONNECT_SELECTORS["pending_button"]).count() > 0:
        raise SkipProfile(
            _probe_skip_message("invite already Pending — awaiting accept", tried)
        )

    tried.append("top-card-connect")
    direct = scope.locator(CONNECT_SELECTORS["invite_to_connect"])
    if direct.count() > 0:
        direct.first.click()
        _raise_on_error_toast(page)
        return "top-card-connect"

    tried.append("more-menu-connect")
    if _open_more_and_click_connect(page, scope, wait_ms):
        _raise_on_error_toast(page)
        return "more-menu-connect"

    tried.append("message-primary")
    if scope.locator(CONNECT_SELECTORS["message_button"]).count() > 0:
        raise SkipProfile(
            _probe_skip_message(
                "already connected (Message present, no Connect affordance)", tried
            )
        )

    debug_path = capture() if capture is not None else ""
    raise SkipProfile(_probe_skip_message("no Connect affordance found", tried, debug_path))


def _raise_on_error_toast(page) -> None:
    error = page.locator(CONNECT_SELECTORS["error_toast"])
    if error.count() > 0:
        raise SkipProfile(error.first.inner_text().strip())


def _open_more_and_click_connect(page, scope, wait_ms: int) -> bool:
    """Open the "More"/overflow menu (if needed) and click its Connect item.
    The dropdown renders as a portal outside the top card, so it is searched
    page-wide. Returns True when Connect was clicked."""
    connect_option = page.locator(CONNECT_SELECTORS["connect_option"])
    if connect_option.count() == 0:
        more = scope.locator(CONNECT_SELECTORS["more_button"])
        if more.count() == 0:
            return False
        more.first.click()
        try:
            page.locator(CONNECT_SELECTORS["connect_option"]).first.wait_for(
                state="visible", timeout=wait_ms
            )
        except (PlaywrightError, TimeoutError):
            pass
        connect_option = page.locator(CONNECT_SELECTORS["connect_option"])
    if connect_option.count() == 0:
        return False
    connect_option.first.click()
    return True


# LinkedIn intermittently serves an ephemeral "This page doesn't exist" shell
# at a perfectly valid profile URL (observed live 2026-07-08, debug capture
# 20260708T172704-connect-no-affordance: correct /in/ URL, empty title, 404
# body — the same URL loaded fine seconds later). The URL check in goto_page
# passes, so without a content check every probe downstream "finds nothing".
_404_SHELL_MARKERS = (
    "This page doesn’t exist",  # typographic apostrophe (as served)
    "This page doesn't exist",
    "Page not found",
)
_404_SHELL_RELOADS = 2


def _page_is_404_shell(page) -> bool:
    """True when the current document is LinkedIn's 404 shell."""
    try:
        content = page.content()
    except PlaywrightError:
        return False
    return any(marker in content for marker in _404_SHELL_MARKERS)


def _reload_past_404_shell(session: AccountSession, public_identifier: str) -> None:
    """Reload up to _404_SHELL_RELOADS times if the 404 shell rendered; raise a
    typed SkipProfile naming the transient cause when it persists."""
    for attempt in range(1, _404_SHELL_RELOADS + 1):
        if not _page_is_404_shell(session.page):
            return
        logger.info(
            "profile /in/%s served LinkedIn's ephemeral 404 shell — reload %d/%d",
            public_identifier, attempt, _404_SHELL_RELOADS,
        )
        session.wait(2.0, 4.0)
        session.page.reload(wait_until="domcontentloaded")
        session.wait()
    if _page_is_404_shell(session.page):
        raise SkipProfile(
            f"LinkedIn served its 404 shell for /in/{public_identifier} "
            f"after {_404_SHELL_RELOADS} reloads — transient anti-automation "
            "response at a valid URL; retry this send later"
        )


def _goto_profile(session: AccountSession, public_identifier: str) -> None:
    url = f"https://www.linkedin.com/in/{public_identifier}/"
    if f"/in/{public_identifier}" not in session.page.url:
        goto_page(
            session,
            action=lambda: session.page.goto(url, wait_until="domcontentloaded"),
            expected_url_pattern=f"/in/{public_identifier}",
            error_message="Failed to navigate to the target profile",
        )
    _reload_past_404_shell(session, public_identifier)


# ── connection status ─────────────────────────────────────────────
def get_connection_status(session: AccountSession, public_identifier: str) -> str:
    """API degree (degree 1 = connected) with a UI fallback for 2nd/3rd/None."""
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    fresh, _raw = api.get_profile(public_identifier=public_identifier)
    degree = (fresh or {}).get("connection_degree")
    if degree is None:
        degree = api.get_connection_degree(public_identifier)
    if degree == 1:
        return STATUS_CONNECTED

    _goto_profile(session, public_identifier)
    session.wait()
    scope = _action_scope(session.page)
    if scope.locator(CONNECT_SELECTORS["pending_button"]).count() > 0:
        return STATUS_PENDING
    return STATUS_QUALIFIED


# ── contact-status sync probe (read-only — degree + last-message) ──
def get_contact_sync_state(session: AccountSession, public_identifier: str) -> dict:
    """Read a contact's live LinkedIn state for the status-sync engine (FR-NW-15).

    Purely READ-ONLY (never sends): the current connection degree, plus the last
    message's direction (`me` = we sent last, `them` = they did) and timestamp in
    the 1:1 thread. The sync entrypoint maps these onto the kanban transitions
    (Sent→Accepted / →Engagement, Accepted→Engagement, →Ghosted). Best-effort: a
    missing/unreadable thread returns null message fields (no transition), never a
    crash — the account risk is the user's, so the tick stays gentle + honest."""
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    parsed, _raw = api.get_profile(public_identifier=public_identifier)
    degree = (parsed or {}).get("connection_degree")
    if degree is None:
        degree = api.get_connection_degree(public_identifier)
    target_urn = (parsed or {}).get("urn")

    direction: str | None = None
    sent_at: float | None = None
    if target_urn:
        try:
            msg = api.get_last_message(target_urn)
            direction = msg.get("direction")
            sent_at = msg.get("sent_at")
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 — a read miss is not fatal
            logger.debug("get_last_message failed for %s: %s", public_identifier, exc)

    return {
        "degree": degree,
        "is_first_degree": degree == 1,
        "last_message_direction": direction,
        "last_message_at": sent_at,
    }


# ── send connection request (no note — fastest & safest, upstream default) ──
def _click_without_note(session: AccountSession) -> None:
    session.wait()
    send_btn = session.page.locator(CONNECT_SELECTORS["send_now"])
    send_btn.first.click(force=True)
    session.wait()


def _fill_note_and_send(session: AccountSession, note: str, *, timeout_ms: int) -> bool:
    """If a note field is visible within `timeout_ms`, type `note` and click Send.
    Returns True on success, False if no note field surfaced (caller degrades)."""
    note_box = session.page.locator(CONNECT_SELECTORS["note_textarea"]).first
    try:
        note_box.wait_for(state="visible", timeout=timeout_ms)
    except Exception:  # noqa: BLE001 — absence is a signal, not an error
        return False
    human_type(note_box, note, 10, 50)
    session.wait()
    session.page.locator(CONNECT_SELECTORS["send_invitation"]).first.click(force=True)
    session.wait()
    return True


def _click_with_note(session: AccountSession, note: str) -> None:
    """Best-effort connection-request-WITH-note flow (FR-NW-03). Surface-aware
    because LinkedIn ships two invite layouts:

      • **SDUI / Premium custom-invite** — Connect (an <a href="…/custom-invite/…">)
        opens the note compose DIRECTLY; the note field is already present, no
        "Add a note" button exists. (2026-07-13 live regression: the old code,
        not finding that button, silently degraded to a note-less send and
        dropped the referral ask.)
      • **Classic modal** — the note field appears only after clicking "Add a note".

    Order: use an already-present note field first; else click "Add a note" and
    use the field it reveals; else degrade honestly to a note-less send (the
    connect still lands; the ask can follow as a post-accept DM). Tested against
    both layouts as local fixtures — see tests/test_connect_note_flow.py."""
    session.wait()
    # SDUI / Premium: note field opens directly. Short probe so the classic path
    # (field absent until "Add a note") doesn't eat the full timeout here.
    if _fill_note_and_send(session, note, timeout_ms=2_000):
        return

    # Classic modal: reveal the note field via "Add a note", then fill it.
    add_note = session.page.locator(CONNECT_SELECTORS["add_note"])
    if add_note.count() > 0:
        add_note.first.click()
        session.wait()
        if _fill_note_and_send(session, note, timeout_ms=8_000):
            return

    # No note field reachable. On the FREE tier "Add a note" opens a Premium
    # UPSELL instead of the note box (2026-07-12 capture …-note-box-missing);
    # dismiss it. Either way, degrade to a note-less send.
    from .session import capture_failure

    upsell = session.page.locator(CONNECT_SELECTORS["premium_upsell"])
    if upsell.count() > 0:
        logger.warning(
            "LinkedIn free custom-note limit reached (Premium upsell shown) "
            "— dismissing and sending WITHOUT the note"
        )
        dismiss = session.page.locator(CONNECT_SELECTORS["upsell_dismiss"])
        if dismiss.count() > 0:
            dismiss.first.click()
            session.wait()
    else:
        # Genuine markup churn — keep the evidence for selector repair.
        capture_failure(session, "note-box-missing")
        logger.warning("note box not reachable — sending note-less")

    # After the dismissal (or miss) we may be on the connect dialog OR back on the
    # profile — re-drive the note-less send from whichever state is live.
    send_btn = session.page.locator(CONNECT_SELECTORS["send_now"])
    try:
        send_btn.first.wait_for(state="visible", timeout=5_000)
        send_btn.first.click()
    except Exception:  # noqa: BLE001 — connect dialog gone → restart the flow
        find_and_click_connect(session.page)
        _click_without_note(session)
    session.wait()


def _verify_invite_sent(session: AccountSession, *, timeout_ms: int = 6000) -> bool:
    """Confirm the invite ACTUALLY went out after the Send click. Returns True on a
    positive signal — the "Invitation sent" confirmation toast, or a Pending
    affordance now on the page — and False if neither appears within the window.

    This is the guard against a **false "sent"**: on the SDUI custom-invite
    surface a click can land on a control without the invite reaching LinkedIn
    (2026-07-13 live: the app recorded pending, LinkedIn showed no request). We
    would rather honestly fail + capture the DOM than claim a send that didn't
    happen (vision: no false success)."""
    page = session.page
    toast = page.locator(CONNECT_SELECTORS["invite_sent_toast"])
    try:
        toast.first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except (PlaywrightError, TimeoutError):
        pass
    # No toast (they're brief and easy to miss) — a Pending affordance on the
    # profile is equally strong proof the invite landed.
    try:
        if _action_scope(page).locator(CONNECT_SELECTORS["pending_button"]).count() > 0:
            return True
    except PlaywrightError:
        pass
    return False


def send_connection_request(
    session: AccountSession, public_identifier: str, note: str = ""
) -> str:
    """Send a LinkedIn connection request. With a `note` it uses the with-note
    flow (cold referral-ask rides in the note, FR-NW-03); without one it sends
    note-less (upstream default — fastest/safest). Returns the new status.

    Raises ReachedConnectionLimit if LinkedIn's weekly-cap UI appears (the host
    maps that to voyager-owned backoff), and a typed SkipProfile (naming the
    probes tried + a local debug-capture path) when no Connect affordance is
    found, the profile is already connected / invite-pending, or the invite could
    not be CONFIRMED sent (post-send verification — never claim a false send)."""
    from .session import capture_failure

    session.ensure_browser()
    _goto_profile(session, public_identifier)
    session.wait()
    probe = find_and_click_connect(
        session.page, capture=lambda: capture_failure(session, "connect-no-affordance")
    )
    logger.debug("connect affordance via %s for %s", probe, public_identifier)
    if note:
        _click_with_note(session, note)
    else:
        _click_without_note(session)
    if session.page.locator(CONNECT_SELECTORS["weekly_limit"]).count() > 0:
        raise ReachedConnectionLimit("Weekly connection limit pop up appeared")
    # Confirm it actually went out before we report success. An unconfirmed send
    # is a typed failure (with a debug capture), never a silent false "sent".
    if not _verify_invite_sent(session):
        debug = capture_failure(session, "invite-unconfirmed")
        raise SkipProfile(
            "connection request could not be confirmed sent — no 'Invitation sent' "
            "toast and no Pending state after the Send click (the click may have "
            f"landed on a control that didn't submit the invite); debug capture: {debug}"
        )
    return STATUS_PENDING


# ── DM send (warm referral-ask path) ──────────────────────────────
def _find_chain(page, key: str, timeout: int = 5000):
    for sel in MESSAGE_SELECTOR_CHAINS[key]:
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="attached", timeout=timeout)
            return loc
        except (PlaywrightError, TimeoutError):
            continue
    raise PlaywrightError(f"No selector matched for '{key}'")


def _encode_urn(urn: str) -> str:
    return quote(urn, safe="")


def _send_dm_via_ui(session: AccountSession, target_urn: str, message: str) -> bool:
    """Navigate to a new thread for the recipient URN, compose, send."""
    thread_url = f"{LINKEDIN_MESSAGING_URL}?recipient={_encode_urn(target_urn)}"
    try:
        goto_page(
            session,
            action=lambda: session.page.goto(thread_url),
            expected_url_pattern="/messaging",
            timeout=30_000,
            error_message="Error opening messaging thread",
        )
        session.wait(1, 2)
        human_type(_find_chain(session.page, "compose_input").first, message, 10, 50)
        _find_chain(session.page, "compose_send").first.click(delay=200)
        session.wait(0.5, 1)
        return True
    except (PlaywrightError, TimeoutError) as e:
        logger.error("UI DM send failed for %s → %s", target_urn, e)
        return False


def _send_message_api(api: PlaywrightLinkedinAPI, conversation_urn: str,
                      message_text: str, mailbox_urn: str) -> dict:
    """Voyager Messaging API createMessage (forked from api/messaging/send.py)."""
    payload = {
        "message": {
            "body": {"attributes": [], "text": message_text},
            "renderContentUnions": [],
            "conversationUrn": conversation_urn,
            "originToken": str(uuid.uuid4()),
        },
        "mailboxUrn": mailbox_urn,
        "trackingId": os.urandom(16).hex(),
        "dedupeByClientGeneratedToken": False,
    }
    url = (
        "https://www.linkedin.com/voyager/api"
        "/voyagerMessagingDashMessengerMessages?action=createMessage"
    )
    headers = {**api.headers, "accept": "application/json",
               "content-type": "text/plain;charset=UTF-8"}
    res = api.post(url, headers=headers, data=json.dumps(payload))
    if res.status == 401:
        raise AuthenticationError("Messaging API 401 (send_message)")
    if not res.ok:
        raise OSError(f"Messaging API {res.status}: {res.text()[:500]}")
    return res.json()


def send_dm(session: AccountSession, public_identifier: str, message: str) -> bool:
    """Resolve the recipient URN live, then send `message` via the thread UI."""
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    parsed, _raw = api.get_profile(public_identifier=public_identifier)
    target_urn = (parsed or {}).get("urn")
    if not target_urn:
        logger.error("No URN resolved for %s — cannot send DM", public_identifier)
        return False
    return _send_dm_via_ui(session, target_urn, message)
