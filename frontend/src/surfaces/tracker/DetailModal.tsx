// Detail modal (US-TR-03/04/10 + Applier screenshot) with its Overview-tab
// pieces: the job-detail block and the attached-documents row. (Extracted from
// Tracker.tsx 2026-07-25, F-M6 monolith split — pure move, zero behavior
// change.) Not memoized: it mounts only while a card's detail is open, and its
// callback props are inline closures over the selected card at the root.

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../../api/index";
import {
  useApplicationActivity,
  useApplicationNetworking,
  useLinkedInSession,
} from "../../api/queries";
import type { Application, ApplicationDocument, Job, Priority } from "../../api/types";
import i18n from "../../i18n";
import type { ResumeModalKind } from "../../popups/ResumeModal";
import { Avatar } from "../../shell/Avatar";
import { formatWhen } from "../../shell/datetime";
import { Icon } from "../../shell/icons";
import { Markdown } from "../../shell/Markdown";
import { Modal } from "../../shell/Modal";
import { scoreTier, workLabel } from "../jobFormat";

// Translation keys for the attached-document slots' human labels (the artifact
// kind vocabulary).
const DOC_KIND_KEY: Record<ApplicationDocument["kind"], string> = {
  tailored_resume: "tracker.docKind.tailored_resume",
  cover_letter: "tracker.docKind.cover_letter",
};

/** Download an attached document (authed fetch → object URL → save). The bearer
 *  token can't ride on a plain href, so we fetch the blob and click a temp link. */
