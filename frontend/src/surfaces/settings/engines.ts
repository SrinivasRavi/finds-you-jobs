// Subscription-CLI engine constants shared by the Prompts and AI Providers
// panes (extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure
// moves, zero behavior change).

import type { components } from "../../api/schema";

type Shared = components["schemas"]["SharedConstants"];

// The backend's claude-cli DEFAULT_MODEL (sidecar/modules/_shared/
// claude_engine.py), checked against it; shown as the effective model when a
// kind routes there.
export const CLAUDE_CLI_DEFAULT_MODEL: Shared["claude_cli_default_model"] = "claude-opus-5-5";

// The subscription-CLI engine family, always routable with no EngineSettings
// row: the backend's engine_config.CLI_PROVIDERS, checked against it. codex/agy
// run their CLI's own configured default model when the routing entry names none.
const CLI_ENGINE_LABELS: Record<Shared["cli_provider"], string> = {
  "claude-cli": "settingsPage.providers.cli.claudeLabel",
  "codex-cli": "settingsPage.providers.cli.codexLabel",
  "antigravity-cli": "settingsPage.providers.cli.antigravityLabel",
};
export const CLI_ENGINE_OPTIONS = Object.entries(CLI_ENGINE_LABELS).map(([id, label]) => ({
  id,
  label,
}));
export const isCliEngine = (id: string) => CLI_ENGINE_OPTIONS.some((o) => o.id === id);
