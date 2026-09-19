// English — the shell namespace (shared modal/dialog/filter primitives, the
// app chrome banners, and cross-surface display labels).
const shell = {
  close: "Close",
  confirm: "Confirm",
  cancel: "Cancel",
  search: "Search",
  clearSearch: "Clear search",
  moreDetail: "More detail",
  // Invisible sizer that pins all three "Deleted …" header buttons to the
  // width of the longest label (HeaderAddButton.tsx).
  deletedSizerLabel: "Deleted Applications",
  bootSplash: "Starting the local backend… first launch can take a moment.",
  // Live-update stream gap (Layout.tsx, F-M5) — data refetches automatically
  // once the stream is back.
  streamReconnecting:
    "Reconnecting to the local backend… live updates are paused and will catch up automatically.",
  backendStoppedFallback: "the backend stopped responding",
  sidecarFatalBanner:
    "Backend stopped: {{message}}. Nothing you do will save until you quit and reopen the app.",
  // Degraded boot (Layout.tsx): the shell saw 3 runs in a row end the same bad
  // way and started the backend without its scheduler, so nothing queues itself
  // into whatever killed them. Dismissible; the button is the way back.
  degradedBoot:
    "Background work is paused. The app closed unexpectedly 3 times in a row, so this session started without it.",
  degradedBootResume: "Resume background work",
  degradedBootDismiss: "Dismiss",
  work: {
    remote: "Remote",
    hybrid: "Hybrid",
    onsite: "Onsite",
  },
  // Directly-editable markdown surface (MarkdownEditor.tsx) — toolbar labels.
  mdEditor: {
    bold: "Bold",
    italic: "Italic",
    h1: "Heading 1",
    h2: "Heading 2",
    h3: "Heading 3",
    paragraph: "Paragraph",
    bullet: "Bulleted list",
    numbered: "Numbered list",
    quote: "Quote",
    code: "Inline code",
    link: "Link",
    placeholder: "Write here, or paste your own resume text…",
  },
  // Route-level error boundary (SurfaceError.tsx) — contains a surface crash
  // instead of letting it hijack the whole app (2026-07-24 customer bug).
  surfaceError: {
    title: "Something went wrong here",
    body: "This view hit an unexpected error and was contained — the rest of the app keeps working and your data is safe. The technical details were logged to the developer console.",
    reload: "Reload app",
  },
  // Global failed-write banner (MutationErrorBanner.tsx) — no mutation error
  // may vanish silently.
  mutationError: {
    body: "That change didn't save — the app hit an error talking to the local backend.",
    dismiss: "Dismiss",
    // Overflow line when a burst of failures exceeds the visible stack (F-L12).
    more_one: "+{{count}} earlier failure not shown",
    more_other: "+{{count}} earlier failures not shown",
  },
};

export default shell;