async function downloadDocument(doc: ApplicationDocument): Promise<void> {
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

/** Activity timestamps read naturally — "7 July 2026, 00:20" (local time). The
 *  date used to be pinned to "en-GB" in a 13-language app (duplication audit
 *  D-F4); it now follows the shared locale rule. The clock stays a manual
 *  24-hour HH:MM so the timeline column keeps its fixed width. */
function formatActivityAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${formatWhen(iso, "longDate")}, ${hh}:${mm}`;
}

// ─── Attached documents (FR-TR manual-add) — the resume/cover the user actually
// submitted for a manually-logged application, downloadable verbatim. ──────────

function AttachedDocuments({ docs }: { docs: ApplicationDocument[] }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-1.5" data-testid="detail-documents">
      <div className="text-[12px] font-medium text-ink-2">{t("tracker.documents.submitted")}</div>
      <div className="flex flex-wrap gap-2">
        {docs.map((doc) => (
          <button
            key={doc.document_id}
            type="button"
            onClick={() =>
              downloadDocument(doc).catch((e: unknown) =>
                setError(e instanceof Error ? e.message : String(e)),
              )
            }
            data-testid={`doc-${doc.kind}`}
            title={t("tracker.documents.downloadTitle", { filename: doc.filename })}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] text-ink-2 hover:border-accent hover:text-ink"
          >
            <Icon name="file" size={13} strokeWidth={2} />
            {t(DOC_KIND_KEY[doc.kind] ?? doc.kind)}
            <span className="max-w-[160px] truncate text-ink-4">· {doc.filename}</span>
          </button>
        ))}
      </div>
      {error ? <p className="text-[11.5px] text-bad">{error}</p> : null}
    </div>
  );
}

// ─── Job detail block (US-TR-03) — the job-board fields on the Overview tab ───
// Everything the Job Board card/detail shows, so a tracked card is a full record
// without bouncing back to the board: logo, title, company · location · work-style,
// match score, the JD, and — most importantly — the canonical Job URL.

function JobDetail({ job }: { job: Job }) {
  const { t } = useTranslation();
  const tier = job.score ? scoreTier(job.score.score_0_100) : null;
  const meta = [job.company, job.location, workLabel(job.work_style)].filter(Boolean).join(" · ");
  return (
    <div className="space-y-3" data-testid="detail-job-info">
      <div className="flex items-start gap-3">
        <Avatar name={job.company} size={10} shape="md" tone="raised" />
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold leading-snug text-ink">{job.title}</div>
          <div className="truncate text-[12px] text-ink-3">{meta}</div>
        </div>
        {job.score ? (
          <span
            data-testid="detail-match-score"
            title={t("tracker.jobDetail.matchScoreTitle")}
            className={`inline-grid h-10 w-10 shrink-0 place-items-center rounded-full border font-mono text-[13px] font-semibold ${tier?.ring} ${tier?.text}`}
          >
            {job.score.score_0_100}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[12px]">
        <a
          href={job.canonical_url}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="detail-job-url"
          className="inline-flex items-center gap-1 font-medium text-accent hover:underline"
        >
          {t("tracker.jobDetail.openPosting")}
        </a>
        {job.salary ? <span className="text-ink-3">{job.salary}</span> : null}
      </div>
      {job.description ? (
        <details className="rounded-md border border-border bg-surface-2" data-testid="detail-jd">
          <summary className="cursor-pointer px-3 py-2 text-[12px] font-medium text-ink-2">
            {t("tracker.jobDetail.jobDescription")}
          </summary>
          <div className="max-h-64 overflow-y-auto border-t border-border px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
            <Markdown md={job.description} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

// ─── Detail modal (US-TR-03/04/10 + Applier screenshot) ──────────────────────

export function DetailModal({
  app,
  onClose,
  onPriority,
  onNotes,
  onArchive,
  onReturn,
  onOpenPopup,
}: {
  app: Application;
  onClose: () => void;
  onPriority: (p: Priority) => void;
  onNotes: (notes: string) => void;
  onArchive: () => void;
  onReturn: () => void;
  onOpenPopup: (kind: ResumeModalKind) => void;
}) {
  // Networking tab restored 2026-07-16 (the referral-outreach backend now
  // exists) — shown only when the LinkedIn toggle is on (US-TR-03 / FR-TR-03),
  // same gate the prior repo used.
  type Tab = "Overview" | "Notes" | "Scoring" | "Activity" | "Networking";
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("Overview");
  const [notes, setNotes] = useState(app.notes);
  const linkedInOn = Boolean(useLinkedInSession().data?.enabled);
  const activity = useApplicationActivity(app.id);
  const networking = useApplicationNetworking(tab === "Networking" ? app.id : null);
  // Activity sits last (maintainer, 2026-07-11) — it's the audit trail, not
  // the working surface.
  const tabs: Tab[] = [
    "Overview",
    "Notes",
    "Scoring",
    ...(linkedInOn ? (["Networking"] as const) : []),
    "Activity",
  ];

  return (
    <Modal title={`${app.job.title} · ${app.job.company}`} onClose={onClose} width={640}>
      <div className="flex items-center gap-1 border-b border-border px-5">
        {tabs.map((tb) => (
          <button
            key={tb}
            onClick={() => setTab(tb)}
            className={
              "border-b-2 px-3 py-2 text-[12.5px] " +
              (tab === tb ? "border-accent font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink")
            }
          >
            {t(`tracker.detail.tab.${tb}`)}
          </button>
        ))}
      </div>
      <div className="px-5 py-4">
        {tab === "Overview" ? (
          <div className="space-y-4">
            <JobDetail job={app.job} />
            {app.documents.length > 0 ? <AttachedDocuments docs={app.documents} /> : null}
            <div className="flex items-center gap-3">
              <span className="text-[12px] text-ink-3">{t("tracker.detail.priority")}</span>
              <select
                value={app.priority}
                onChange={(e) => onPriority(e.target.value as Priority)}
                data-testid="priority-select"
                className="rounded-md border border-border bg-surface px-2 py-1 text-[12.5px] text-ink"
              >
                {(["P0", "P1", "P2", "P3"] as Priority[]).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <span
                className="ml-auto font-mono text-[11px] text-ink-4"
                data-testid="app-ref"
                title={t("tracker.detail.appRefTitle")}
              >
                {"#" + app.id.replace(/-/g, "").slice(-6).toUpperCase()}
              </span>
              <span className="text-[12px] text-ink-3">
                {t("tracker.detail.stageLine", { stage: t(`tracker.stage.${app.stage}`) })}
              </span>
            </div>
            {app.posting_closed || app.job.board_state === "expired" ? (
              <div
                className="rounded-md border border-bad/40 bg-bad-wash px-3 py-2 text-[12px] text-bad"
                data-testid="posting-closed-note"
              >
                {t("tracker.detail.postingClosed")}
              </div>
            ) : null}
            <div className="flex gap-2">
              <button
                onClick={() => onOpenPopup("tailored")}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
              >
                {app.packet_resume_state === "ready" || app.packet_resume_state === "approved"
                  ? t("tracker.detail.viewResume")
                  : app.packet_resume_state === "generating"
                    ? t("tracker.detail.generatingResume")
                    : t("tracker.detail.generateResume")}
              </button>
              <button
                onClick={() => onOpenPopup("cover")}
                className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
              >
                {app.packet_cover_state === "ready" || app.packet_cover_state === "approved"
                  ? t("tracker.detail.viewCover")
                  : app.packet_cover_state === "generating"
                    ? t("tracker.detail.generatingCover")
                    : t("tracker.detail.generateCover")}
              </button>
            </div>
            {/* REMOVED: Apply button, Applier run summary, and Applier preview
                screenshot (no Applier surface on this sidecar yet). */}
            <div className="flex gap-2 border-t border-border pt-3">
              {app.stage === "Saved" ? (
                <button
                  onClick={onReturn}
                  className="rounded-md border border-border-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 hover:bg-surface-3"
                >
                  {t("tracker.moveToDiscover")}
                </button>
              ) : null}
              <button
                onClick={onArchive}
                data-testid="detail-archive-btn"
                className="ml-auto rounded-md border border-bad/40 px-3 py-1.5 text-[12.5px] text-bad hover:bg-bad-wash"
              >
                {t("tracker.archive")}
              </button>
            </div>
          </div>
        ) : tab === "Notes" ? (
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => onNotes(notes)}
            data-testid="notes-editor"
            rows={8}
            placeholder={t("tracker.detail.notesPlaceholder")}
            className="w-full resize-none rounded-md border border-border bg-surface p-3 text-[13px] text-ink placeholder:text-ink-4 focus:border-accent focus:outline-none"
          />
        ) : tab === "Scoring" ? (
          app.job.score ? (
            <div>
              <div className="mb-2 text-[13px] font-semibold text-ink">
                {t("tracker.detail.matchScore", { score: app.job.score.score_0_100 })}
              </div>
              <ul className="mb-3 space-y-1 text-[12px] text-ink-2">
                {app.job.score.reasons.map((r, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="mt-1 size-1 shrink-0 rounded-full bg-accent" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
              <Markdown md={app.job.score.breakdown_md} className="text-[11.5px]" />
            </div>
          ) : (
            <p className="text-[12.5px] text-ink-3">{t("tracker.detail.scoringPending")}</p>
          )
        ) : tab === "Activity" ? (
          // Real Activity log (US-TR-03 / FR-TR-03) — composed server-side from
          // the operations ledger + outreach log, not synthesized client-side.
          <ul className="space-y-2 text-[12px] text-ink-2" data-testid="activity-log">
            {activity.isLoading ? (
              <li className="text-ink-3">{t("tracker.detail.loadingActivity")}</li>
            ) : (activity.data ?? []).length === 0 ? (
              <li className="text-ink-3">{t("tracker.detail.noActivity")}</li>
            ) : (
              // Reverse chronological — newest first (maintainer, 2026-07-11).
              [...(activity.data ?? [])]
                .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))
                .map((e, i) => (
                <li key={i} className="flex items-start gap-2" data-testid="activity-entry">
                  <span
                    className={
                      "mt-1 size-1.5 shrink-0 rounded-full " +
                      (e.state === "failed" ? "bg-bad" : "bg-accent")
                    }
                  />
                  <span className="flex-1">{e.label}</span>
                  {e.at ? (
                    <span className="font-mono text-[10.5px] text-ink-4">
                      {formatActivityAt(e.at)}
                    </span>
                  ) : null}
                </li>
              ))
            )}
          </ul>
        ) : (
          // Networking tab (US-TR-03) — the role's referral contacts + statuses.
          // Restored 2026-07-16.
          <div data-testid="networking-tab">
            {networking.isLoading ? (
              <p className="text-[12.5px] text-ink-3">{t("tracker.detail.loadingContacts")}</p>
            ) : (networking.data ?? []).length === 0 ? (
              <p className="text-[12.5px] text-ink-3">
                {t("tracker.detail.noContacts")}
              </p>
            ) : (
              <ul className="space-y-2">
                {(networking.data ?? []).map((c) => (
                  <li
                    key={c.contact_id}
                    data-testid="networking-contact"
                    className="rounded-md border border-border px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12.5px] font-medium text-ink">{c.name || t("tracker.detail.unknown")}</span>
                      <span className="rounded-full border border-border-2 bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink-3">
                        {c.connection_status}
                      </span>
                    </div>
                    <div className="text-[11px] text-ink-3">
                      {[c.role, c.company].filter(Boolean).join(" · ")}
                    </div>
                    {c.last_message ? (
                      <div className="mt-1 truncate text-[11px] text-ink-4">
                        {t("tracker.detail.lastMessage", { message: c.last_message })}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
