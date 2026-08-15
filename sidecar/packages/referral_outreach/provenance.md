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
