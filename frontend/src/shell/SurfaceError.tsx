// Route-level error boundary (2026-07-24, after a customer-reported crash
// took down the whole app): a render error in one surface is contained to that
// surface. Child routes mount this inside the Layout's Outlet — the left rail
// stays alive and navigating away recovers; the root route shows the same
// panel full-screen. The error is logged, never swallowed.
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useRouteError } from "react-router-dom";

export function SurfaceError() {
  const { t } = useTranslation();
  const error = useRouteError();
  useEffect(() => {
    console.error("[finds-you-jobs] surface error:", error);
  }, [error]);
  return (
    <div
      data-testid="surface-error"
      className="grid h-full min-h-[50vh] flex-1 place-items-center bg-canvas p-8"
    >
      <div className="max-w-[420px] text-center">
        <div className="text-[15px] font-semibold text-ink">
          {t("shell.surfaceError.title")}
        </div>
        <p className="mt-2 text-[12.5px] leading-relaxed text-ink-3">
          {t("shell.surfaceError.body")}
        </p>
        <button
          onClick={() => window.location.assign("/")}
          className="mt-4 rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
        >
          {t("shell.surfaceError.reload")}
        </button>
      </div>
    </div>
  );
}
