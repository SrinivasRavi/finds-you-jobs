import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import { useMasterProfileExists, useSSEInvalidation } from "./api/queries";
import { Dev } from "./surfaces/Dev";
import { JobBoard } from "./surfaces/JobBoard";
import { Placeholder } from "./surfaces/Placeholder";
import { Tracker } from "./surfaces/Tracker";
import { Layout } from "./shell/Layout";
import { installExternalLinkInterceptor } from "./shell/openExternal";
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
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 0, refetchOnWindowFocus: false } },
});

// First-launch guard (FR-OB-01 / US-OB-01): `MasterProfile` exists ⟺ onboarded.
// No profile → the app opens onboarding and the board is unreachable; a completed
// (or pre-existing) profile → the wizard can't reopen and `/` lands on the board.
// The query resolves before first paint (returns null while pending, so no board
// flash before the redirect).
// eslint-disable-next-line react-refresh/only-export-components -- entry-file guard, not HMR'd
function GuardedLayout() {
  const { data: onboarded, isPending } = useMasterProfileExists();
  if (isPending) return null;
  if (!onboarded) return <Navigate to="/onboarding" replace />;
  return <Layout />;
}

// eslint-disable-next-line react-refresh/only-export-components -- entry-file guard, not HMR'd
function OnboardingRoute() {
  const { data: onboarded, isPending } = useMasterProfileExists();
  if (isPending) return null;
  if (onboarded) return <Navigate to="/jobs" replace />;
  return <Placeholder name="Onboarding" commit="onboarding" />;
}

// Explicit React Router config (the pinned choice — architecture §6). Routes are
// internal navigation only; this is a desktop app, no URL-bar-driven flows.
const router = createBrowserRouter([
  { path: "/onboarding", element: <OnboardingRoute /> },
  {
    path: "/",
    element: <GuardedLayout />,
    children: [
      { index: true, element: <Navigate to="/jobs" replace /> },
      { path: "jobs", element: <JobBoard /> },
      { path: "applications", element: <Tracker /> },
      { path: "networking", element: <Placeholder name="Networking" commit="referral-outreach" /> },
      { path: "dev", element: <Dev /> },
      { path: "analytics", element: <Placeholder name="Analytics" commit="observability" /> },
      { path: "logs", element: <Navigate to="/analytics" replace /> },
      { path: "settings", element: <Placeholder name="Settings" commit="settings-surface" /> },
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
