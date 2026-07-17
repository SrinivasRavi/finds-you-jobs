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

## The submit boundary

The Applier agent navigates, fills, and verifies application forms — and stops. Its tool vocabulary contains no submit capability, so neither a prompt mistake nor a page injection can make it submit; the human reviews the filled form in the open browser and clicks Submit themselves. A card is recorded as *Applied* only on detected confirmation evidence or the user's explicit attestation. Autonomous submission is a P2 design behind an explicit delegation opt-in — see [RELEASING.md](RELEASING.md) for the boundary record.

## Releasing

The manual release checklist — gates, provenance audit, per-OS builds, and the honesty requirements for release notes — lives in [RELEASING.md](RELEASING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every commit requires a Developer Certificate of Origin sign-off.

Private planning and agent working documents belong in the local `docs/` directory. Its contents are intentionally ignored so they are not published accidentally.
