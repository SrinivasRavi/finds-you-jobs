// The upload formats each door offers, read off the sidecar rather than guessed.
//
// 2 server lists, not 1, and the doors had drifted from both: `documents.py`
// ALLOWED_TYPES is what we STORE as a binary attachment, `doc_extract.py`
// SUPPORTED_SUFFIXES is what we can PARSE into the editor. `.markdown` extracts
// but isn't a storable attachment type, which is the only difference.

/** Attach-as-submitted-document (mirrors `app/documents.ALLOWED_TYPES`). */
export const ATTACH_ACCEPT =
  ".pdf,.docx,.odt,.pages,.txt,.md,.rtf,.doc,application/pdf," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.oasis.opendocument.text,application/vnd.apple.pages," +
  "text/plain,text/markdown,application/rtf,application/msword";

/** Extract-into-the-editor (mirrors `app/doc_extract.SUPPORTED_SUFFIXES`).
 *  `.doc` is offered so its honest "export to PDF/DOCX" message fires instead
 *  of the file picker silently refusing to show it. */
export const EXTRACT_ACCEPT = `${ATTACH_ACCEPT},.markdown`;
