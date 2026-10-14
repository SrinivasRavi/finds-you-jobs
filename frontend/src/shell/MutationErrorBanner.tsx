// Global failed-write banner: every mutation error is re-broadcast by main.tsx
// as a `fyj:mutation-error` event and shown here until dismissed, so a failed
// write is never silent. A burst stacks (F-L12); past MAX_STACK it's a count.
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
  // Auto-dismiss, so the banner never blocks a click (it once sat over a Save).
  // The timer keys to the head entry's identity: new arrivals mustn't reset its
  // clock, or a steady failure cadence would never drain the stack.
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
