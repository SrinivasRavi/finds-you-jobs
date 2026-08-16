// The `openResumeModal` trio — one shared component switched by `kind`
// (US-RES-01, US-RES-02, US-CL-01).
//   master  → single-column editor, Preview⇄Raw toggle, MASTER pill, Upload PDF.
//   tailored→ two-column (master read-only | tailored editable), TAILORED pill,
//             Generate at the top, fabrication-guard NOTES rail, stale hint.
//   cover   → single-column tailored editor, COVER LETTER pill.
// Preview is ONE directly-editable rendered surface (contentEditable WYSIWYG-
// lite; markdown stays the document of record — see shell/mdHtml.ts); Raw is
// the same pane as markdown source. The variant box is always editable, so a
// user can paste their own (from their own ChatGPT/Gemini). packetState only
// gates the generating spinner.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/index";

import type { Application, ApplicationDocument, PacketState, Profile } from "../api/types";
import { Icon } from "../shell/icons";
import { MarkdownEditor } from "../shell/MarkdownEditor";
import { Modal } from "../shell/Modal";

export type ResumeModalKind = "master" | "tailored" | "cover";

type Mode = "preview" | "raw";

// i18n key maps — translated with t(...) at render.
const PILL: Record<ResumeModalKind, { label: string; cls: string }> = {
  master: { label: "popups.resume.pill.master", cls: "bg-good-wash text-good" },
  tailored: { label: "popups.resume.pill.tailored", cls: "bg-accent-wash text-accent" },
  cover: { label: "popups.resume.pill.cover", cls: "bg-purple-wash text-purple" },
};

const BLURB: Record<ResumeModalKind, string> = {
  master: "popups.resume.blurb.master",
  tailored: "popups.resume.blurb.tailored",
  cover: "popups.resume.blurb.cover",
};

