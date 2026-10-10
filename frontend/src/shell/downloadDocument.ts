// Save an attached application document to disk.
//
// The bearer token can't ride on a plain href, so the blob is fetched and
// handed to a temporary anchor. 4 copies of this routine had grown 3 different
// failure behaviours (local state, the global banner, throw), and only one of
// them named the file when the upload had no filename.

import { api } from "../api";
import i18n from "../i18n";
import type { ApplicationDocument } from "../api/types";

const DOC_KIND_KEY: Record<ApplicationDocument["kind"], string> = {
  tailored_resume: "tracker.docKind.tailored_resume",
  cover_letter: "tracker.docKind.cover_letter",
};

/** Fetch and save `doc`. Rejects on failure so a caller that renders its own
 *  error can; callers with no error surface use `downloadDocumentOrReport`. */
export async function downloadDocument(doc: ApplicationDocument): Promise<void> {
  const blob = await api.fetchDocument(doc.document_id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = doc.filename || i18n.t(DOC_KIND_KEY[doc.kind] ?? doc.kind);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Same, but a failure goes to the global MutationErrorBanner, never a silent
 *  no-op (2026-07-24 graceful-failure audit). */
export function downloadDocumentOrReport(doc: ApplicationDocument): void {
  void downloadDocument(doc).catch((e: unknown) => {
    console.error("[finds-you-jobs] document download failed:", e);
    window.dispatchEvent(new CustomEvent("fyj:mutation-error", { detail: e }));
  });
}
