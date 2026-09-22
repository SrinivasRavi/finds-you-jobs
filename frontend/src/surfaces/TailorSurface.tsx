import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/index";
import { useProfile } from "../api/queries";
import { Icon } from "../shell/icons";
import { MarkdownEditor } from "../shell/MarkdownEditor";

type Mode = "preview" | "raw";

function ShareDropdown({ getMarkdown, what }: { getMarkdown: () => string; what: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <div className="relative">
      <button
        data-testid="tailor-share-btn"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-[32px] items-center gap-1.5 rounded-lg border border-border-2 bg-surface px-3 text-[12.5px] font-medium text-ink-2 shadow-sm hover:bg-surface-3"
      >
        <Icon name="share" size={14} />
        {t("popups.resume.share.share", "Share / Copy")}
      </button>
      {open ? (
        <div className="absolute right-0 top-[36px] z-20 w-64 overflow-hidden rounded-lg border border-border bg-surface shadow-xl">
          <button
            data-testid="tailor-share-copy-md"
            onClick={() => {
              void navigator.clipboard?.writeText(getMarkdown());
              setCopied(true);
              setTimeout(() => {
                setCopied(false);
                setOpen(false);
              }, 900);
            }}
            className="block w-full px-3.5 py-2.5 text-left text-[12.5px] text-ink-2 hover:bg-surface-2"
          >
            {copied ? t("popups.resume.share.copied", "Copied to clipboard!") : t("popups.resume.share.copyToClipboard", { what })}
            <span className="mt-0.5 block text-[10.5px] text-ink-4">
              {t("popups.resume.share.copyHint", "Pure markdown for forms & ATS paste")}
            </span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

function NotesAside({ notes }: { notes: string[] }) {
  const { t } = useTranslation();
  return (
    <aside className="flex min-h-0 flex-col rounded-lg border border-warn/30 bg-warn-wash/30 p-3" data-testid="tailorer-notes-panel">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-warn">
        <span className="inline-block h-2 w-2 rounded-full bg-warn" />
        {t("popups.resume.notesFromTailorer", { count: notes.length })}
      </div>
      <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto text-[12px] leading-relaxed text-ink-2 pr-1">
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

export function TailorSurface() {
  const { data: profile } = useProfile();
  const [jobDescription, setJobDescription] = useState("");
  const [guidance, setGuidance] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tailoredMd, setTailoredMd] = useState<string>("");
  const [notes, setNotes] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("preview");

  const handleTailor = async () => {
    if (!jobDescription.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.tailorAdhoc(jobDescription, guidance.trim() || undefined);
      setTailoredMd(res.resume_md);
      setNotes(res.notes || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Tailoring failed. Please check your AI settings and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-surface-2">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-wash text-accent">
            <Icon name="file" size={18} />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-ink">Instant Resume Tailorer</h1>
            <p className="text-[11.5px] text-ink-3">Paste any Job Description to generate an ATS-tailored resume</p>
          </div>
        </div>
        {tailoredMd ? (
          <div className="flex items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-lg border border-border text-[11.5px]">
              <button
                data-testid="mode-preview"
                onClick={() => setMode("preview")}
                className={`px-3 py-1.5 font-medium transition-colors ${
                  mode === "preview" ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3"
                }`}
              >
                Preview
              </button>
              <button
                data-testid="mode-raw"
                onClick={() => setMode("raw")}
                className={`px-3 py-1.5 font-medium transition-colors ${
                  mode === "raw" ? "bg-accent text-white" : "bg-surface text-ink-2 hover:bg-surface-3"
                }`}
              >
                Raw Markdown
              </button>
            </div>
            <ShareDropdown getMarkdown={() => tailoredMd} what="Tailored Resume" />
          </div>
        ) : null}
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-2 gap-4 p-4 overflow-hidden">
        {/* Left: Input Form */}
        <div className="flex min-h-0 flex-col rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[13px] font-semibold text-ink">Target Job Description</span>
            <span className="text-[11px] text-ink-4">Required</span>
          </div>
          <textarea
            data-testid="tailor-jd-input"
            value={jobDescription}
            onChange={(e) => setJobDescription(e.target.value)}
            placeholder="Paste the raw job description, requirements, or posting text here..."
            className="min-h-0 flex-1 resize-none rounded-lg border border-border-2 bg-surface-2 p-3 text-[12.5px] font-mono leading-relaxed text-ink focus:border-accent focus:bg-surface focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <div className="mt-3">
            <label className="mb-1 block text-[12px] font-medium text-ink-2">
              Custom Tailoring Guidance <span className="text-ink-4 font-normal">(Optional)</span>
            </label>
            <input
              type="text"
              data-testid="tailor-guidance-input"
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              placeholder="e.g., Emphasize backend APIs and Docker, highlight Python experience..."
              className="w-full rounded-lg border border-border-2 bg-surface-2 px-3 py-2 text-[12.5px] text-ink focus:border-accent focus:bg-surface focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          {error ? (
            <div className="mt-3 rounded-lg border border-bad/30 bg-bad-wash/40 p-2.5 text-[12px] text-bad">
              {error}
            </div>
          ) : null}
          <div className="mt-4 flex items-center justify-between pt-2 border-t border-border-2">
            <span className="text-[11.5px] text-ink-3">
              Uses Master Resume {profile?.application_profile?.name ? `(${profile.application_profile.name})` : ""}
            </span>
            <button
              data-testid="tailor-submit-btn"
              disabled={loading || !jobDescription.trim()}
              onClick={() => void handleTailor()}
              className="inline-flex h-[34px] items-center gap-2 rounded-lg bg-accent px-4 text-[13px] font-medium text-white shadow-sm hover:bg-accent-ink disabled:opacity-50"
            >
              {loading ? (
                <>
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Tailoring Resume…
                </>
              ) : (
                "Tailor Resume"
              )}
            </button>
          </div>
        </div>
        {/* Right: Output Pane */}
        <div className="flex min-h-0 flex-col rounded-xl border border-border bg-surface p-4 shadow-sm overflow-hidden">
          {tailoredMd ? (
            <div className="flex min-h-0 flex-1 flex-col gap-3">
              {notes.length > 0 ? <NotesAside notes={notes} /> : null}
              <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-border-2 bg-surface">
                <MarkdownEditor
                  value={tailoredMd}
                  onChange={setTailoredMd}
                  mode={mode}
                />
              </div>
            </div>
          ) : (
            <div className="grid h-full place-items-center text-center p-6">
              <div className="max-w-sm space-y-2">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-surface-3 text-ink-3">
                  <Icon name="file" size={24} />
                </div>
                <h3 className="text-[14px] font-semibold text-ink">Ready to Tailor</h3>
                <p className="text-[12px] text-ink-3 leading-relaxed">
                  Paste any job description on the left and click Tailor Resume to see your customized resume and ATS alignment notes here.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

