# Releasing finds-you-jobs

The manual release checklist for the self-hosted desktop app. Every release
is a source release first — the AGPL-3.0-only obligations are satisfied by
this public repository carrying the complete corresponding source, including
the third-party subtrees with their original notices.

## 1. Gates (all green before anything is tagged)

```bash
pnpm boot           # install: frontend + root node deps, uv sync
pnpm codegen        # OpenAPI → frontend/src/api/schema.d.ts (must be clean in git)
pnpm test           # pytest (includes real-Chromium applier + observation tests)
pnpm lint           # ruff
pnpm typecheck      # pyright + tsc
cd frontend && npx playwright test   # e2e vs a real sidecar; screenshots reviewed
pnpm dev            # manual boot check: full Tauri window, graceful quit
```

The e2e suite must be run with its screenshots *looked at*, not just green —
"done means verified" includes the pixels.

## 2. Provenance / notice audit (per release)

- `UPSTREAMS.md` rows match reality: every carried subtree's pin, license,
  and path (career-ops @ `8369b40` MIT prompt distillations; OpenOutreach @
  `a7a9101` GPL-3.0-only at `sidecar/packages/referral_outreach/upstream/`;
  Skyvern @ `28db09cb` AGPL-3.0 at `sidecar/packages/jobapplier/upstream/`).
- `THIRD_PARTY_NOTICES.md` sections present; upstream `LICENSE` files intact
  under each subtree; SPDX headers on every carried file.
- CI `repository-policy` workflow green (license text, private-docs ignore,
  DCO sign-offs).
- No `docs/internal/` content staged; `docs/.gitignore` unchanged unless a
  public doc was deliberately unignored in a reviewed change.

## 3. Build

Per-OS packaged builds (`pnpm tauri build`) target macOS, Windows, and Linux.
Build on each OS natively where available; document any platform not built
in the release notes rather than shipping an untested binary. The packaged
build spawns the sidecar binary directly, so the orphan-watchdog chain holds
(the known dev-only `uv run` wrapper edge does not apply to packaged builds).

## 4. Release notes must state honestly

- What the Applier does in P1: it navigates, fills, and verifies — it opens
  the form and hands off; **it never submits**. Submission is the human's
  click (or an explicit post-review attestation). See the P2 boundary below.
- Referral Outreach is experimental, default-off, and the LinkedIn-account
  risk sits with the user.
- The BYOK-cloud path sends data to the user's own model provider; only the
  local-model path keeps everything on the machine.

## 5. The P2 submit boundary (design record — NOT a P1 feature)

P1 ships **no auto-submit**: the agent's tool vocabulary
(`sidecar/packages/jobapplier/actions.py`) contains no `submit` tool, so no
prompt, page injection, or model mistake can expose one; the executor would
reject it anyway. The P1 terminal success is `ready_for_human`, and a card
reaches *Applied* only through detected confirmation evidence or the user's
explicit attestation.

When P2 delegation arrives, `submit` will be a **separate, package-level
tool provider** that:

1. is absent from every P1 build's engine tool schema (not flag-gated —
   absent);
2. requires an explicit, revocable full-delegation opt-in stored in user
   preferences, plus a per-run quality gate;
3. verifies a confirmation page before recording `submitted`, exactly like
   the P1 evidence path;
4. reports what it applied to (the delegation report), never silently.

No release before that design lands may enable any submit path. This section
is the boundary record required by the rebuild roadmap; changing it requires
a maintainer decision, not a PR drive-by.
