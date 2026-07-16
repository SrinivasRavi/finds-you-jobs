# Upstream provenance register

This register is intentionally created before the first upstream import. An entry is not permission to copy a project wholesale. Each import must identify the exact files, origin commit, license text, modifications, excluded components, and selected dependency closure.

| Upstream | URL | Exact source pin | License | Intended local use | Current status |
| --- | --- | --- | --- | --- | --- |
| career-ops | https://github.com/santifer/career-ops | `8369b40` is the prior repository's documented skill lineage; re-verify before porting | MIT | Skill/prompt provenance review; independently written implementations remain marked as such | Not carried |
| OpenOutreach | https://github.com/eracle/OpenOutreach | `a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a` is the prior repository's documented Voyager fork pin; re-verify before porting | GPL-3.0 | Trimmed, local in-process Referral Outreach core | Not carried |
| Skyvern | https://github.com/Skyvern-AI/skyvern | Must be selected and recorded before any import | AGPL-3.0 | Trimmed, local screenshot/observe/act/verify browser-agent core | Not carried |

## Import gate

Before an entry can move from **Not carried** to **Carried**, a dedicated commit must include:

- the exact upstream license text and copyright notices;
- a file-level take/trim table;
- an auditable dependency inventory;
- a local-only feasibility proof for the selected slice;
- proof that excluded cloud, proxy, CAPTCHA, telemetry, payment, queue, and database infrastructure is absent; and
- tests for the package's direct, in-process facade.

Do not use git submodules. The desktop application must import its selected package directly; a future CLI/MCP adapter is a separate optional caller.
