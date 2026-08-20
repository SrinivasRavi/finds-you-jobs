# Referral Outreach — provenance

The finds-you-jobs Referral Outreach package pairs a finds-you-jobs-owned,
AGPL-3.0-only facade (`client.py`, `types.py`, `fake.py`, this package's
`__init__.py`) with a trimmed, GPLv3, OpenOutreach-derived browser core under
`upstream/`.

## Licensing posture

- The `upstream/` subtree is **GPL-3.0-only** and cannot be relicensed. The full
  license text is `upstream/LICENSE` (verbatim GPLv3). Every file under
  `upstream/` carries an `SPDX-License-Identifier: GPL-3.0-only` header and a
  per-file provenance line naming its upstream source.
- The facade at the package root is **finds-you-jobs-owned, AGPL-3.0-only**.
- GNU's guidance holds that GPLv3 and AGPLv3 modules may be combined; the
  aggregate finds-you-jobs product is AGPL-based while the `upstream/` files
  retain their GPLv3 notices. Nothing here relabels GPL material as AGPL.

## No subprocess firewall (deviation from the prior repository)

The prior MIT-era repository isolated this GPL code behind a subprocess
(`python -m voyager_py <command>`, one JSON object over stdout) because an MIT
host may not link GPL code. finds-you-jobs is AGPL, so the firewall is
unnecessary and **retired** (`docs/internal/referral-outreach.md` section 2): the
facade imports and calls `upstream/` directly, in-process.

Consequences, all deliberate:

- The subprocess JSON-CLI (`cli.py`, `__main__.py`) is **not carried**.
- Inline comments inside `upstream/*.py` that mention "the subprocess", "the MIT
  host", or `python -m voyager_py` are carried verbatim from the subprocess-era
  fork and describe that origin, **not** this repository's architecture. This
  file is the authoritative current record.

The upstream GPLv3 test suite (prior repo `voyager_py/tests/`) is carried under
`sidecar/tests/packages/referral_outreach/`, imports rewritten to
`sidecar.packages.referral_outreach.upstream.*`; the retired subprocess CLI tests
(`test_cli_dry_run.py` and the `contact-sync` CLI cases) are dropped, everything
else carried verbatim with its GPL-era headers retained.

## Fork edits after the initial carry

Changes to `upstream/*.py` made after the original carry. Each keeps the file's
GPLv3 header and per-file provenance line; this section is the running record.

### Broker-backed session mode (2026-08, Referral Outreach Phase 3)

**Files:** `upstream/session.py`, `upstream/worker.py` (both GPL-3.0-only; headers
retained, with a fork-change bullet added in each file).

**What changed and why.** Before this change every browser operation launched,
drove, and tore down its OWN Chromium (`AccountSession._start_persistent` /
`_launch_browser` calling `launch_persistent_context`). finds-you-jobs now hosts
a core, vendor-agnostic browser broker (`sidecar/app/browser`) that keeps one
persistent real-Chrome surface per slug and exposes a single serialized operation
lane (`BrowserSurface.run_on_lane`), streamed to the app. Phase 3 routes the
verbatim referral actions onto that shared surface instead of a private browser,
so the referral flow drives the same surface the user sees rather than an
invisible one.

- `AccountSession.__init__` gained an optional `surface_provider` (a
  `(slug) -> ready surface` callable) and `surface_slug` (default `SURFACE_SLUG =
  "linkedin"`, owned here so core never names the vendor). When a provider is
  set, the session is "broker-backed": it launches no browser.
- New `AccountSession.run_browser(action)`: broker-backed, it submits `action`
  to the surface's lane via `run_on_lane` and binds the surface's own
  `page`/`context` onto the session just before the action runs, so the
  unchanged page-driving code operates on the surface page; the returned
  `concurrent.futures.Future` is `.result()`-ed, which blocks the calling worker
  thread (never the serving loop) and re-raises the action's own exception. With
  no provider it runs the action inline after the existing self-launch, so the
  standalone/CLI path is byte-for-byte unchanged.
- `ensure_browser` short-circuits in broker mode (the surface is always live,
  the page is bound inside the lane); `close` leaves the broker-owned surface
  intact and only drops the session's references (the surface is persistent and
  is what the app streams).
- `worker.py`: `_paced_session` and every browser-touching op
  (`resolve_company`, `discover`, `search_jobs`, `send_connection`, `send_dm`,
  `contact_sync`, `contact_sync_states`, `status`) gained a `surface_provider`
  passthrough and now run their single page-driving call through
  `session.run_browser(...)`. The op STRUCTURE is unchanged: the cap gates,
  charge-on-attempt / refund bookkeeping, and `RateLimited`/`ReachedConnectionLimit`
  to-envelope translation all stay on the worker thread exactly as before (a
  `RateLimited` raised inside the lane surfaces through `.result()` and is caught
  by the same `except`).

