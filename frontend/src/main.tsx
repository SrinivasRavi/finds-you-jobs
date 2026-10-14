import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { useTranslation } from "react-i18next";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import { useMasterProfileExists, useSSEInvalidation } from "./api/queries";
import { Analytics } from "./surfaces/Analytics";
import { BrowserSurface } from "./surfaces/BrowserSurface";
import { Dev } from "./surfaces/Dev";
import { JobBoard } from "./surfaces/JobBoard";
import { Networking } from "./surfaces/Networking";
import { Onboarding } from "./surfaces/Onboarding";
import { Settings } from "./surfaces/Settings";
import { Tracker } from "./surfaces/Tracker";
import { Layout } from "./shell/Layout";
import { SurfaceError } from "./shell/SurfaceError";
import { installExternalLinkInterceptor } from "./shell/openExternal";
// i18n init (side-effect import): registers bundled locales and applies the
// persisted language before first paint.
import "./i18n";
// Fonts bundled locally (MIT/OFL) — the packaged app's loopback-only CSP blocks
// external hosts, so no Google Fonts link (ROADMAP A4; THIRD_PARTY_NOTICES).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
// Quicksand — the rounded brand wordmark ("finds you jobs.") in the left rail.
import "@fontsource/quicksand/500.css";
import "@fontsource/quicksand/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./index.css";

// One QueryClient for the app session. SSE events invalidate keys (queries.ts).
// The MutationCache hook is the no-silent-failure net (2026-07-24): every
// failed write is logged AND surfaced via the Layout's MutationErrorBanner —
// before this a failed archive/move/save showed nothing anywhere.
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 0, refetchOnWindowFocus: false } },
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      console.error("[finds-you-jobs] mutation failed:", error);
      // Hooks whose surfaces already render the failure inline declare
      // `meta.errorHandledLocally` — the global banner is the net for the
      // UNHANDLED rest, not a second voice over handled ones.
      if (mutation.meta?.errorHandledLocally) return;
      window.dispatchEvent(new CustomEvent("fyj:mutation-error", { detail: error }));
    },
  }),
});

// First-launch guard (FR-OB-01): a profile exists if and only if onboarding is
// done. This splash shows instead of a blank window while the backend boots (slow
// on a first Windows launch); the profile query retries forever, so it self-heals.
// eslint-disable-next-line react-refresh/only-export-components -- entry-file boot state, not HMR'd
function BootSplash() {
  const { t } = useTranslation();
  return (
    <div
      data-testid="boot-splash"
      className="grid h-screen place-items-center bg-surface-2"
    >
      <div className="text-center">
        <div className="font-[Quicksand] text-[18px] font-semibold text-ink">
          finds you jobs.
        </div>
        <div className="mt-2 text-[12.5px] text-ink-3">
          {t("shell.bootSplash")}
        </div>
      </div>
    </div>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- entry-file guard, not HMR'd
function GuardedLayout() {
  const { data: onboarded, isPending } = useMasterProfileExists();
  if (isPending) return <BootSplash />;
  if (!onboarded) return <Navigate to="/onboarding" replace />;
  return <Layout />;
}

// eslint-disable-next-line react-refresh/only-export-components -- entry-file guard, not HMR'd
function OnboardingRoute() {
  const { data: onboarded, isPending } = useMasterProfileExists();
  if (isPending) return <BootSplash />;
  if (onboarded) return <Navigate to="/jobs" replace />;
  return <Onboarding />;
}

// Explicit router config (architecture section 6); internal navigation only.
// Every route carries the SurfaceError boundary: a child error renders inside the
// Layout so the rail stays usable, and a root error or unmatched path gets it full
// page, instead of React Router's developer page.
const router = createBrowserRouter([
  { path: "/onboarding", element: <OnboardingRoute />, errorElement: <SurfaceError /> },
  {
    path: "/",
    element: <GuardedLayout />,
    errorElement: <SurfaceError />,
    children: [
      { index: true, element: <Navigate to="/jobs" replace /> },
      { path: "jobs", element: <JobBoard />, errorElement: <SurfaceError /> },
      { path: "applications", element: <Tracker />, errorElement: <SurfaceError /> },
      { path: "networking", element: <Networking />, errorElement: <SurfaceError /> },
      { path: "dev", element: <Dev />, errorElement: <SurfaceError /> },
      { path: "browser", element: <BrowserSurface />, errorElement: <SurfaceError /> },
      { path: "analytics", element: <Analytics />, errorElement: <SurfaceError /> },
      { path: "logs", element: <Navigate to="/analytics" replace /> },
      { path: "settings", element: <Settings />, errorElement: <SurfaceError /> },
    ],
  },
]);

// eslint-disable-next-line react-refresh/only-export-components -- entry-file root, not HMR'd
function Root() {
  useSSEInvalidation(queryClient);
  return <RouterProvider router={router} />;
}

// Every external <a target="_blank"> opens in the OS browser — the WebView
// blocks them otherwise (2026-07-11 beta feedback: no links opened at all).
installExternalLinkInterceptor();

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("root element not found");
}

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Root />
    </QueryClientProvider>
  </StrictMode>,
);
