# Upstream provenance register

This register is intentionally created before the first upstream import. An entry is not permission to copy a project wholesale. Each import must identify the exact files, origin commit, license text, modifications, excluded components, and selected dependency closure.

| Upstream | URL | Exact source pin | License | Intended local use | Current status |
| --- | --- | --- | --- | --- | --- |
| career-ops | https://github.com/santifer/career-ops | `8369b4001ba63be78818240b9dbc3aa94aebe2e8` (re-pinned from `6a13d8a`; distilled source files byte-identical except `modes/oferta.md`) | MIT | **Carried — prompt text only.** Skill files for tailorer/coverletterer (scorer follows) distill six upstream prompt files; near-verbatim blocks identified and logged per file (see each skill's distillation-log appendix + `THIRD_PARTY_NOTICES.md`). Snapshots + upstream LICENSE at `third_party/career-ops@8369b40/`. No runtime code carried; module implementations are independent. Dependency closure of the carried text: none (markdown prompts). | Carried (prompts) |
| OpenOutreach | https://github.com/eracle/OpenOutreach | `a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a` (2026-04-29) | GPL-3.0 | **Carried — trimmed LinkedIn Voyager browser core** at `sidecar/packages/referral_outreach/upstream/` (GPLv3, per-file SPDX + provenance; take/trim table in `…/provenance.md`). Called DIRECTLY in-process by the AGPL facade — no subprocess firewall (retired vs. the prior repository). Freemium/newsletter/Django-CRM/ML stripped. Runtime deps of the carried subset: `playwright` (added when the facade impl lands, commits 10–11), `cryptography`+`keyring` (already present), stdlib otherwise. | Carried (GPLv3 core) |
| Skyvern | https://github.com/Skyvern-AI/skyvern | `28db09cb59d2f3c15b1a8e1a8405f1a9eaa36ca3` (2026-07-16) | AGPL-3.0 | **Carried — trimmed browser observation core** at `sidecar/packages/jobapplier/upstream/` (AGPL-3.0, per-file SPDX + provenance; take/trim table in `…/provenance.md`): verbatim `domUtils.js` + adapted scraper/element-tree/frame-walk/screenshot subset. Cloud/dashboard/workflow-builder/queue/proxy/CAPTCHA/telemetry and the upstream agent loop are NOT taken; the finds-you-jobs agent is written new against `docs/internal/applier.md`. Runtime deps of the carried subset: `playwright` + stdlib. | Carried (AGPL core) |

## Import gate

Before an entry can move from **Not carried** to **Carried**, a dedicated commit must include:

- the exact upstream license text and copyright notices;
- a file-level take/trim table;
- an auditable dependency inventory;
- a local-only feasibility proof for the selected slice;
- proof that excluded cloud, proxy, CAPTCHA, telemetry, payment, queue, and database infrastructure is absent; and
- tests for the package's direct, in-process facade.

Do not use git submodules. The desktop application must import its selected package directly; a future CLI/MCP adapter is a separate optional caller.