**Load-bearing verification (no account, no network).**
`tests/test_broker_lane_connect_note.py` stands up a real broker surface, points
it at the local `invite_classic_modal.html` fixture, and drives
`actions._click_with_note` through a broker-backed session's `run_browser`; it
asserts the note textarea holds the exact composed message. This proves the
verbatim GPL actions run correctly on the broker lane with zero linkedin.com.

**Not done in this change (the clean boundary).** The self-launch path stays the
default; the host does not yet install the provider seam in production, so a live
send still self-launches. Enabling broker mode also needs the login session
reconciled with the broker's per-slug profile dir (`<data>/browser/<slug>/profile`
vs today's `<data>/linkedin/profile`), which cannot be validated without a real
account. See `docs/internal/plugin-architecture.md`. (Resolved for the
fixture-provable part in Phase 5, below.)

### Broker profile reconciliation (2026-08, Referral Outreach Phase 5)

**Files:** `upstream/session.py` (GPL-3.0-only; header retained, with a Phase-5
fork-change bullet added). The rest of the change is core-side (AGPL): the host's
`registry/networker_ops.py` now points the login's persistent profile + sealed
storage-state at the broker's per-slug dir (`linkedin_profile_dir` /
`linkedin_storage_path` derive `<data>/browser/<slug>/{profile,storage_state.json}`
through the broker's OWN `profile_dir` convention, slug from
`referral_surface_slug()`), and the facade exposes that package-owned slug.

**What changed and why.** The Phase-3 boundary above flagged that broker mode
needs the one-time login's session to land where a broker surface reads it. It
now does:

- The headed one-time login (`capture_login`, unchanged) is pointed at the
  broker's per-slug profile dir, so the session it writes and the profile a
  broker surface launches on are the same directory.
- New `AccountSession._seed_surface_session` (called from `run_browser` on the
  first lane bind): when the broker surface's context carries no `li_at`, it
  seeds it from the saved storage-state — the broker-backed twin of
  `_start_persistent`'s first-run migration. This is required because the
  vendor-agnostic broker launches the shared profile WITHOUT
  `--use-mock-keychain` (part of its guardrailed identity), so cookies the login
  wrote under Playwright's default OSCrypt key are undecryptable from the
  profile's on-disk jar (measured). The sealed storage-state carries the
  decrypted cookie values, so the seed restores the session independent of the
  profile's cookie encryption. Sealing (FYJ_SESSION_KEY) is read through the same
  `_load_storage_state` the self-launch path uses; the seed is idempotent and
  never clobbers a context that already carries `li_at`.

### Voyager origin assertion (2026-08-14, first live run)

**Files:** `upstream/session.py` and `upstream/client.py` (both GPL-3.0-only;
headers retained, fork-change bullets added).

**What changed and why.** Every voyager call is an in-page `fetch` against the
page's own origin. The self-launch path establishes that origin implicitly:
`start()`/`_start_persistent` end on `_goto_feed`, so the page always sits on
linkedin.com before any action runs. The broker path had no equivalent — a
broker surface starts at `about:blank`, and the host's Browser tab lets the
user drive it anywhere between runs — so the first live `contact_sync` failed
instantly with `Page.evaluate: TypeError: Failed to fetch` (2026-08-14; the
send path masked the gap because its first action is a profile `goto`). The
guard sits at the one choke point every voyager call funnels through:
`client._fetch` calls the new `AccountSession.ensure_linkedin_origin()`, which
navigates to the feed only when the page sits off `https://www.linkedin.com`.
On-origin fetches add nothing; an off-origin fetch spends the one feed load a
self-launch session start always spent. DOM-driving actions (connect, note,
DM) never pass through `_fetch` and navigate themselves, so fixture-paged flows
stay wire-cold — the first placement of this guard (on every lane bind) broke
exactly that and was moved here.

### Popup-handler fix (2026-08-14, first live run)

**Files:** `upstream/actions.py` (GPL-3.0-only; header retained).

**What changed and why.** `send_connection_request` registered its popup
watcher as `main_page.on("popup", popups.append)`. Playwright's sync wrapper
(`wrap_handler`) setattr's an impl handle on the handler it is given, and a
bound builtin like `list.append` carries no `__dict__`, so the first live
invite failed at registration with `AttributeError: 'builtin_function_or_method'
object has no attribute '_pw_impl_instance_'` — before any Connect click (the
attempt charge stayed on the ledger by design; an unproven send is never
refunded). The composed connect-note fixture flow enters below this function,
so no wire-cold test had ever run this line against a real `Page.on`. The
handler is now a plain function closing over the list; `remove_listener` keeps
working since it is the same function object.

