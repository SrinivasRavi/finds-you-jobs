// The upload formats each door offers, read off the sidecar rather than guessed.
//
// 2 server lists, not 1, and the doors had drifted from both: `documents.py`
// ALLOWED_TYPES is what we STORE as a binary attachment, `doc_extract.py`
// SUPPORTED_SUFFIXES is what we can PARSE into the editor. `.markdown` extracts
// but isn't a storable attachment type, which is the only difference.

import type { components } from "../api/schema";

type Shared = components["schemas"]["SharedConstants"];

/** Attach-as-submitted-document: `app/documents.ALLOWED_TYPES`, checked against
 *  it (sidecar/app/shared_constants.py), so a drift is a compile error. */
const ATTACH_TYPES: Shared["attach_types"] = {
  ".pdf": "application/pdf",
  ".doc": "application/msword",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".odt": "application/vnd.oasis.opendocument.text",
  ".pages": "application/vnd.apple.pages",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".rtf": "application/rtf",
};
export const ATTACH_ACCEPT = [...Object.keys(ATTACH_TYPES), ...Object.values(ATTACH_TYPES)].join(",");

/** Extract-into-the-editor: `app/doc_extract.SUPPORTED_SUFFIXES`, checked the
 *  same way. `.doc` is offered so its honest "export to PDF/DOCX" message fires
 *  instead of the file picker silently refusing to show it. */
const EXTRACT_SUFFIXES: Record<Shared["extract_suffix"], true> = {
  ".pdf": true,
  ".doc": true,
  ".docx": true,
  ".odt": true,
  ".pages": true,
  ".rtf": true,
  ".txt": true,
  ".md": true,
  ".markdown": true,
};
export const EXTRACT_ACCEPT = [
  ...Object.keys(EXTRACT_SUFFIXES),
  ...Object.values(ATTACH_TYPES),
].join(",");
