// English — the browser namespace: the core browser surface (screencast canvas
// + URL bar). Vendor-agnostic — the connection state ("live" / "connecting") is
// left untranslated on purpose (a machine-readable status the surface asserts on,
// not prose). The LinkedIn Browser tab reuses this same surface.
const browser = {
  title: "Browser",
  urlPlaceholder: "https://example.com",
  go: "Go",
  framesLabel: "frames",
  noPage: "no page yet",
};

export default browser;