**Load-bearing verification (no account, no network).**
`tests/test_broker_profile_reconciliation.py`: `capture_login` writes into a
temp broker profile dir against a LOCAL fixture that drops a persistent `li_at`,
asserts the storage-state is SEALED (FYJ_SESSION_KEY; no plaintext at rest), then
a real headless broker surface on the same slug — driven by a broker-backed
`AccountSession` — reads `li_at` back off its own context via the seed. A second
test encodes the SingletonLock reality (real Chrome refuses a concurrent open on
the held profile; the open succeeds once it closes), so "the one-time login
window must be closed before the headless surface opens" is a test, not a hope.

**Not done in this change (the clean boundary).** The self-launch path is still
the default (the provider seam is not installed in production), so today the same
retargeted profile dir is used by a self-launched login/discover/send. The live
one-time login itself is the maintainer's step (real credentials, 2FA); only the
plumbing is fixture-proven here. Real Chrome enforces the profile ProcessSingleton
that guards against a login window and a headless surface running on the profile
at once; the higher-level openers' bundled-Chromium fallback does not, but the
intended flow is sequential (log in once, close, then headless forever), so the
two never overlap. See `docs/internal/plugin-architecture.md`.

### Live csrf-token derivation (2026-08-15, cold-boot 403)

**Files:** `upstream/client.py` (GPL-3.0-only; header retained, fork-change
bullet added).

**What changed and why.** `PlaywrightLinkedinAPI.__init__` snapshots the
`csrf-token` header from the context's JSESSIONID cookie once, at construction,
and `_fetch`'s origin assertion (above) can navigate AFTER that snapshot. The
first gated send after the 2026-08-14T23:47:59 boot failed with
`ProfileInaccessibleError: srinivas-ravi (HTTP 403)` at exactly that seam,
evidenced wire-cold from the broker profile's cookie DB plus launch probes on
profile copies (`docs/internal/evidence/2026-08-15-voyager-403-spike/`):
Chrome purges session cookies on the launch after a clean exit, so the cold
surface's jar had li_at but no JSESSIONID, the construction snapshot was empty,
the feed navigation minted a fresh JSESSIONID mid-op (DB row created 23:48:08
IST, inside the 23:48:06→23:48:24 op), and the fetch sent the new cookie with
the empty header — voyager's csrf check answers 403. The earlier cold boots
succeeded because their predecessors exited uncleanly and crash-restore kept
the JSESSIONID, so snapshot and jar matched. The fix derives the header from
the live jar at fetch time, in two layers at the `_fetch` choke point:
`_live_csrf_header` re-reads `context.cookies()` after the origin assertion,
and `_FETCH_JS` re-derives it from `document.cookie` in the same JS turn as the
fetch (LinkedIn keeps JSESSIONID JS-readable for exactly this pattern — its own
client does the same), which also covers a rotation racing the fetch. Pages
without a JSESSIONID cookie keep the passed header, so fixture-paged flows are
unchanged. The construction snapshot stays as the documented fallback.

**Load-bearing verification (no account, no network).**
`tests/test_voyager_csrf_freshness.py` drives the real client + a broker-backed
`AccountSession` through Playwright route interception of the linkedin.com
URLs (fulfilled locally, everything else aborted — zero packets leave): a
fixture feed mints/rotates JSESSIONID via Set-Cookie and the fixture voyager
endpoint answers 403 unless the csrf header equals the minted cookie, the same
contract the live 403 exposed. The cold empty-jar boot, the stale-snapshot
boot, and the warm second action all must send the minted value; a control
fetch proves the fixture really rejects a stale header.

### Self-anchored last-message direction + redacted capture (2026-08-15, contact-sync)

**Files:** `upstream/voyager.py` and `upstream/client.py` (both GPL-3.0-only;
headers retained, fork-change bullets added).

**What changed and why.** The contact-sync probe never read an incoming reply
as `them`, so an accepted contact who replied never moved Accepted → Engagement
(reproduced live 2026-08-14: two full sweeps after a real reply, each
`transitions: {}`). Two code-level defects, both in the finds-you-jobs-authored
messaging section of `voyager.py` (not upstream OpenOutreach code — upstream has
no messaging probe):

- `_message_events` read only the literal `messages`/`events` keys and
  `_event_sender_urn` only `sender`/`*sender`, while `get_last_message`
  requests the LEGACY_INBOX conversations decoration with the normalized
  accept header — a response whose messages arrive as flat `included`
  entities with `*from` senders, none of which those helpers could see.
- The direction rule `"them" if sender_id == target_id else "me"` made `me`
  the unfalsifiable complement: any sender-extraction miss, or a sender urn
  in a different namespace than the target's `fsd_profile` urn, silently
  claimed WE sent last — which suppresses the Engagement transition while the
  sweep still reports success.