function PacketPill({ state }: { state: PacketState }) {
  const { t } = useTranslation();
  const map: Record<PacketState, [string, string]> = {
    approved: [t("popups.resume.packet.approved"), "bg-good-wash text-good"],
    ready: [t("popups.resume.packet.ready"), "bg-warn-wash text-warn"],
    generating: [t("popups.resume.packet.generating"), "bg-surface-3 text-ink-3"],
    none: [t("popups.resume.packet.none"), "bg-surface-3 text-ink-3"],
    failed: [t("popups.resume.packet.failed"), "bg-bad-wash text-bad"],
  };
  // Defensive: an unknown server state must degrade to the grey pill, never
  // crash the destructure (2026-07-24 unsafe-lookup audit).
  const [label, cls] = map[state] ?? map.none;
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] ${cls}`}>
      {label}
    </span>
  );
}

/**
 * Share dropdown (US-RES-01/02, US-CL-01) — header button next to ×:
 * "Copy <this document> to clipboard" (Markdown, for ATS forms) + "Export to PDF"
 * (browser print → Save as PDF; real selectable text). `what` names the document
 * so the copy action is unambiguous ("Copy tailored resume to clipboard").
 */
function ShareDropdown({ getMarkdown, what }: { getMarkdown: () => string; what: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [exported, setExported] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  return (
    <div className="relative">
      <button
        data-testid="share-btn"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-[28px] items-center gap-1 rounded-7 border border-border-2 bg-surface px-2.5 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
      >
        {t("popups.resume.share.share")}
      </button>
      {open ? (
        <div className="absolute right-0 top-[32px] z-10 w-60 overflow-hidden rounded-lg border border-border bg-surface shadow-lg">
          <button
            data-testid="share-copy-md"
            onClick={() => {
              void navigator.clipboard?.writeText(getMarkdown());
              setCopied(true);
              setTimeout(() => {
                setCopied(false);
                setOpen(false);
              }, 900);
            }}
            className="block w-full px-3 py-2 text-left text-[12.5px] text-ink-2 hover:bg-surface-2"
          >
            {copied ? t("popups.resume.share.copied") : t("popups.resume.share.copyToClipboard", { what })}
            <span className="mt-0.5 block text-[10.5px] text-ink-4">
              {t("popups.resume.share.copyHint")}
            </span>
          </button>
          <button
            data-testid="share-export-pdf"
            disabled={exporting}
            onClick={() => {
              // The webview can't print or download — the sidecar renders the
              // PDF (real selectable text) straight into ~/Downloads.
              setExporting(true);
              setExportError(null);
              void Promise.resolve(api.exportPdf(getMarkdown(), what))
                .then((path) => setExported(path))
                .catch((e: unknown) =>
                  setExportError(e instanceof Error ? e.message : t("popups.resume.share.exportFailed")),
                )
                .finally(() => setExporting(false));
            }}
            className="block w-full px-3 py-2 text-left text-[12.5px] text-ink-2 hover:bg-surface-2 disabled:opacity-50"
          >
            {exporting
              ? t("popups.resume.share.exporting")
              : exported
                ? t("popups.resume.share.exported")
                : t("popups.resume.share.exportPdf")}
            <span className="mt-0.5 block break-all text-[10.5px] text-ink-4" data-testid="share-export-result">
              {exportError
                ? exportError
                : exported
                  ? t("popups.resume.share.savedTo", { path: exported })
                  : t("popups.resume.share.exportHint")}
            </span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** Read-only viewer for a document the user submitted for a MANUAL application
 *  (FR-TR manual-add). The file is binary (usually a PDF), so we fetch the blob
 *  (authed) and either embed it (PDF / text / image render inline) or offer a
 *  download when the format can't preview. Nothing here is editable. */
function SubmittedDocViewer({ doc }: { doc: ApplicationDocument }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let revoked = false;
    let objUrl: string | null = null;
    api
      .fetchDocument(doc.document_id)
      .then((blob) => {
        if (revoked) return;
        objUrl = URL.createObjectURL(blob);
        setUrl(objUrl);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      revoked = true;
      if (objUrl) URL.revokeObjectURL(objUrl);
    };
  }, [doc.document_id]);

  const embeddable =
    doc.mime_type === "application/pdf" ||
    doc.mime_type.startsWith("text/") ||
    doc.mime_type.startsWith("image/");

  function download() {
    api
      .fetchDocument(doc.document_id)
      .then((blob) => {
        const dl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = dl;
        a.download = doc.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(dl);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }

  if (error) {
    return (
      <div className="grid h-full place-items-center text-[12.5px] text-bad" data-testid="submitted-doc-error">
        {t("popups.resume.submittedDoc.loadError", { filename: doc.filename, error })}
      </div>
    );
  }
  if (embeddable) {
    return url ? (
      <iframe
        src={url}
        title={doc.filename}
        data-testid="submitted-doc-frame"
        className="h-full w-full rounded-md border border-border bg-surface"
      />
    ) : (
      <div className="grid h-full place-items-center text-[12.5px] text-ink-3">{t("popups.resume.submittedDoc.loading")}</div>
    );
  }
  return (
    <div className="grid h-full place-items-center text-center" data-testid="submitted-doc-download">
      <div className="space-y-3">
        <p className="text-[12.5px] text-ink-3">
          {t("popups.resume.submittedDoc.noPreview", { filename: doc.filename })}
        </p>
        <button
          onClick={download}
          className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
        >
          {t("popups.resume.submittedDoc.downloadToView")}
        </button>
      </div>
    </div>
  );
}

function DownloadDocButton({ doc }: { doc: ApplicationDocument }) {
  const { t } = useTranslation();
  return (
    <button
      data-testid="submitted-doc-download-btn"
      onClick={() => {
        void api
          .fetchDocument(doc.document_id)
          .then((blob) => {
            const dl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = dl;
            a.download = doc.filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(dl);
          })
          .catch((e: unknown) => {
            // Never a silent no-op (2026-07-24 graceful-failure audit).
            console.error("[finds-you-jobs] document download failed:", e);
            window.dispatchEvent(new CustomEvent("fyj:mutation-error", { detail: e }));
          });
      }}
      className="inline-flex h-[28px] items-center gap-1 rounded-7 border border-border-2 bg-surface px-2.5 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
    >
      {t("popups.resume.submittedDoc.download")}
    </button>
  );
}

const MODE_LABEL: Record<Mode, string> = {
  preview: "popups.resume.mode.preview",
  raw: "popups.resume.mode.raw",
};

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  const { t } = useTranslation();
  return (
    <div className="inline-flex overflow-hidden rounded-7 border border-border text-[11.5px]">
      {(["preview", "raw"] as Mode[]).map((m) => (
        <button
          key={m}
          data-testid={`mode-${m}`}
          onClick={() => setMode(m)}
          className={
            "px-2.5 py-1 " +
            (mode === m ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3")
          }
        >
          {t(MODE_LABEL[m])}
        </button>
      ))}
    </div>
  );
}

// Every format the sidecar can EXTRACT (master resume → editor) or STORE as a
// binary attachment (tailored/cover → submitted document). One list so all
// three uploaders offer the same set (pdf/docx/odt/pages/txt/md/rtf/doc).
const UPLOAD_ACCEPT =
  ".pdf,.docx,.odt,.pages,.txt,.md,.rtf,.doc,application/pdf," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.oasis.opendocument.text,application/vnd.apple.pages," +
  "text/plain,text/markdown,application/rtf,application/msword";

/** The shared "Upload" control (icon + short label). It only PICKS a file and
 *  hands it to `onFile`; the caller decides what to do — extract-to-editor for
 *  the master resume, attach-as-submitted-document for the tailored / cover
 *  variant. Supports pdf/docx/odt/pages/txt/md/rtf/doc. */
function UploadButton({
  onFile,
  busy,
  error,
  testid,
}: {
  onFile: (file: File) => void;
  busy: boolean;
  error: string | null;
  testid: string;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement | null>(null);
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={UPLOAD_ACCEPT}
        data-testid={`${testid}-input`}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // allow re-selecting the same file
          if (file) onFile(file);
        }}
      />
      <button
        type="button"
        data-testid={testid}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        title={error ? t("popups.resume.uploadFailed", { error }) : t("popups.resume.uploadTitle")}
        className={
          "inline-flex h-[28px] shrink-0 items-center gap-1 rounded-7 border px-2.5 text-[12px] font-medium hover:bg-surface-3 disabled:opacity-50 " +
          (error ? "border-bad text-bad" : "border-border-2 bg-surface text-ink-2")
        }
      >
        <Icon name="upload" size={13} strokeWidth={2} />
        {busy ? t("popups.resume.uploading") : t("popups.resume.upload")}
      </button>
    </>
  );
}

/** The attached submitted-document chip (tailored/cover) — the exact file the
 *  user will submit on Apply, sitting beside any generated variant. Click the
 *  name to download it verbatim; ✕ detaches it. */
function AttachedDocChip({ doc, onRemove }: { doc: ApplicationDocument; onRemove: () => void }) {
  const { t } = useTranslation();
  const download = () => {
    void api
      .fetchDocument(doc.document_id)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = doc.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch((e: unknown) => {
        console.error("[finds-you-jobs] document download failed:", e);
        window.dispatchEvent(new CustomEvent("fyj:mutation-error", { detail: e }));
      });
  };
  return (
    <span
      data-testid="attached-doc-chip"
      className="inline-flex h-[28px] max-w-[260px] shrink-0 items-center gap-1.5 rounded-7 border border-accent/40 bg-accent-wash px-2.5 text-[12px] text-accent-ink"
      title={t("popups.resume.attachedTitle", { filename: doc.filename })}
    >
      <Icon name="file" size={13} strokeWidth={2} />
      <button onClick={download} data-testid="attached-doc-download" className="truncate hover:underline">
        {doc.filename}
      </button>
      <button
        onClick={onRemove}
        data-testid="attached-doc-remove"
        aria-label={t("popups.resume.removeAttachment")}
        className="shrink-0 text-accent-ink/70 hover:text-bad"
      >
        <Icon name="x" size={13} strokeWidth={2} />
      </button>
    </span>
  );
}

export function ResumeModal({
  kind,
  onClose,
  profile,
  application,
  submittedDoc,
  attachedDoc,
  onAttachDocument,
  onRemoveDocument,
  onSaveMaster,
  onApprove,
  onSaveVariant,
  onRegenerate,
}: {
  kind: ResumeModalKind;
  onClose: () => void;
  profile: Profile;
  application?: Application;
  /** For a MANUAL application: the resume/cover the user actually submitted.
   *  When set, this popup is a read-only viewer of that file, not the
   *  generate/tailor flow. */
  submittedDoc?: ApplicationDocument;
  /** The external file attached to THIS application's tailored/cover slot (the
   *  document submitted on Apply). Shown as a chip beside the editable variant;
   *  coexists with a generated markdown variant. */
  attachedDoc?: ApplicationDocument;
  /** Attach an external file (tailored/cover Upload button). Resolves once the
   *  upload persists so the button can clear its busy/error state. */
  onAttachDocument?: (file: File) => Promise<void> | void;
  /** Detach the attached file (the chip ✕). */
  onRemoveDocument?: () => Promise<void> | void;
  onSaveMaster?: (md: string) => void;
  onApprove?: (markdown: string) => void;
  onSaveVariant?: (markdown: string) => void;
  onRegenerate?: () => void;
}) {
  const { t } = useTranslation();
  const isMaster = kind === "master";
  const submitted = !isMaster ? submittedDoc : undefined;
  // Per-artifact state (US-RES-02 / US-CL-01): the resume and cover slots are
  // independent — read the one this popup shows so a generating cover never
  // blanks the resume editor and vice-versa.
  const packet: PacketState = isMaster
    ? "approved"
    : kind === "cover"
      ? (application?.packet_cover_state ?? "none")
      : (application?.packet_resume_state ?? "none");
  // Stale-variant warning (FR-RES-03): the variant was generated from an older
  // master version than the one on disk now.
  const variantVersion =
    kind === "cover" ? application?.cover_profile_version : application?.tailored_profile_version;
  const stale =
    !isMaster &&
    variantVersion != null &&
    profile.version != null &&
    variantVersion < profile.version;

  const initial =
    kind === "master"
      ? profile.master_md
      : kind === "cover"
        ? (application?.cover_letter_md ?? "")
        : (application?.tailored_resume_md ?? "");
  const notes = kind === "cover" ? (application?.cover_notes ?? []) : (application?.tailored_notes ?? []);

  const [value, setValue] = useState(initial);
  const [mode, setMode] = useState<Mode>("preview");
  const [dirty, setDirty] = useState(false);
  // Master upload EXTRACTS into the editor; tailored/cover Upload ATTACHES a
  // file — separate busy/error so one can't mask the other.
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);

  const handleMasterUpload = (file: File) => {
    setIngesting(true);
    setIngestError(null);
    void api
      .ingestResume(file)
      .then((res) => {
        setValue(res.text);
        setDirty(true);
        setMode("preview");
      })
      .catch((e: unknown) => setIngestError(e instanceof Error ? e.message : String(e)))
      .finally(() => setIngesting(false));
  };
  const handleAttach = (file: File) => {
    setAttaching(true);
    setAttachError(null);
    void Promise.resolve(onAttachDocument?.(file))
      .catch((e: unknown) => setAttachError(e instanceof Error ? e.message : String(e)))
      .finally(() => setAttaching(false));
  };

  const title = isMaster
    ? t("popups.resume.masterTitle")
    : `${application?.job.title ?? ""} · ${application?.job.company ?? ""}`;

  // As large as a dialog can read without becoming a fullscreen takeover: the
  // shell clamps to 96vw / 94vh, backdrop + × stay visible (2026-07-12 beta
  // feedback, twice — these carry a lot of text; give them the room).
  const width = kind === "tailored" ? 1840 : kind === "cover" ? 1480 : 1100;

  // Right-aligned control cluster, rendered beside the pane it controls: in the
  // modal header for master/cover, in the tailored pane's own header row for the
  // tailored view (maintainer: they relate to the variant, not the whole modal).
  // Order (right side): Generate · Upload · Preview|Raw.
  const generateBtn =
    !isMaster && packet !== "generating" ? (
      <button
        data-testid="generate-variant"
        onClick={onRegenerate}
        className="inline-flex h-[28px] shrink-0 items-center rounded-7 bg-accent px-2.5 text-[12px] font-medium text-white hover:bg-accent-ink"
      >
        {value ? t("popups.resume.regenerate") : t("popups.resume.generate")}
      </button>
    ) : null;

  // Master: Upload (extract into editor) + Preview|Raw.
  const masterControls = (
    <div className="ml-auto flex items-center gap-2">
      <UploadButton
        onFile={handleMasterUpload}
        busy={ingesting}
        error={ingestError}
        testid="upload-doc-master"
      />
      <ModeToggle mode={mode} setMode={setMode} />
    </div>
  );

  // Tailored/Cover: Generate + Upload (attach the submitted file) + Preview|Raw.
  const variantControls = (
    <div className="ml-auto flex items-center gap-2">
      {generateBtn}
      {onAttachDocument ? (
        <UploadButton
          onFile={handleAttach}
          busy={attaching}
          error={attachError}
          testid="upload-doc-variant"
        />
      ) : null}
      <ModeToggle mode={mode} setMode={setMode} />
    </div>
  );

  // The attached submitted-file chip (tailored/cover), shown beside the variant.
  const attachedChip =
    !isMaster && attachedDoc && onRemoveDocument ? (
      <AttachedDocChip doc={attachedDoc} onRemove={() => void Promise.resolve(onRemoveDocument())} />
    ) : null;

  const header = submitted ? (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-2 py-0.5 text-[10px] tracking-wider ${PILL[kind].cls}`}>
        {t(PILL[kind].label)}
      </span>
      <span className="rounded-full bg-good-wash px-2 py-0.5 text-[10px] text-good">
        {t("popups.resume.submittedPill")}
      </span>
      <DownloadDocButton doc={submitted} />
    </div>
  ) : (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-2 py-0.5 text-[10px] tracking-wider ${PILL[kind].cls}`}>
        {t(PILL[kind].label)}
      </span>
      {/* The review-state pill: "Ready to review" flips to "Approved" once the
          user clicks Approve and Save (approved_at on the artifact). */}
      {!isMaster ? <PacketPill state={packet} /> : null}
      {/* The tailored view renders its controls + attached-file chip in ITS
          pane's header (they belong to the variant, not the whole modal —
          maintainer); master/cover keep them here, right-aligned, beside the
          pane they describe. */}
      {isMaster ? (
        masterControls
      ) : kind === "cover" ? (
        <>
          {attachedChip}
          {variantControls}
        </>
      ) : null}
    </div>
  );

  // State-aware body for tailored/cover (US-RES-02 / US-CL-01 table).
  function stateBody() {
    if (packet === "generating") {
      return (
        <div className="grid h-full place-items-center text-[13px] text-ink-3" data-testid="packet-generating">
          <div className="flex items-center gap-2">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border-2 border-t-accent" />
            {kind === "cover" ? t("popups.resume.generatingCover") : t("popups.resume.generatingResume")}
          </div>
        </div>
      );
    }
    // Always editable now (Req): the box is present whether or not a variant was
    // generated, so the user can paste their own (from their own ChatGPT/Gemini)
    // and edit it. Generate lives at the top of the header.
    return (
      <MarkdownEditor
        value={value}
        onChange={(v) => {
          setValue(v);
          setDirty(true);
        }}
        mode={mode}
        testid={`${kind}-editor`}
      />
    );
  }

  const submittedLabel =
    kind === "cover" ? t("popups.resume.submittedCoverLabel") : t("popups.resume.submittedResumeLabel");
  const body = submitted ? (
    <div className="flex h-[82vh] flex-col gap-2 p-5">
      {header}
      <p className="text-[12.5px] text-ink-3">
        {kind === "cover"
          ? t("popups.resume.submittedBlurbCover")
          : t("popups.resume.submittedBlurbResume")}
      </p>
      {kind === "tailored" ? (
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              {t("popups.resume.masterColumn")}
              <span className="rounded-full bg-surface-3 px-1.5 py-px text-[9.5px] text-ink-4">
                {t("popups.resume.readOnly")}
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <MarkdownEditor value={profile.master_md} mode="preview" readOnly testid="tailored-master-ref" />
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              {submittedLabel}
              <span className="rounded-full bg-good-wash px-1.5 py-px text-[9.5px] text-good">
                {t("popups.resume.readOnly")}
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <SubmittedDocViewer doc={submitted} />
            </div>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1">
          <SubmittedDocViewer doc={submitted} />
        </div>
      )}
    </div>
  ) : kind === "master" ? (
      <div className="flex h-[80vh] flex-col gap-2 p-5">
        {header}
        <p className="text-[12.5px] text-ink-3">{t(BLURB.master)}</p>
        <div className="min-h-0 flex-1">
          <MarkdownEditor
            value={value}
            onChange={(v) => {
              setValue(v);
              setDirty(true);
            }}
            mode={mode}
            testid="master-editor"
          />
        </div>
      </div>
    ) : kind === "tailored" ? (
      <div className="flex h-[82vh] flex-col gap-2 p-5">
        {header}
        <p className="text-[12.5px] text-ink-3">{t(BLURB.tailored)}</p>
        <div
          className={
            "grid min-h-0 flex-1 gap-3 " +
            (notes.length > 0 ? "grid-cols-[1fr_1fr_300px]" : "grid-cols-2")
          }
        >
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              {t("popups.resume.masterColumn")}
              <span className="rounded-full bg-surface-3 px-1.5 py-px text-[9.5px] text-ink-4">
                {t("popups.resume.readOnly")}
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <MarkdownEditor value={profile.master_md} mode="preview" readOnly testid="tailored-master-ref" />
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              {t("popups.resume.tailoredVariant")}
              <span className="rounded-full bg-good-wash px-1.5 py-px text-[9.5px] text-good">
                {t("popups.resume.editable")}
              </span>
              {attachedChip}
              {variantControls}
            </div>
            <div className="min-h-0 flex-1">{stateBody()}</div>
          </div>
          {notes.length > 0 ? <NotesAside notes={notes} /> : null}
        </div>
      </div>
    ) : (
      <div className="flex h-[82vh] flex-col gap-2 p-5">
        {header}
        <p className="text-[12.5px] text-ink-3">{t(BLURB.cover)}</p>
        {/* Cover letter mirrors the tailored layout: content left, the
            fabrication-guard notes in a right rail (2026-07-12 beta feedback —
            they used to sit under the letter). */}
        <div
          className={
            "grid min-h-0 flex-1 gap-3 " +
            (notes.length > 0 ? "grid-cols-[1fr_320px]" : "grid-cols-1")
          }
        >
          <div className="min-h-0">{stateBody()}</div>
          {notes.length > 0 ? <NotesAside notes={notes} /> : null}
        </div>
      </div>
    );

  // The variant is always editable (paste-your-own), so the save footer shows on
  // every non-generating, non-submitted view — not only ready/approved.
  const showEditor = !submitted && (isMaster || packet !== "generating");
  const footer = showEditor ? (
    <div className="flex items-center gap-3">
      {/* Fabrication-guard NOTES (FR-TL-01) live in the right-rail panel for
          both tailored and cover views (2026-07-12 — the cover footer line
          moved into the shared aside). */}
      {dirty ? (
        <span className="text-[11.5px] text-ink-3">{t("popups.resume.unsavedChanges")}</span>
      ) : stale ? (
        <span className="text-[11.5px] text-warn" data-testid="stale-variant-hint">
          {t("popups.resume.staleVariant")}
        </span>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {isMaster ? (
          <button
            onClick={() => {
              onSaveMaster?.(value);
              setDirty(false);
            }}
            className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            {t("popups.resume.saveChanges")}
          </button>
        ) : packet === "ready" ? (
          <button
            onClick={() => {
              onApprove?.(value);
              setDirty(false);
            }}
            data-testid="approve-and-save"
            className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            {t("popups.resume.approveAndSave")}
          </button>
        ) : (
          <button
            onClick={() => {
              onSaveVariant?.(value);
              setDirty(false);
            }}
            data-testid="save-variant"
            className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
          >
            {t("popups.resume.saveChanges")}
          </button>
        )}
      </div>
    </div>
  ) : undefined;

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={width}
      footer={footer}
      headerExtra={
        showEditor && value ? (
          <ShareDropdown
            getMarkdown={() => value}
            what={
              kind === "master"
                ? t("popups.resume.doc.master")
                : kind === "cover"
                  ? t("popups.resume.doc.cover")
                  : t("popups.resume.doc.tailored")
            }
          />
        ) : undefined
      }
    >
      {body}
    </Modal>
  );
}

/** The fabrication-guard notes rail (FR-TL-01) — shared by the tailored and
 *  cover views so both read the tailorer's caveats beside the content. */
function NotesAside({ notes }: { notes: string[] }) {
  const { t } = useTranslation();
  return (
    <aside className="flex min-h-0 flex-col" data-testid="tailorer-notes-panel">
      <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-warn">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-warn" />
        {t("popups.resume.notesFromTailorer", { count: notes.length })}
      </div>
      <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-md border border-warn/30 bg-warn-wash/40 p-3 text-[12px] leading-relaxed text-ink-2">
        {notes.map((n, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-warn" aria-hidden="true" />
            <span>{n}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
