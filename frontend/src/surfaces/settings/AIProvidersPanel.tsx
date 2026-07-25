// AI Providers panel (FR-SET-06 / US-SET-07). The tile catalog is a static P1
// menu; configured/verified state is cross-referenced from settings.providers
// (the persisted EngineSettings rows) and "In use" from the routing map.
// (Extracted from Settings.tsx 2026-07-25, F-M6 monolith split — pure moves,
// zero behavior change.) Memoized: `settings` is the query-stable object.

import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeleteEngine, useSaveEngine, useVerifyEngine } from "../../api/queries";
import type { EngineVerifyResult, Settings as SettingsT } from "../../api/types";
import { isCliEngine } from "./engines";
import { MUTED_WARN_BOX } from "./shared";

type ProviderCatalogEntry = {
  id: string;
  label: string;
  kind: "key" | "local";
  desc: string;
  modelChips?: string[];
  modelPlaceholder?: string;
};

const PROVIDER_CATALOG: ProviderCatalogEntry[] = [
  {
    id: "openrouter",
    label: "OpenRouter",
    kind: "key",
    desc: "settingsPage.providers.openrouterDesc",
    modelPlaceholder: "e.g. anthropic/claude-opus-4-8",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    kind: "key",
    desc: "settingsPage.providers.anthropicDesc",
    modelChips: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  },
  {
    id: "openai",
    label: "OpenAI",
    kind: "key",
    desc: "settingsPage.providers.openaiDesc",
    modelChips: ["gpt-5", "gpt-5-mini", "gpt-4o", "gpt-4o-mini"],
  },
  {
    id: "local",
    label: "Local LLM",
    kind: "local",
    desc: "settingsPage.providers.localDesc",
    modelPlaceholder: "e.g. llama3.1:70b",
  },
];

const INPUT_CLS =
  "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink placeholder:text-ink-4";

// Subscription-CLI rows in the AI Providers panel (verify-only — no key, no
// persisted row; routing under "Engine routing & prompts" selects them).
const SUBSCRIPTION_CLIS: { id: string; label: string; desc: string }[] = [
  {
    id: "claude-cli",
    label: "settingsPage.providers.cli.claudeLabel",
    desc: "settingsPage.providers.cli.claudeDesc",
  },
  {
    id: "codex-cli",
    label: "settingsPage.providers.cli.codexLabel",
    desc: "settingsPage.providers.cli.codexDesc",
  },
  {
    id: "antigravity-cli",
    label: "settingsPage.providers.cli.antigravityLabel",
    desc: "settingsPage.providers.cli.antigravityDesc",
  },
];

