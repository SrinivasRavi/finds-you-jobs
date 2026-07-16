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
unnecessary and **retired** (`docs/internal/referral-outreach.md` §2): the
facade imports and calls `upstream/` directly, in-process.

Consequences, all deliberate:

- The subprocess JSON-CLI (`cli.py`, `__main__.py`) is **not carried**.
- Inline comments inside `upstream/*.py` that mention "the subprocess", "the MIT
  host", or `python -m voyager_py` are carried verbatim from the subprocess-era
  fork and describe that origin, **not** this repository's architecture. This
  file is the authoritative current record.

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
| `client.py` | `linkedin/api/client.py` | Forked; `tenacity` retry → hand-rolled `_retry_io` (no added dep). |
| `session.py` | `linkedin/browser/{session,login,nav}.py` | Adapted: Django dropped (cookies from a storage-state file); `playwright_stealth`/`termcolor` optional/dropped. |
| `actions.py` | `linkedin/actions/{connect,status,send_dm,message}.py` + `linkedin/api/messaging/{send,utils}.py` | Selector chains + no-note connect flow verbatim; `ProfileState` enum → plain strings; DB dump dropped. |
| `discovery.py` | `linkedin/actions/search.py` + `linkedin/browser/nav.py` | Adapted: plain contact dicts (no Django), degree-first sort. |
| `errors.py` | `linkedin/exceptions.py` | Forked + `RateLimited`/`ReachedConnectionLimit` added. |
| `secure_store.py` | *new (GPL)* | Fernet-sealed storage-state read/write; reads `FYJ_SESSION_KEY`. |
| `pacing.py` | *new (GPL)*, derived from upstream `conf.py` limits + `session.random_sleep` | Tiered caps, jittered send delay, 24 h backoff — owned here. |
| `worker.py` | *new (GPL)* | The bounded-operation layer the facade drives directly. |

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
