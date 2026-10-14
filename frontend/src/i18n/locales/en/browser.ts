// English — the browser namespace: the core watch-only browser surface
// (screencast canvas + read-only URL line). Vendor-agnostic — the connection
// state ("live" / "connecting") is left untranslated on purpose (a
// machine-readable status the surface asserts on, not prose).
const browser = {
  framesLabel: "frames",
  noPage: "no page yet",
  download: {
    button: "Download Chromium",
    downloading: "Downloading Chromium… this takes a minute or two",
    stopped: "The download stopped before it finished. Try again.",
    surfaceMissing: "This browser needs Chrome or Chromium, and neither is on this computer. Download Chromium, a one-time download.",
    applyMissing: "Applying needs Chrome or Chromium, and neither is on this computer. Download Chromium, a one-time download, then apply again.",
    applyReady: "Chromium is ready. Apply again.",
  },
  applierTab: {
    show: "Show in browser",
    gone: "The form's tab is closed.",
  },
};

export default browser;
