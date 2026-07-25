// Subscription-CLI engine constants shared by the Prompts and AI Providers
// panes (extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure
// moves, zero behavior change).

// Mirrors the backend's claude-cli DEFAULT_MODEL (sidecar/modules/_shared/
// claude_engine.py) — shown as the effective model when a kind routes there.
export const CLAUDE_CLI_DEFAULT_MODEL = "claude-opus-4-8";

// The subscription-CLI engine family — always routable, no EngineSettings row
// (mirrors the backend's engine_config.CLI_PROVIDERS). codex/agy run their
// CLI's own configured default model when the routing entry names none.
export const CLI_ENGINE_OPTIONS = [
  { id: "claude-cli", label: "settingsPage.providers.cli.claudeLabel" },
  { id: "codex-cli", label: "settingsPage.providers.cli.codexLabel" },
  { id: "antigravity-cli", label: "settingsPage.providers.cli.antigravityLabel" },
];
export const isCliEngine = (id: string) => CLI_ENGINE_OPTIONS.some((o) => o.id === id);
