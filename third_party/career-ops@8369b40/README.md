# career-ops upstream snapshot — commit 8369b40 (2026-07-05)

Verbatim copies of the career-ops files distilled into finds-you-jobs skill files, kept so
(a) the maintainer can review each distillation against its source, and (b) the next
module repeats the same method.

- Upstream: `santifer/career-ops` @ `8369b4001ba63be78818240b9dbc3aa94aebe2e8` — MIT (LICENSE included).
- **Re-pinned 2026-07-05** from `6a13d8a` (2026-07-03) after the stale-checkout discovery
  (see the tailorer parity-analysis provenance correction): all skills now distill from and
  parity-run against this one commit, checked out at `~/dev/career-ops-pin-8369b40`.
  Diff `6a13d8a → 8369b40` across our source files: **only `modes/oferta.md` changed**
  (+15 lines, the Block A geo-mismatch check — distilled into the scorer skill);
  `pdf.md`, `_shared.md`, `cover.md`, `voice-dna.md`, `heuristics/recruiter-side.md`
  are byte-identical, so the tailorer and coverletterer skills needed no content change.
- Files: `pdf.md` (tailoring recipe), `_shared.md` (source-of-truth + fabrication rules +
  voice/style calibration + scoring system), `heuristics/recruiter-side.md` (risk map +
  six-second gate), `voice-dna.md` (anti-slop guardrail), `cover.md` (CoverLetterer source),
  `oferta.md` (A–G evaluation — Scorer source). All byte-verified against the pin worktree.
- These are **research snapshots**, not vendored product code: the product ships our
  distilled skill files (see `sidecar/modules/*/`), re-synced manually per ROADMAP
  (sync-CI is P2).
- Attribution: see `THIRD_PARTY_NOTICES.md` at repo root.