The fix anchors the decision on the logged-in member's own identity instead of
inverting the brittle comparison: `client._self_member_urns` reads
`/voyager/api/me` once per browser session (cached on the session object) and
`voyager.parse_self_member_urns` collects every member-namespace urn in it
(miniProfile, `urn:li:member:<id>`, dash profile, plainId — the response
describes only the logged-in member, so all of them are self). In
`parse_last_message(self_urns=…)`: sender ∈ self ⇒ `me`; any other READABLE
sender in the 1:1 thread ⇒ `them`; unreadable sender ⇒ `None` (honest unknown,
never a claimed `me`). Without self urns it falls back to the old target-side
comparison. Event/sender extraction now covers the dash embedding, legacy
`events` lists, the normalized flat-entity shape, and `*from`/`from` senders,
with member-ids on both sides extracted through the one `_urn_member_id`
helper.

**Redacted capture (default off).** `client.get_last_message` gained an
env-gated hook: with `FYJ_LINKEDIN_CAPTURE_DIR` set, each probe writes one
redacted copy of the raw conversations payload (plus the cached `/me` payload,
the target/self urns, and what the parser concluded) to that directory —
`voyager.redact_voyager_payload` keeps keys, nesting, `$type` names, urn
NAMESPACES and timestamps, maps urn id fragments to stable `ID_n` placeholders
through one shared map (so sender-vs-self/target cross-references stay
checkable), and replaces every other string with `REDACTED_TEXT`. Unset (the
default), a run captures nothing. This is the instrument for locking the
wire-cold fixtures to the real live shape without the agent ever touching
LinkedIn.

**Load-bearing verification (no account, no network).**
`tests/test_contact_sync_probe.py` grows wire-cold fixtures for the normalized
LEGACY_INBOX shape (flat `included` events, `*from` senders, urn namespaces the
old code could not read), the self-anchor decision table, `parse_self_member_urns`
across `/me` decorations, and the redaction contract (no identity fragment nor
message body survives; structure and cross-references do). The app-level
reply → Engagement path is covered end-to-end in
`sidecar/tests/app/test_contact_sync_op.py`.

### All-paths probe capture + messaging GET repair (2026-08-15, contact-sync)

**Files:** `upstream/client.py`, `upstream/voyager.py`, `upstream/actions.py`
(all GPL-3.0-only; headers retained, fork-change bullets added).

**What changed and why.** The self-anchored parse fix above was necessary but
could not fire: with the real backend live, a forced 5-contact sweep still
returned `transitions: {}` AND the capture wrote zero files. The evidence chain
(`synced: 5, failed: 0`, no `stopped`) means every probe's `get_profile`
succeeded, `target_urn` was truthy, and `get_last_message` returned cleanly —
so for every contact the conversations GET came back non-ok (not 401/429/999;
those raise) and the miss branch returned direction None. The GET was malformed
on 2 axes, both carried verbatim from the prior repository:

