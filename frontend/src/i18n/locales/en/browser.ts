// English — the browser namespace: the core watch-only browser surface
// (screencast canvas + read-only URL line). Vendor-agnostic — the connection
// state ("live" / "connecting") is left untranslated on purpose (a
// machine-readable status the surface asserts on, not prose).
const browser = {
  framesLabel: "frames",
  noPage: "no page yet",
};

export default browser;
