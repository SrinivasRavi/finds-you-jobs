// The `openResumeModal` trio — one shared component switched by `kind`
// (US-RES-01, US-RES-02, US-CL-01). Ports assets/shell.js openResumeModal():
//   master  → single-column editor, Preview⇄Edit toggle, MASTER pill.
//   tailored→ two-column (master read-only | tailored editable), TAILORED pill,
//             fabrication-guard NOTES footer, stale + Re-generate.
//   cover   → single-column tailored editor, COVER LETTER pill.
// packetState drives the state-aware body (generating spinner / none CTA / editor).

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/index";

import type { Application, ApplicationDocument, PacketState, Profile } from "../api/types";
import { Markdown } from "../shell/Markdown";
import { Modal } from "../shell/Modal";

export type ResumeModalKind = "master" | "tailored" | "cover";

type Mode = "preview" | "edit";

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
  const [label, cls] = map[state];
  return (
    <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${cls}`}>
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
        void api.fetchDocument(doc.document_id).then((blob) => {
          const dl = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = dl;
          a.download = doc.filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(dl);
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
  edit: "popups.resume.mode.edit",
};

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  const { t } = useTranslation();
  return (
    <div className="inline-flex overflow-hidden rounded-7 border border-border text-[11.5px]">
      {(["preview", "edit"] as Mode[]).map((m) => (
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

/** Editable pane: Preview (rendered, contentEditable-lite) or raw Markdown. */
function EditorPane({
  value,
  onChange,
  mode,
  readOnly = false,
  testid,
}: {
  value: string;
  onChange?: (v: string) => void;
  mode: Mode;
  readOnly?: boolean;
  testid?: string;
}) {
  if (mode === "edit" && !readOnly) {
    return (
      <textarea
        data-testid={testid}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        className="h-full w-full resize-none rounded-md border border-accent bg-surface p-3 font-mono text-[12px] text-ink focus:border-accent focus:outline-none"
      />
    );
  }
  return (
    <div data-testid={testid} className="h-full overflow-auto rounded-md border border-border bg-surface p-4">
      <Markdown md={value} />
    </div>
  );
}

export function ResumeModal({
  kind,
  onClose,
  profile,
  application,
  submittedDoc,
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

  const title = isMaster
    ? t("popups.resume.masterTitle")
    : `${application?.job.title ?? ""} · ${application?.job.company ?? ""}`;

  // As large as a dialog can read without becoming a fullscreen takeover: the
  // shell clamps to 96vw / 94vh, backdrop + × stay visible (2026-07-12 beta
  // feedback, twice — these carry a lot of text; give them the room).
  const width = kind === "tailored" ? 1840 : kind === "cover" ? 1480 : 1100;

  const header = submitted ? (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] tracking-wider ${PILL[kind].cls}`}>
        {t(PILL[kind].label)}
      </span>
      <span className="rounded-full bg-good-wash px-2 py-0.5 font-mono text-[10px] text-good">
        {t("popups.resume.submittedPill")}
      </span>
      <DownloadDocButton doc={submitted} />
    </div>
  ) : (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] tracking-wider ${PILL[kind].cls}`}>
        {t(PILL[kind].label)}
      </span>
      {!isMaster ? <PacketPill state={packet} /> : null}
      <ModeToggle mode={mode} setMode={setMode} />
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
    if (packet === "none" || packet === "failed") {
      return (
        <div className="grid h-full place-items-center text-center" data-testid="packet-none">
          <div className="space-y-3">
            <p className="text-[13px] text-ink-3">
              {packet === "failed" ? t("popups.resume.generationFailed") : t("popups.resume.notGenerated")}
            </p>
            <button
              onClick={onRegenerate}
              className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {kind === "cover" ? t("popups.resume.generateCover") : t("popups.resume.generateResume")}
            </button>
          </div>
        </div>
      );
    }
    return (
      <EditorPane
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
              <EditorPane value={profile.master_md} mode="preview" readOnly testid="tailored-master-ref" />
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
          <EditorPane
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
              <EditorPane value={profile.master_md} mode="preview" readOnly testid="tailored-master-ref" />
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              {t("popups.resume.tailoredVariant")}
              <span
                className={
                  "rounded-full px-1.5 py-px text-[9.5px] " +
                  (mode === "edit" ? "bg-accent-wash text-accent" : "bg-good-wash text-good")
                }
              >
                {mode === "edit" ? t("popups.resume.editing") : t("popups.resume.editableSwitch")}
              </span>
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

  const showEditor = !submitted && (isMaster || packet === "ready" || packet === "approved");
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
        {!isMaster ? (
          <button
            onClick={onRegenerate}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            {t("popups.resume.regenerate")}
          </button>
        ) : null}
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
