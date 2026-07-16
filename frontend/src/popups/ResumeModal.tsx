// The `openResumeModal` trio — one shared component switched by `kind`
// (US-RES-01, US-RES-02, US-CL-01). Ports assets/shell.js openResumeModal():
//   master  → single-column editor, Preview⇄Edit toggle, MASTER pill.
//   tailored→ two-column (master read-only | tailored editable), TAILORED pill,
//             fabrication-guard NOTES footer, stale + Re-generate.
//   cover   → single-column tailored editor, COVER LETTER pill.
// packetState drives the state-aware body (generating spinner / none CTA / editor).

import { useState } from "react";

import type { Application, PacketState, Profile } from "../api/types";
import { Markdown } from "../shell/Markdown";
import { Modal } from "../shell/Modal";

export type ResumeModalKind = "master" | "tailored" | "cover";

type Mode = "preview" | "edit";

const PILL: Record<ResumeModalKind, { label: string; cls: string }> = {
  master: { label: "MASTER", cls: "bg-good-wash text-good" },
  tailored: { label: "TAILORED", cls: "bg-accent-wash text-accent" },
  cover: { label: "COVER LETTER", cls: "bg-purple-wash text-purple" },
};

const BLURB: Record<ResumeModalKind, string> = {
  master: "Your canonical source — every tailored variant builds from this.",
  tailored: "Generated from your master. Edits apply only to this variant.",
  cover: "Generated for this role. Edits apply only to this variant.",
};

function PacketPill({ state }: { state: PacketState }) {
  const map: Record<PacketState, [string, string]> = {
    approved: ["Approved", "bg-good-wash text-good"],
    ready: ["Ready to review", "bg-warn-wash text-warn"],
    generating: ["Generating…", "bg-surface-3 text-ink-3"],
    none: ["Not generated", "bg-surface-3 text-ink-3"],
    failed: ["Failed", "bg-bad-wash text-bad"],
  };
  const [label, cls] = map[state];
  return (
    <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${cls}`}>
      {label}
    </span>
  );
}

/**
 * Share dropdown (US-RES-01/02, US-CL-01) — header button next to ×:
 * "Copy <this document> to clipboard" (Markdown, for ATS forms). `what` names
 * the document so the copy action is unambiguous ("Copy tailored resume to
 * clipboard"). REMOVED: "Export to PDF" — /api/export/pdf doesn't exist on
 * this sidecar yet.
 */
function ShareDropdown({ getMarkdown, what }: { getMarkdown: () => string; what: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <button
        data-testid="share-btn"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-[28px] items-center gap-1 rounded-7 border border-border-2 bg-surface px-2.5 text-[12px] font-medium text-ink-2 hover:bg-surface-3"
      >
        Share
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
            {copied ? "Copied ✓" : `Copy ${what} to clipboard`}
            <span className="mt-0.5 block text-[10.5px] text-ink-4">
              Markdown — paste into ATS forms
            </span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

const MODE_LABEL: Record<Mode, string> = { preview: "Preview", edit: "Edit" };

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
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
          {MODE_LABEL[m]}
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
  onSaveMaster,
  onApprove,
  onSaveVariant,
  onRegenerate,
}: {
  kind: ResumeModalKind;
  onClose: () => void;
  profile: Profile;
  application?: Application;
  onSaveMaster?: (md: string) => void;
  onApprove?: (markdown: string) => void;
  onSaveVariant?: (markdown: string) => void;
  onRegenerate?: () => void;
}) {
  const isMaster = kind === "master";
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
    ? "Master resume"
    : `${application?.job.title ?? ""} · ${application?.job.company ?? ""}`;

  // As large as a dialog can read without becoming a fullscreen takeover: the
  // shell clamps to 96vw / 94vh, backdrop + × stay visible (2026-07-12 beta
  // feedback, twice — these carry a lot of text; give them the room).
  const width = kind === "tailored" ? 1840 : kind === "cover" ? 1480 : 1100;

  const header = (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] tracking-wider ${PILL[kind].cls}`}>
        {PILL[kind].label}
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
            {kind === "cover" ? "Generating cover letter…" : "Generating tailored resume…"}
          </div>
        </div>
      );
    }
    if (packet === "none" || packet === "failed") {
      return (
        <div className="grid h-full place-items-center text-center" data-testid="packet-none">
          <div className="space-y-3">
            <p className="text-[13px] text-ink-3">
              {packet === "failed" ? "Generation failed — try again." : "Not generated yet."}
            </p>
            <button
              onClick={onRegenerate}
              className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-accent-ink"
            >
              {kind === "cover" ? "Generate cover letter" : "Generate tailored resume"}
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

  const body =
    kind === "master" ? (
      <div className="flex h-[80vh] flex-col gap-2 p-5">
        {header}
        <p className="text-[12.5px] text-ink-3">{BLURB.master}</p>
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
        <p className="text-[12.5px] text-ink-3">{BLURB.tailored}</p>
        <div
          className={
            "grid min-h-0 flex-1 gap-3 " +
            (notes.length > 0 ? "grid-cols-[1fr_1fr_300px]" : "grid-cols-2")
          }
        >
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              Master
              <span className="rounded-full bg-surface-3 px-1.5 py-px text-[9.5px] uppercase tracking-wider text-ink-4">
                Read-only
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <EditorPane value={profile.master_md} mode="preview" readOnly testid="tailored-master-ref" />
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <div className="mb-1 flex items-center gap-2 text-[12px] font-medium text-ink-3">
              Tailored variant
              <span
                className={
                  "rounded-full px-1.5 py-px text-[9.5px] uppercase tracking-wider " +
                  (mode === "edit" ? "bg-accent-wash text-accent" : "bg-good-wash text-good")
                }
              >
                {mode === "edit" ? "Editing" : "Editable — switch to Edit"}
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
        <p className="text-[12.5px] text-ink-3">{BLURB.cover}</p>
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

  const showEditor = isMaster || packet === "ready" || packet === "approved";
  const footer = showEditor ? (
    <div className="flex items-center gap-3">
      {/* Fabrication-guard NOTES (FR-TL-01) live in the right-rail panel for
          both tailored and cover views (2026-07-12 — the cover footer line
          moved into the shared aside). */}
      {dirty ? (
        <span className="text-[11.5px] text-ink-3">Unsaved changes</span>
      ) : stale ? (
        <span className="text-[11.5px] text-warn" data-testid="stale-variant-hint">
          Generated from an older version of your master resume
        </span>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {!isMaster ? (
          <button
            onClick={onRegenerate}
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:border-border-2"
          >
            Re-generate
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
            Save changes
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
            Approve and Save
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
            Save changes
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
                ? "master resume"
                : kind === "cover"
                  ? "cover letter"
                  : "tailored resume"
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
  return (
    <aside className="flex min-h-0 flex-col" data-testid="tailorer-notes-panel">
      <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-warn">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-warn" />
        {notes.length} note{notes.length > 1 ? "s" : ""} from the tailorer
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
