// Is this the packaged desktop app, or a browser dev/test page?
//
// The Tauri webview injects `__TAURI_INTERNALS__`; nothing else does. 5 modules
// each had their own copy of this predicate and 4 of them re-declared the same
// global augmentation, which a `declare global` only needs once.

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

/** True only inside the packaged desktop app. */
export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
