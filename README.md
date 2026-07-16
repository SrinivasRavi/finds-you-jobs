# finds-you-jobs

A local-first, open-source desktop application for managing a job search: discovery, scoring, tailoring, referral outreach, and assisted application filling.

## Status

This public repository is an AGPLv3 rebuild with a fresh history. It is being constructed in small, reviewable commits. The first commit establishes release provenance and contribution guardrails; it intentionally contains no imported application source.

## Product principles

- One desktop product: Tauri shell, local Python sidecar, React UI, and local SQLite.
- BYOK: model providers and keys are selected by the user; there is no hosted execution backend.
- Human control: P1 application submission and outbound referral sends remain user-confirmed.
- Transparent operations: durable status, evidence, and model-cost accounting.
- Reuse without distributed plumbing: product modules are direct in-process packages; optional external CLI/MCP adapters are separate callers, never the app's runtime path.

## License

finds-you-jobs-owned code is licensed under [AGPL-3.0-only](LICENSE). This repository also records third-party material and its original terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [UPSTREAMS.md](UPSTREAMS.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every commit requires a Developer Certificate of Origin sign-off.

Private planning and agent working documents belong in the local `docs/` directory. Its contents are intentionally ignored so they are not published accidentally.
