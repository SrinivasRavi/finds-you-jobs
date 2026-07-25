// Global failed-write surfacing (2026-07-24): every mutation error is logged
// by the QueryClient's MutationCache (main.tsx) and re-broadcast as a
// `fyj:mutation-error` window event; this banner shows recent ones until
// dismissed. Before this, a failed archive/move/save died silently — the user
// clicked, nothing happened, nothing was logged. A burst of failed writes
// stacks (latest last) instead of collapsing to one flash (F-L12); beyond
// MAX_STACK the overflow is summarized as a count.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export const MUTATION_ERROR_EVENT = "fyj:mutation-error";

const AUTO_DISMISS_MS = 8000;
const MAX_STACK = 3;

interface BannerError {
  id: number;
  message: string;
}

// Errors + overflow live in ONE state object so a single PURE updater computes
// both — the previous shape called setOverflow inside the setErrors updater,
// and StrictMode's double-invoked updaters (dev) inflated the overflow count.
interface BannerState {
  errors: BannerError[];
  overflow: number;
}

const EMPTY: BannerState = { errors: [], overflow: 0 };

export function MutationErrorBanner() {
  const { t } = useTranslation();
  const [state, setState] = useState<BannerState>(EMPTY);
  const nextId = useRef(0);
  useEffect(() => {
    const onError = (e: Event) => {
      const detail: unknown = (e as CustomEvent).detail ?? new Error("unknown");
      const message = detail instanceof Error ? detail.message : String(detail);
      const id = nextId.current++;
      setState((prev) => {
        const next = [...prev.errors, { id, message }];
        if (next.length > MAX_STACK) {
          return {
            errors: next.slice(next.length - MAX_STACK),
            overflow: prev.overflow + (next.length - MAX_STACK),
          };
        }
        return { ...prev, errors: next };
      });
    };
    window.addEventListener(MUTATION_ERROR_EVENT, onError);
    return () => window.removeEventListener(MUTATION_ERROR_EVENT, onError);
  }, []);
  // Auto-dismiss: the banner informs, it must never become a lingering
  // obstacle (it once sat over a modal's Save button and blocked the click).
  // The timer is keyed to the HEAD ENTRY'S IDENTITY, not the array: new
  // arrivals must not reset the head's clock, or a sustained failure cadence
  // (< AUTO_DISMISS_MS apart) would keep the stack from ever draining. Each
  // head expires on its own clock and the burst drains oldest-first.
  const headId = state.errors[0]?.id;
  useEffect(() => {
    if (headId == null) return;
    const timer = setTimeout(
      () =>
        setState((prev) => {
          const errors = prev.errors.filter((err) => err.id !== headId);
          // The overflow count only means something while its stack is on
          // screen — clear it when the last entry drains.
          return errors.length === 0 ? EMPTY : { ...prev, errors };
        }),
      AUTO_DISMISS_MS,
    );
    return () => clearTimeout(timer);
  }, [headId]);
  const { errors, overflow } = state;
  if (errors.length === 0) return null;
  const dismissAll = () => setState(EMPTY);
  return (
    // pointer-events-none wrapper: clicks pass THROUGH the banner to whatever
    // is beneath; only the dismiss button itself is interactive.
    <div
      role="alert"
      data-testid="mutation-error-banner"
      className="pointer-events-none fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-lg border border-bad/40 bg-bad-wash px-4 py-2 text-[12.5px] text-bad shadow-lg"
    >
      {t("shell.mutationError.body")}
      <button
        onClick={dismissAll}
        className="pointer-events-auto ml-3 underline"
      >
        {t("shell.mutationError.dismiss")}
      </button>
      {errors.map((err) => (
        <div key={err.id} className="mt-1 font-mono text-[11px] opacity-80">
          {err.message.slice(0, 120)}
        </div>
      ))}
      {overflow > 0 ? (
        <div className="mt-1 text-[11px] opacity-80">
          {t("shell.mutationError.more", { count: overflow })}
        </div>
      ) : null}
    </div>
  );
}
