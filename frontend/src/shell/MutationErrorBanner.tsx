// Global failed-write surfacing (2026-07-24): every mutation error is logged
// by the QueryClient's MutationCache (main.tsx) and re-broadcast as a
// `fyj:mutation-error` window event; this banner shows the latest one until
// dismissed. Before this, a failed archive/move/save died silently — the user
// clicked, nothing happened, nothing was logged.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export const MUTATION_ERROR_EVENT = "fyj:mutation-error";

const AUTO_DISMISS_MS = 8000;

export function MutationErrorBanner() {
  const { t } = useTranslation();
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const onError = (e: Event) => {
      setError((e as CustomEvent).detail ?? new Error("unknown"));
    };
    window.addEventListener(MUTATION_ERROR_EVENT, onError);
    return () => window.removeEventListener(MUTATION_ERROR_EVENT, onError);
  }, []);
  // Auto-dismiss: the banner informs, it must never become a lingering
  // obstacle (it once sat over a modal's Save button and blocked the click).
  useEffect(() => {
    if (error == null) return;
    const timer = setTimeout(() => setError(null), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [error]);
  if (error == null) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    // pointer-events-none wrapper: clicks pass THROUGH the banner to whatever
    // is beneath; only the dismiss button itself is interactive.
    <div
      role="alert"
      data-testid="mutation-error-banner"
      className="pointer-events-none fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-lg border border-bad/40 bg-bad-wash px-4 py-2 text-[12.5px] text-bad shadow-lg"
    >
      {t("shell.mutationError.body")}
      <span className="ml-2 font-mono text-[11px] opacity-80">{message.slice(0, 120)}</span>
      <button
        onClick={() => setError(null)}
        className="pointer-events-auto ml-3 underline"
      >
        {t("shell.mutationError.dismiss")}
      </button>
    </div>
  );
}
