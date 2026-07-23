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
  backendStoppedFallback: "the backend stopped responding",
  sidecarFatalBanner:
    "Backend stopped: {{message}}. Nothing you do will save until you quit and reopen the app.",
  work: {
    remote: "Remote",
    hybrid: "Hybrid",
    onsite: "Onsite",
  },
  rescore: {
    title: "Re-score jobs with AI?",
    // Interpolated into the body strings below (with a leading space) when
    // some jobs already carry an AI score and are skipped.
    skipped_one: "({{count}} already has an AI score for this resume — skipped.)",
    skipped_other: "({{count}} already have an AI score for this resume — skipped.)",
    bodyResumeEdit_one:
      "Your resume changed. Re-score {{count}} job against it with AI?{{skipped}} This uses your LLM key — one call per job. Or keep the current scores; you can re-score anytime by editing your resume again.",
    bodyResumeEdit_other:
      "Your resume changed. Re-score {{count}} jobs against it with AI?{{skipped}} This uses your LLM key — one call per job. Or keep the current scores; you can re-score anytime by editing your resume again.",
    bodyModeSwitch_one:
      "Score the {{count}} job on your board that has no AI score yet?{{skipped}} This uses your LLM key — one call per job. New jobs from future scans are AI-scored automatically either way.",
    bodyModeSwitch_other:
      "Score the {{count}} jobs on your board that have no AI score yet?{{skipped}} This uses your LLM key — one call per job. New jobs from future scans are AI-scored automatically either way.",
    busy: "Re-scoring…",
    confirm_one: "Re-score {{count}} job",
    confirm_other: "Re-score {{count}} jobs",
    keepScores: "Keep current scores",
  },
};

export default shell;