- `get_last_message` sent its params through `get(params=…)` → `urlencode`,
  which percent-encodes the Rest.li `List(…)` grammar into `List%28…%29` —
  the same voyager grammar fact `search_jobs` already documents and works
  around ("build the URL directly rather than via urlencode, which would
  percent-encode the parentheses/colons the API requires literally").
- The recipient was the FULL `urn:li:fsd_profile:…` urn. The LEGACY_INBOX
  participants finder takes the BARE member id (the urn's tail): the endpoint's
  known-working reference client passes the profile-id fragment, never a urn.

The fix builds the URL literally (`_CONVERSATIONS_URL`), with the recipient
derived through `voyager.urn_member_id` (made public — the cross-module
convention here is public helpers) and value-quoted only. Whether the repaired
GET now answers 200 with events can only be confirmed by the maintainer's one
live sweep — which is what the capture rework below exists to prove either way.

**The capture itself was the second defect.** `_capture_last_message_probe`
fired only on the messaging SUCCESS path (after the `if not res.ok` early
return), so the exact live failure it existed to diagnose produced zero files.
Replaced by `client.ProbeCapture` (same env gate, `FYJ_LINKEDIN_CAPTURE_DIR`,
default off; same shared-id-map redaction): `actions.get_contact_sync_state`
creates one per probed contact and writes ONE JSON in its `finally` — on
success and on every failure/skip — recording the (redacted) contact id,
whether the profile read yielded a target urn (namespace preserved) and the
degree, the messaging GET's HTTP status/ok, a body-shape summary
(`voyager.summarize_last_message_payload`: elements/included/events/readable
senders, walked exactly as `parse_last_message` walks), the parsed direction +
timestamp, and any exception type per stage. Called standalone,
`get_last_message` owns and writes its own capture on every path.

**Also fixed:** `get_contact_sync_state`'s blanket `except Exception` swallowed
`RateLimited` from the messaging GET, so the backoff `get_last_message`
deliberately raises for (a 429/999 must stop the sweep, section 0.4) was
unreachable from the sync path. RateLimited now propagates beside
AuthenticationError; the worker maps it to backoff + sweep stop, as designed.

**Load-bearing verification (no account, no network).**
`tests/test_last_message_client.py`: the GET's literal `List(<bare-id>)` form
(no `%28`, no urn in the URL), plus captures written on the non-ok, throttle
and auth paths with status + redacted error body. New
`tests/test_contact_sync_all_paths_capture.py`: one capture per probe through
`get_contact_sync_state` on success / urn-less skip / profile failure /
throttle (which now propagates), identity-free, and default-off writes nothing.
`tests/test_contact_sync_probe.py`: the body-shape summary counts.

### Numeric-member recipient (2026-08-15, live capture round 2)

**Files:** `upstream/client.py`, `upstream/voyager.py`, `upstream/actions.py`
(all GPL-3.0-only; headers retained, fork-change bullets added).

**What the live capture proved.** The maintainer's sweep with the all-paths
capture (5 redacted files) confirmed the grammar fix above and pinned the
remaining break in the recipient VALUE: every probe read the profile fine and
sent `recipients=List(<fsd_profile opaque tail>)`; the finder answered 500 for
the three 1st-degree contacts and an empty 200 (elements=0, events=0) for the
degree-2/3 ones — no conversation ever returned, including for a contact with
a live thread. The decisive cross-reference: the self identity from `/me`
carries a numeric `urn:li:member:` value DISTINCT from the fsd_profile /
fs_miniProfile opaque id, so the LEGACY_INBOX participants finder resolves
recipients by numeric member id, which the fsd_profile urn does not spell.

**What changed.**

- `voyager.parse_profile_member_urn` (pure, new): the contact's numeric
  `urn:li:member:<digits>` urn from their raw `get_profile` response —
  ANCHORED, never scraped payload-wide: claimed only from an entity provably
  the contact's own (same `publicIdentifier`, or an
  entityUrn/dashEntityUrn/*miniProfile tail equal to the contact's opaque
  profile id), so another member's id in the same response can never become
  the recipient. None when the payload spells none.
- `actions.get_contact_sync_state` extracts it from the raw profile response
  (previously discarded) and passes it to `get_last_message`; when it is None
  the messaging read is SKIPPED (`skipped: "no_member_urn"`) rather than fired
  in the live-disproven opaque form.
- `client.get_last_message(target_urn, member_urn=…)` sends the member urn's
  bare digits inside the literal `List(…)`; without `member_urn` (standalone /
  fixture callers only) it falls back to the target tail, named in the capture
  as `recipient_kind: "target-tail"`.
- The capture gains the evidence for the next round: `profile.member_urn`,
  `messaging.recipient_kind`, and `profile_payload` — the contact's raw
  profile response redacted through the same shared id-map — so ONE more live
  sync shows exactly where the numeric id lives in a real payload (or that it
  doesn't, with the full shape to find it in).

**Open until the next live sync (deliberately not decided here).** Whether the
finder wants the bare digits (this attempt, matching the bare-id form of the
grammar) or a full urn inside `List(…)`; and why 1st-degree opaque probes
answered 500 while others answered empty 200 (hypothesis, untested: the finder
resolves-then-hydrates for existing threads and faults on an unresolvable id,
where a no-thread lookup just returns empty). One more capture decides both.

**Load-bearing verification (no account, no network).**
`tests/test_contact_sync_probe.py`: the extraction across payload shapes and
the ownership anchor (a foreign member's id is never claimed).
`tests/test_last_message_client.py`: the numeric recipient in the literal
`List(…)` URL + `recipient_kind` in the capture, and the documented
target-tail fallback. `tests/test_contact_sync_all_paths_capture.py`: the
member-urn pass-through, the `no_member_urn` skip (no fetch fired), and the
redacted `profile_payload` dump (identity-free, member-urn cross-reference
intact).

**Superseded same day** by the GraphQL inbox read below: the live wire proved
the whole LEGACY_INBOX finder dead, so the numeric-member extraction never
shipped in a working path and was removed with it.

### GraphQL inbox read (2026-08-15, headed DevTools ground truth)

**Files:** `upstream/client.py`, `upstream/voyager.py`, `upstream/actions.py`
(all GPL-3.0-only; headers retained, fork-change bullets consolidated).

**Ground truth.** The maintainer captured LinkedIn's own client from a headed
session's DevTools Network panel: messaging moved to GraphQL, and the legacy
`/messaging/conversations?keyVersion=LEGACY_INBOX&q=participants` REST finder
is DEAD — which is why every recipient form (opaque tail, then numeric member
id) answered 500/empty. The working request — the SYNC-TOKEN snapshot query,
whose full response was verified on the wire to return the entire inbox — is:

    GET /voyager/api/voyagerMessagingGraphQL/graphql
      ?queryId=messengerConversations.0d5e6781bbee71c3e51c8843c6519f48
      &variables=(mailboxUrn:urn%3Ali%3Afsd_profile%3A<SELF>)

With no stored token it returns the full current inbox snapshot plus a
`newSyncToken`; the probe calls fresh each sweep and never persists the token,
so every sweep reads the whole snapshot. Structural parens/colons are literal;
only the mailbox urn's own colons are %3A-encoded; the accept is the plain
graphql one LinkedIn's client sends. `mailboxUrn` is the SELF fsd_profile urn
(from the existing `/me` read). The response wraps the conversations under
`data.messengerConversationsBySyncToken.elements` (the parser stays
wrapper-agnostic); each Conversation carries `conversationParticipants[]`
(hostIdentityUrn = fsd_profile urn, `participantType.member.distance` marks
SELF) and `messages.elements` NEWEST-first with `sender.hostIdentityUrn`. The
hashed queryId is tied to LinkedIn's client build and WILL rotate; refreshing
it means re-capturing via headed DevTools — inherent to the private API.
Wire-cold fixtures are SYNTHETIC, built to this shape; no real payload data is
kept in the repo.

**Correction (2026-08-15, second live confirmation).** The first cut of this
change used the OTHER captured queryId — the paginated list
(messengerConversations.9501…, `variables` carrying the PRIMARY_INBOX
predicate + `count` + `lastUpdatedBefore:now`). Live it answered a clean 200
with ZERO conversations (the filters excluded everything), while the caching
held (1 request, rest cached). The sync-token snapshot above is the form whose
response was actually verified to carry the inbox; the paginated variant is
retired. Pragmatic pagination stance, deliberately not over-engineered: the
snapshot may not carry every historical thread — a contact absent from it
degrades to honest `thread_found: false` (degree transitions still apply); no
token-following, no paging.

**What changed (the account-safety redesign).** One messaging request per
sync sweep instead of one per contact:

- `client.inbox_last_messages` fetches the inbox snapshot ONCE per browser
  session (cached on the session like the `/me` identity read) and returns
  `{contact profile-id tail: (last-sender tail, deliveredAt seconds)}`.
  Failure honesty: 401 → AuthenticationError,
  429/999 → RateLimited (sweep-stop signals, never cached); any other failure
  caches an EMPTY map for the sweep — no reply-based transitions this tick,
  degree transitions still apply, and a failing endpoint is never re-hammered
  per contact. No retry by design: the next tick is the retry.
- `voyager.parse_inbox_last_messages` (pure) reads that map out of the
  response: conversations found by their own shape (the wrapper key varies by
  query), 1:1 threads only (exactly 2 participants, exactly 1 marked SELF by
  the response itself), last message = `messages.elements[0]`, sender from
  `sender`/`actor` hostIdentityUrn, newest-wins on duplicate threads.
  `voyager.inbox_direction_for` joins one contact by fsd_profile urn tail:
  sender == contact ⇒ `them`, other readable sender ⇒ `me`, unreadable ⇒
  None, no thread ⇒ honest nulls. Live-verified direction table: a contact
  who replied last reads `them` (Accepted → Engagement fires upstream); the
  thread where SELF sent last reads `me` and correctly stays.
- `actions.get_contact_sync_state` answers from the shared map — no
  per-contact messaging request exists any more. `ProbeCapture` adapted: each
  probe records the sweep's inbox read (status/ok/error, conversation counts,
  `cached` flag, redacted payload on the fetching probe only) plus
  per-contact `thread_found` and the parsed direction.
- `voyager.redact_voyager_payload` keeps categorical enum values under the
  `distance`/`category` keys (SELF / DISTANCE_1 / PRIMARY_INBOX): never user
  content, and exactly what keeps a redacted inbox payload direction-faithful
  (it still parses to the same map).

**Removed (orphaned by this change):** `client.get_last_message` and the
LEGACY_INBOX URL (the dead finder), `voyager.parse_last_message` with its
event/sender helpers (`_message_events`, `_event_sender_urn`, the sender-key
tables) and `summarize_last_message_payload`, and the round-2 numeric-member
extraction (`parse_profile_member_urn` + helpers) with its capture fields
(`profile.member_urn`, `recipient*`, `profile_payload`). `_event_timestamp`,
`urn_member_id`, `parse_self_member_urns` and the redaction helpers stay (the
new read uses them).

**Load-bearing verification (no account, no network).**
`tests/test_contact_sync_probe.py`: the parse locked to the captured shape —
direction table (`them`/`me`/unreadable-None), group-chat and no-SELF skips,
newest-first, wrapper-key independence, duplicate-thread newest-wins, the
summary counts, and the redaction staying direction-faithful.
`tests/test_last_message_client.py`: the request matching the captured shape
(queryId, literal variables grammar, %3A-encoded mailbox urn, graphql accept),
one request per session, 401/429 sweep-stops uncached, http_500 soft-fail
cached, the `no_mailbox_urn` skip, and the capture on every path.
`tests/test_contact_sync_all_paths_capture.py`: the probe end to end — reply
reads `them`, own-last reads `me`, a 2-contact sweep fires ONE messaging
request (second capture `cached: true`), no-thread honest nulls, throttle
propagation, default-off captures nothing.

### Inbox-read display pair (2026-08-15, same read, richer return)

**Files:** `upstream/voyager.py`, `upstream/client.py`, `upstream/actions.py`
(all GPL-3.0-only; headers updated).

**Why.** The host's kanban card and contact modal used to show only the last
message the USER sent (their OutreachLog), even after a sync had read the
contact's reply. The display needs the thread's real last message and who
sent it — data the one inbox response above already carries.

**What changed.** NEW code in the fork's own messaging section, no upstream
material involved (upstream never had a messaging read; see the GraphQL inbox
read entry above):

- `voyager.InboxThread` (NamedTuple) replaces the 2-tuple inbox-map value:
  `(sender_tail, sent_at)` grew `text` (the last message's body, via the
  existing `text_of` on `body`) and `other_name` (the other participant's
  display name off `participantType.member` first/last text nodes, new helper
  `_participant_display_name`). `parse_inbox_last_messages` fills both;
  `inbox_direction_for` returns them as 2 extra tuple slots
  `(direction, sent_at, thread_found, text, from_name)`.
- `client.inbox_last_messages`: annotation/docstring only — same one request
  per sweep, same caching, nothing extra fetched.
- `actions.get_contact_sync_state`: the probe dict grows
  `last_message_text` / `last_message_from` (None when unread). Neither field
  ever enters `ProbeCapture` — the capture stays identity- and body-free
  (asserted in `tests/test_contact_sync_all_paths_capture.py`).

The host (outside this GPL subtree) persists the pair into the contact row's
`profile_payload.last_thread_message` and shows it with Me/name attribution.
Storing the user's own inbox snippet locally for display is the single-user
app showing the user their own data.

**Verification (no account, no network):** the 3 test files above extended in
place — the parse asserts text + name ride every map row, the redaction test
asserts the pair survives only as `REDACTED_TEXT` placeholders, and the
capture tests assert the raw text/name never appear in a capture file.

### Unmetered thread-only probe (2026-08-16, the read-budget redesign)

**Files:** `upstream/actions.py`, `upstream/worker.py`, `upstream/client.py`
(all GPL-3.0-only; headers updated).

**Why.** The live 2026-08-15 sweeps burned the whole day's profile-view
budget re-reading profiles whose only open question was the 1:1 thread —
data the sweep's ONE inbox read already answers. Once spent, every later
Sync press was refused in milliseconds while looking like a successful
sync, and the one contact whose reply the maintainer was waiting on sat in
the unprobed tail.

**What changed.** NEW code in the fork's own contact-sync section, no
upstream material involved:

- `actions.get_contact_sync_state` also returns the `target_urn` it
  resolved, so the host can cache the join key per contact.
- NEW `actions.get_contact_thread_state(session, pid, target_urn)`: the
  thread half of the probe for a host-cached urn — no profile read, no
  profile-view charge; 401/429 on the inbox read propagate as the sweep's
  auth-stop/backoff; other misses degrade to honest nulls. One capture file
  per probe, `profile.cached_urn: true` (new
  `client.ProbeCapture.record_profile_cached`).
- `worker.contact_sync_states` takes per-contact entries
  `{public_identifier, urn, thread_only}` instead of bare pids: thread-only
  entries with a urn run FIRST and unmetered (they cannot be refused by the
  read budget — the message-driven kanban columns stay syncable on a spent
  ledger); the rest keep the full probe's exact pacing/charging/stop
  semantics. Results are keyed by `public_identifier` (no longer
  input-ordered); the up-front no-browser refusal now applies only when the
  sweep has no unmetered work.

**Verification (no account, no network):**
`tests/test_worker_contact_sync_batch.py` and
`tests/test_contact_sync_all_paths_capture.py` extended in place — thread
probes charge zero views and run on a spent ledger, full probes still
charge on attempt, and the thread-only capture is marked and stays
identity-free.

### Send-step narration (2026-08-16, the queue panel ticks real progress)

**Files:** `upstream/actions.py`, `upstream/worker.py` (both GPL-3.0-only).

**Why.** The host's Messenger queue panel used to render a send's step plan
as an unticked list with a "steps aren't reported one by one" caption — the
maintainer's call: we drive every step, so we report every step.

**What changed.** NEW fork code, no upstream material involved: an optional
`on_step` callback threads through `worker.send_connection` /
`worker.send_dm` into `actions.send_connection_request` /
`actions.send_dm` / `_send_dm_via_ui`. The driver calls it as each step
COMPLETES — the pacing wait (worker, `invite1`/`dm1`), the profile open
(`invite2`/`dm2`), the Connect click (`invite3`), the note attach
(`invite4`), the verified invite send (`invite5`), the thread open (`dm3`),
the delivered message (`dm4`). Narration is wrapped so a callback failure
can never break the send it narrates (`_emit_step`). The host publishes
each key as a `send_step` SSE event; the step vocabulary matches the
host's plan i18n keys.

**Verification (no account, no network):** worker send tests assert the
step order on the fixture path; the host-side entrypoint test asserts the
published `send_step` events verbatim.

## Upstream

- **Project:** OpenOutreach — <https://github.com/eracle/OpenOutreach>
- **Forked at commit:** `a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a` (2026-04-29)
- **Upstream license:** GNU GPL v3 (confirmed via upstream `LICENCE.md` + the
  repository's GPLv3 badge — **not** AGPL).

## Take / trim table (per-file; headers in each file are authoritative)

| File (`upstream/`) | Upstream source | Treatment |
| --- | --- | --- |
| `voyager.py` | `linkedin/api/voyager.py` | Verbatim — Voyager profile-response parser (pure). |
| `url_utils.py` | `linkedin/url_utils.py` | Verbatim — public-id ↔ URL helpers. |
| `client.py` | `linkedin/api/client.py` | Forked; `tenacity` retry → hand-rolled `_retry_io` (no added dep). **finds-you-jobs addition:** `search_jobs()` — logged-in job search via the `voyagerJobsDashJobCards` REST endpoint; endpoint/params/decoration DERIVED by observing LinkedIn's own web client (no third-party code copied). |
| `jobs.py` | *new (GPL)* | finds-you-jobs-authored (OpenOutreach has no job-search feature) — parses the normalized `voyagerJobsDashJobCards` response into plain job dicts. GPL-3.0-only because it builds on the GPL fetch-in-page client. Discovery-expansion #6. |
| `session.py` | `linkedin/browser/{session,login,nav}.py` | Adapted: Django dropped (cookies from a storage-state file); `playwright_stealth`/`termcolor` optional/dropped. |
| `actions.py` | `linkedin/actions/{connect,status,send_dm,message}.py` + `linkedin/api/messaging/{send,utils}.py` | Selector chains + no-note connect flow verbatim; `ProfileState` enum → plain strings; DB dump dropped. |
| `discovery.py` | `linkedin/actions/search.py` + `linkedin/browser/nav.py` | Adapted: plain contact dicts (no Django), degree-first sort. |
| `errors.py` | `linkedin/exceptions.py` | Forked + `RateLimited`/`ReachedConnectionLimit` added. |
| `secure_store.py` | *new (GPL)* | Fernet-sealed storage-state read/write; reads `FYJ_SESSION_KEY`. |
| `pacing.py` | *new (GPL)*, derived from upstream `conf.py` limits + `session.random_sleep` | Tiered caps, jittered send delay, 24 h backoff — owned here. |
| `worker.py` | *new (GPL)* | The bounded-operation layer the facade drives directly. |
| `company.py` | `linkedin/actions/company.py` + `linkedin/api/company.py` | Company-entity resolution (typeahead/detail parsers, id/domain helpers). Missed in the original import; carried later under the same take/trim conventions. |

## Deliberately NOT taken (stripped as incompatible with the vision)

- **Freemium promotional actions** — upstream periodically sends a connection
  request + a promo message *from the user's account*, remotely controlled by a
  server the upstream maintainer runs (`linkedin/setup/freemium.py`,
  `linkedin/pipeline/freemium_pool.py`). Not forked.
- **Auto-newsletter subscription** in non-GDPR jurisdictions
  (`linkedin/setup/gdpr.py`, `linkedin/api/newsletter.py`). Not forked.
- The Django/Celery CRM, ML qualifier/embeddings, mem0 vendor tree, and remote
  config — none needed for the bounded operations.

## Dependency closure of the carried subset

- `playwright` (browser automation) — added when the facade's concrete
  implementation lands (commits 10–11); the provenance commit carries the source
  but does not import the browser modules.
- `cryptography` + `keyring` (Fernet sealing of the storage-state) — already a
  finds-you-jobs dependency (NFR-SEC-01).
- Standard library only otherwise. No `requests`, `tenacity`, `playwright_stealth`,
  or `termcolor` (deliberately dropped upstream).

## GPL source availability

When a finds-you-jobs binary ships, the GPLv3 source for `upstream/` is available
via the public repository at <https://github.com/SrinivasRavi/finds-you-jobs>
(path: `sidecar/packages/referral_outreach/upstream/`), satisfying the GPL source-
availability requirement — reinforced by the whole aggregate being AGPL-3.0-only.
