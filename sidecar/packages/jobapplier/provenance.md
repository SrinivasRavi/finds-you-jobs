# jobapplier — provenance

The finds-you-jobs Applier package pairs a finds-you-jobs-owned, AGPL-3.0-only
facade and agent (package root) with a trimmed, Skyvern-derived browser
*observation* core under `upstream/`. This file is the authoritative
take/trim record required by `UPSTREAMS.md`'s import gate.

## Licensing posture

- Skyvern is **AGPL-3.0** — the same license as finds-you-jobs-owned code, so
  the carried subtree needs no license boundary at all. Even so, the same
  discipline as the GPL subtree applies: the full upstream license text is
  `upstream/LICENSE` (verbatim), and every file under `upstream/` carries an
  `SPDX-License-Identifier: AGPL-3.0-only` header plus a per-file provenance
  line naming its exact upstream source path and the trims applied.
- The package root (`observe.py`, `__init__.py`, and the agent files that land
  in later commits) is finds-you-jobs-owned, AGPL-3.0-only, written new.
- Nothing here removes or rewrites Skyvern's copyright; the carried files
  remain attributed Skyvern-derived work.

## Upstream

- **Project:** Skyvern — <https://github.com/Skyvern-AI/skyvern>
- **Forked at commit:** `28db09cb59d2f3c15b1a8e1a8405f1a9eaa36ca3` (2026-07-16)
- **Upstream license:** GNU AGPL v3 (verbatim `LICENSE` at the repo root of the
  pin; retained at `upstream/LICENSE`).

## What is taken, and why this slice

Skyvern's durable value for this product is its battle-tested *observation*
layer: the injected `domUtils.js` that walks the live DOM (including shadow
roots and same-origin frames) and emits an interactive-element tree with
stable per-scan ids, plus the Python side that injects it, walks child frames,
assembles/hashes/trims the tree, and renders it as compact HTML for a model
prompt. That is what roadmap commit 12 carries — observation only. The agent
loop, actions, and any fill/submit capability are finds-you-jobs-owned and land
in later commits (`docs/internal/applier.md` §4).

| Upstream file | Carried as | Mode |
| --- | --- | --- |
| `skyvern/webeye/scraper/domUtils.js` | `upstream/domUtils.js` | **Verbatim** (SPDX/provenance header prepended; no other change) |
| `skyvern/webeye/scraper/scraper.py` | `upstream/scraper.py` | Adapted (kept: `load_js_script`, `build_element_dict`, element clean/hash/trim family, frame walk — `get_frame_text`, `get_all_children_frames`, `filter_frames`, `add_frame_interactable_elements`, `get_interactable_element_tree`, `trim_element_tree` + helpers, `_build_element_links`) |
| `skyvern/webeye/scraper/scraped_page.py` | `upstream/scraped_page.py` | Adapted (kept: `json_to_html` + attribute/PUA helpers, `ElementTreeFormat`, `ElementTreeBuilder`; the `ScrapedPage` model dropped) |
| `skyvern/webeye/utils/page.py` | `upstream/page_utils.py` | Adapted (the minimal `SkyvernFrame` subset the kept functions call, plus the viewport-screenshot helper) |
| `skyvern/constants.py`, `skyvern/exceptions.py` | `upstream/constants.py`, `upstream/exceptions.py` | Adapted (only referenced constants/exception classes) |
| Skyvern `LICENSE` | `upstream/LICENSE` | Verbatim |

Uniform trim rules, applied everywhere and noted per file:

- `structlog` → stdlib `logging`, via a small `_StructlogShim` in
  `upstream/constants.py` that accepts structlog-style keyword fields so the
  carried call sites stay byte-identical (the shim is new glue, not carried
  code);
- OpenTelemetry tracing (spans, `traced`, context attrs) → removed;
- `skyvern.config.settings` / `SettingsManager` → module constants with the
  same defaults;
- forge-SDK `calculate_sha256` → local `hashlib` helper;
- `skyvern_context` (their request/telemetry context) → removed — the
  enriched-attr switch defaults off, the persistent cross-observation
  `frame_index_map` becomes a per-call local (identical numbering within one
  observation), `json_to_html`'s hashed-href map becomes module-level;
- experimentation gates → removed, non-experimental default kept;
- token-count–based tree budgeting → dropped (prompt economy is the agent
  loop's concern, later commit).

Audited deviations beyond the uniform rules (2026-07-17 diff-audit against
the pristine pin; `domUtils.js` body verified byte-identical by SHA-256 after
stripping the 8-line provenance header, `LICENSE` verbatim by diff):

- `_dispatch_evaluate` keeps only the direct `frame.evaluate` branch — the
  main-world CDP prefix path is anti-bot middleware we do not carry;
- the cursor-visualization branches of the screenshot helper are dropped
  (default-off upstream), so `cursorOverlay.js` is not carried;
- `add_frame_interactable_elements` no longer calls `_wait_for_scrape_ready`
  (that helper is in the dropped set);
- `UnknownElementTreeFormat` is not carried — its only raisers were dropped;
- `ENRICHED_ONLY_ATTRIBUTES` / `ENRICHED_RESERVED_ATTRIBUTES` dropped
  (unreferenced once the enriched switch was removed).

## Deliberately NOT taken

Per the vision and `docs/internal/roadmap.md` §3: no Skyvern Cloud/API/
dashboard, workflow builder, multi-tenant accounts, database/queue/
orchestrator, proxy networks, anti-bot/CAPTCHA-solving services, stealth
integrations, telemetry/analytics, billing, credential injection, or the
upstream agent loop/action handlers (`skyvern/webeye/actions/`,
`forge/`) — the finds-you-jobs agent is written to this product's own safety
contract instead.

## Dependency closure of the carried subset

`playwright` (already a sidecar dependency) and the standard library.
No `structlog`, no OpenTelemetry, no PIL, no Skyvern forge SDK.

## Feasibility proof

`sidecar/tests/packages/jobapplier/test_observation_spike.py` runs real
headless Chromium against a static local fixture
(`fixtures/application_form.html`, `file://` — zero network): screenshot +
interactive-element tree + frame walk (iframe input observed), per-observation
opaque element ids, compact HTML render. This is the roadmap commit-12 gate
("static local form screenshot→observation test").