export const AIProvidersPanel = memo(function AIProvidersPanel({ settings }: { settings: SettingsT }) {
  const { t } = useTranslation();
  const verify = useVerifyEngine();
  const save = useSaveEngine();
  const del = useDeleteEngine();

  const [selected, setSelected] = useState<string>(
    () => settings.providers.find((p) => p.configured)?.id ?? PROVIDER_CATALOG[0].id,
  );
  const savedRow = settings.providers.find((p) => p.id === selected);
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(savedRow?.base_url ?? "");
  const [model, setModel] = useState(savedRow?.default_model ?? "");
  const [result, setResult] = useState<EngineVerifyResult | null>(null);
  // Per-CLI verify outcome/busy — independent of the BYOK verify flow above.
  const [cliResults, setCliResults] = useState<Record<string, EngineVerifyResult>>({});
  const [cliBusy, setCliBusy] = useState<string | null>(null);

  async function verifyCli(id: string) {
    setCliBusy(id);
    try {
      const res = await verify.mutateAsync({ provider: id });
      setCliResults((prev) => ({ ...prev, [id]: res }));
    } catch (e) {
      setCliResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          status: "error",
          detail: e instanceof Error ? e.message : String(e),
          provider: id,
        },
      }));
    } finally {
      setCliBusy(null);
    }
  }

  const entry = PROVIDER_CATALOG.find((e) => e.id === selected) ?? PROVIDER_CATALOG[0];

  function select(id: string) {
    const row = settings.providers.find((p) => p.id === id);
    setSelected(id);
    setKey("");
    setBaseUrl(row?.base_url ?? "");
    setModel(row?.default_model ?? "");
    setResult(null);
  }

  const input = {
    provider: selected,
    key: key || undefined,
    base_url: baseUrl || undefined,
    default_model: model || undefined,
  };
  const inUse = settings.routing.some((r) => r.engine === selected);
  // The subscription CLIs are always routable (no saved key) — operations
  // default-route to claude-cli, so "nothing configured" is only a real
  // problem when an operation is routed to a BYOK provider with no saved key
  // (2026-07-23: the old blanket warning contradicted the "In use" CLI badge).
  const unconfiguredRouted = settings.routing.some(
    (r) =>
      !isCliEngine(r.engine) &&
      !settings.providers.find((p) => p.id === r.engine)?.configured,
  );

  return (
    <div className="space-y-4" data-testid="ai-providers-panel">
      {unconfiguredRouted && (
        <div data-testid="no-provider-warning" className={"p-3 text-[11.5px] " + MUTED_WARN_BOX}>
          {t("settingsPage.providers.unconfiguredWarning")}
        </div>
      )}

      {/* Tile grid */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {PROVIDER_CATALOG.map((e) => {
          const row = settings.providers.find((p) => p.id === e.id);
          return (
            <button
              key={e.id}
              data-testid={`provider-tile-${e.id}`}
              onClick={() => select(e.id)}
              // No highlight state on tiles (maintainer 2026-07-24 #2): the
              // config panel right below names the picked tile, and the
              // Configured / In use text says what's actually live — a lit
              // tile just read as "already active" when nothing was set.
              className="rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:bg-surface-3"
            >
              <div className="text-[13px] font-medium text-ink">{e.label}</div>
              <div className={"mt-1 text-[10.5px] " + (row?.configured ? "text-good" : "text-ink-4")}>
                {row?.configured
                  ? t("settingsPage.providers.configured")
                  : t("settingsPage.providers.notSet")}
              </div>
            </button>
          );
        })}
      </div>

      {/* Config panel */}
      <div className="rounded-lg border border-border bg-surface-2 p-3" data-testid="provider-config-panel">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[13px] font-medium text-ink">{entry.label}</div>
          {inUse && (
            <span
              data-testid="engine-in-use"
              className="rounded-full bg-good-wash px-2 py-0.5 text-[10px] font-medium text-good"
            >
              {t("settingsPage.providers.inUse")}
            </span>
          )}
        </div>
        <p className="mt-1 text-[12px] text-ink-3">{t(entry.desc)}</p>

        {entry.kind === "local" ? (
          <div className="mt-3 space-y-2">
            <input
              data-testid="engine-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434/v1"
              className={INPUT_CLS}
            />
            <input
              data-testid="engine-model-input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={entry.modelPlaceholder}
              className={INPUT_CLS}
            />
          </div>
        ) : (
          <div className="mt-3 space-y-2">
            <input
              data-testid="engine-key-input"
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                savedRow?.configured
                  ? t("settingsPage.providers.keySavedPlaceholder", {
                      hint: savedRow.key_hint ?? "•••",
                    })
                  : t("settingsPage.providers.keyPlaceholder")
              }
              className={INPUT_CLS}
            />
            {entry.modelChips ? (
              <div className="flex flex-wrap gap-1.5">
                {entry.modelChips.map((m) => (
                  <button
                    key={m}
                    data-testid={`model-chip-${m}`}
                    onClick={() => setModel(m)}
                    className={
                      "rounded-full border px-2 py-0.5 text-[11px] " +
                      (model === m
                        ? "border-accent bg-accent text-white"
                        : "border-border bg-surface text-ink-2 hover:bg-surface-3")
                    }
                  >
                    {m}
                  </button>
                ))}
              </div>
            ) : (
              <input
                data-testid="engine-model-input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={entry.modelPlaceholder}
                className={INPUT_CLS}
              />
            )}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            data-testid="engine-verify-btn"
            disabled={verify.isPending}
            onClick={() => verify.mutate(input, { onSuccess: setResult })}
            className="inline-flex h-[30px] items-center rounded-md border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink hover:bg-surface-3 disabled:opacity-50"
          >
            {verify.isPending
              ? t("settingsPage.providers.verifying")
              : t("settingsPage.providers.verify")}
          </button>
          <button
            data-testid="engine-save-btn"
            onClick={() => save.mutate(input, { onSuccess: () => setKey("") })}
            className="inline-flex h-[30px] items-center rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
          >
            {t("settingsPage.providers.save")}
          </button>
          {savedRow && (
            <button
              data-testid="engine-delete-btn"
              onClick={() => del.mutate(selected)}
              className="inline-flex h-[30px] items-center rounded-md border border-transparent px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
            >
              {t("settingsPage.providers.remove")}
            </button>
          )}
        </div>

        {result && (
          <div
            data-testid="engine-verify-result"
            className={
              "mt-2 rounded-md border p-2 text-[11.5px] " +
              (result.ok
                ? "border-good-2 bg-good-wash text-good"
                : "border-bad-2 bg-bad-wash text-bad")
            }
          >
            {result.ok ? t("settingsPage.providers.verified") : result.detail}
          </div>
        )}
      </div>

      {/* Subscription CLIs — verify-only providers (no key, nothing persisted);
          route operations to one under Prompts & Models. */}
      <div
        className="rounded-lg border border-border bg-surface-2 p-3"
        data-testid="cli-providers-panel"
      >
        <div className="text-[13px] font-medium text-ink">
          {t("settingsPage.providers.clisTitle")}
        </div>
        <p className="mt-1 text-[12px] text-ink-3">
          {t("settingsPage.providers.clisIntro")}
        </p>
        <div className="mt-2 space-y-1.5">
          {SUBSCRIPTION_CLIS.map((c) => {
            const res = cliResults[c.id];
            const busy = cliBusy === c.id;
            return (
              <div
                key={c.id}
                data-testid={`cli-provider-${c.id}`}
                className="flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-2"
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] font-medium text-ink">{t(c.label)}</span>
                    {settings.routing.some((r) => r.engine === c.id) ? (
                      <span className="rounded-full bg-good-wash px-1.5 py-0.5 text-[9px] font-medium text-good">
                        {t("settingsPage.providers.inUse")}
                      </span>
                    ) : null}
                  </span>
                  <span className="block truncate text-[11px] text-ink-3">{t(c.desc)}</span>
                  {res ? (
                    <span
                      data-testid={`cli-verify-result-${c.id}`}
                      className={
                        "block truncate text-[11px] " + (res.ok ? "text-good" : "text-bad")
                      }
                    >
                      {res.detail}
                    </span>
                  ) : null}
                </span>
                <button
                  onClick={() => void verifyCli(c.id)}
                  disabled={busy}
                  data-testid={`cli-verify-${c.id}`}
                  className="rounded-md border border-border px-2.5 py-1 text-[12px] text-ink-2 hover:border-border-2 disabled:opacity-40"
                >
                  {busy
                    ? t("settingsPage.providers.verifying")
                    : res?.ok
                      ? t("settingsPage.providers.verifiedCheck")
                      : t("settingsPage.providers.verify")}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
});
