// Observability pane section (extracted from Settings.tsx 2026-07-25, F-M6
// monolith split — pure moves, zero behavior change). Memoized: `settings` is
// the query-stable object, `patch` a root useCallback.

import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { Settings as SettingsT } from "../../api/types";
import { InfoDot } from "../../shell/InfoDot";
import { Section, Toggle } from "./shared";

// OTLP headers (audit P2-3) — the wire shape (`observability/config.py`'s
// `otlp_headers`) is a flat string dict; the Settings input edits it as a
// single "key1=val1,key2=val2" field, converted at the UI boundary only.
function otlpHeadersToText(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([k, v]) => `${k}=${v}`)
    .join(",");
}

function otlpHeadersFromText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const pair of text.split(",")) {
    const idx = pair.indexOf("=");
    if (idx <= 0) continue;
    const key = pair.slice(0, idx).trim();
    const value = pair.slice(idx + 1).trim();
    if (key) out[key] = value;
  }
  return out;
}

export const ObservabilitySection = memo(function ObservabilitySection({
  settings,
  patch,
}: {
  settings: SettingsT;
  patch: (p: Partial<SettingsT>) => void;
}) {
  const { t } = useTranslation();
  return (
    <Section title={t("settingsPage.observability.title")}>
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center text-[13px] font-medium text-ink">
              {t("settingsPage.observability.contentLogging")}
              <InfoDot label={t("settingsPage.observability.contentLogging")}>
                {t("settingsPage.observability.contentLoggingInfo")}
              </InfoDot>
            </div>
            <div className="text-[12px] text-ink-3">
              {t("settingsPage.observability.contentLoggingHint")}
            </div>
          </div>
          <Toggle
            on={settings.observability.content_logging}
            onChange={(v) =>
              patch({ observability: { ...settings.observability, content_logging: v } })
            }
            testid="content-logging-toggle"
          />
        </div>
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center text-[13px] font-medium text-ink">
              {t("settingsPage.observability.otlpExport")}
              <InfoDot label={t("settingsPage.observability.otlpExport")}>
                {t("settingsPage.observability.otlpExportInfo")}
              </InfoDot>
            </div>
            <div className="text-[12px] text-ink-3">
              {t("settingsPage.observability.otlpExportHint")}
            </div>
          </div>
          <Toggle
            on={settings.observability.otlp_enabled}
            onChange={(v) =>
              patch({ observability: { ...settings.observability, otlp_enabled: v } })
            }
            testid="otlp-toggle"
          />
        </div>
        {settings.observability.otlp_enabled ? (
          <div className="flex items-center gap-3" data-testid="otlp-endpoint-row">
            <div className="flex-1 text-[13px] text-ink-2">
              {t("settingsPage.observability.otlpEndpoint")}
            </div>
            <input
              value={settings.observability.otlp_endpoint}
              onChange={(e) =>
                patch({
                  observability: { ...settings.observability, otlp_endpoint: e.target.value },
                })
              }
              placeholder="https://otlp.example.com:4318/v1/traces"
              className="w-64 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink placeholder:text-ink-4"
            />
          </div>
        ) : null}
        {settings.observability.otlp_enabled ? (
          <div className="flex items-center gap-3" data-testid="otlp-headers-row">
            <div className="flex-1 text-[13px] text-ink-2">
              {t("settingsPage.observability.otlpHeaders")}
            </div>
            <input
              value={otlpHeadersToText(settings.observability.otlp_headers)}
              onChange={(e) =>
                patch({
                  observability: {
                    ...settings.observability,
                    otlp_headers: otlpHeadersFromText(e.target.value),
                  },
                })
              }
              placeholder="key1=val1,key2=val2"
              className="w-64 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink placeholder:text-ink-4"
            />
          </div>
        ) : null}
        <div className="flex items-center gap-3" data-testid="retention-days-row">
          <div className="flex-1">
            <div className="text-[13px] text-ink-2">
              {t("settingsPage.observability.retention")}
            </div>
            <div className="text-[12px] text-ink-3">
              {t("settingsPage.observability.retentionHint")}
            </div>
          </div>
          <input
            type="number"
            min={1}
            value={settings.observability.retention_days}
            onChange={(e) =>
              patch({
                observability: {
                  ...settings.observability,
                  retention_days: Number(e.target.value) || 0,
                },
              })
            }
            className="w-20 rounded-md border border-border bg-surface px-2 py-1 text-[12px] text-ink"
          />
        </div>
      </div>
    </Section>
  );
});
