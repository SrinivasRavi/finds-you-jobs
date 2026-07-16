# Contributing to FindsYouJobs

## Commit provenance

Every commit must be signed off with the Developer Certificate of Origin:

```sh
git commit -s -m "type(scope): concise change"
```

The sign-off asserts that the contributor has the right to submit the work under this repository's license. Do not commit code, prompts, assets, or documentation copied from another project without recording its exact source, license, commit/version, and modifications in `UPSTREAMS.md` and `THIRD_PARTY_NOTICES.md`.

## Change discipline

- Keep commits small, buildable, and reviewable.
- Preserve the one local desktop runtime: do not add required module services, submodules, or self-MCP/self-CLI hops.
- Test behavior proportionately: unit/contract tests, integration tests, and inspected UI screenshots for user-facing work.
- Never commit user résumés, cover letters, application screenshots, browser cookies, API keys, local SQLite databases, or private planning/agent material.
- When a change affects user behavior, data schema, or a license boundary, update the corresponding design/requirements/provenance material in the same change.

## Upstream-derived work

Before importing upstream work, make a dedicated provenance commit that pins the upstream source, records the license, inventories dependencies, and identifies taken/adapted/excluded files. Do not mix that audit with unrelated product behavior.
