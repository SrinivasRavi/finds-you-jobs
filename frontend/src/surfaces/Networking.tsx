// Networking kanban — Track N3 (US-NW-01/02/03/07/11), restored 2026-07-16 from
// the prior repo (the referral-outreach backend now exists on this sidecar): a
// contact lifecycle kanban (Sent / Accepted → Engagement → Converted | Ghosted),
// cards with a last-message snippet + days-in-column, a company/audience scope
// row, and a manual add-contact-by-URL modal (the rank-don't-gate escape hatch).
// Always reachable (2026-07-09 always-on decision): the CRM carries no account
// risk; the LinkedIn risk toggle gates only automated actions (FR-SET-03).
//
// The LinkedIn status button below (2026-08-16, was a read-only pill) is the
// one opener for the LinkedIn browser modal: it shows the session status —
// plus a live "in progress" state while an op drives the surface — and opens
// the modal, except in the expired/never-connected states, where it lands on
// Settings (the connect flow lives there).

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  useAddContact,
  useArchivedContacts,
  useContactSyncInFlight,
  useContacts,
  useLinkedInSession,
  useReachOut,
  useSyncContacts,
  useUpdateContact,
  useViewInBrowser,
} from "../api/queries";
import type { AudienceTag, ConnectionStatus, NetContact } from "../api/types";
import { audienceTag } from "../shell/audienceTag";
import { Avatar } from "../shell/Avatar";
import { Icon } from "../shell/icons";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import { MasterResumeLauncher } from "../shell/MasterResumeLauncher";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { Modal } from "../shell/Modal";
import { RecoveryListModal } from "../shell/RecoveryListModal";
import { opBusy } from "./BrowserOpPlan";
import { ContactComposer } from "./ContactComposer";
import { daysBetween } from "./jobFormat";
import { useLinkedInBrowser } from "./LinkedInBrowserProvider";
import { type LinkedInPillState, type LinkedInPillTone, linkedInStatusPill } from "./linkedInStatus";

// label/empty hold i18n keys — wrapped with t(...) at render.
const COLUMNS: { id: ConnectionStatus; label: string; dot: string; empty: string }[] = [
  { id: "sent", label: "networking.columns.sent", dot: "bg-ink-3", empty: "networking.columnEmpty.sent" },
  { id: "accepted", label: "networking.columns.accepted", dot: "bg-accent", empty: "networking.columnEmpty.accepted" },
  { id: "engagement", label: "networking.columns.engagement", dot: "bg-warn", empty: "networking.columnEmpty.engagement" },
  { id: "ghosted", label: "networking.columns.ghosted", dot: "bg-bad", empty: "networking.columnEmpty.ghosted" },
  { id: "converted", label: "networking.columns.converted", dot: "bg-good", empty: "networking.columnEmpty.converted" },
];

// The header's read-only session chip. Tone + which state a status means come
// from the shared table (duplication audit D-F8); the classes and the copy stay
// here because this chip is not the one Settings renders.
const PILL_CLS: Record<LinkedInPillTone, string> = {
  good: "bg-good-wash border-good text-good",
  warn: "bg-warn-wash border-warn text-warn",
  bad: "bg-bad-wash border-bad text-bad",
};
const PILL_LABEL: Record<LinkedInPillState, string> = {
  connected: "networking.linkedinPill.connected",
  connecting: "networking.linkedinPill.connecting",
  backingOff: "networking.linkedinPill.backingOff",
  expired: "networking.linkedinPill.expired",
  disconnected: "networking.linkedinPill.connect",
};

function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return daysBetween(iso, "floor");
}

/** Who the last-message snippet belongs to, as an i18n key (+ name): the
 *  thread's real last message is attributed honestly — "Me:" when we sent it,
 *  the contact's first name when they did (their thread display name first,
 *  falling back to the stored contact name). The label and composition go
 *  through i18n; the name itself is data. Exported for its unit tests. */
export function lastMessageAttribution(
  c: Pick<NetContact, "last_message_direction" | "last_message_from" | "name">,
): { key: string; name?: string } {
  if (c.last_message_direction === "them") {
    const source = (c.last_message_from ?? "").trim() || (c.name ?? "").trim();
    const first = source.split(/\s+/)[0];
    if (first) return { key: "networking.card.from", name: first };
  }
  return { key: "networking.card.me" };
}

/** The instant a card's shown activity happened, for ordering a kanban column
 *  (most recent first, maintainer ask 2026-08-16). Exactly the timestamp the
 *  card displays (`last_message_at ?? sent_at`), then the row's other
 *  lifecycle stamps so undated cards still order deterministically — and NEVER
 *  `last_touched_at`, the sync engine's rotation cursor, whose churn is what
 *  made the board reshuffle on every Sync press. Exported for its unit
 *  tests. */
export function cardActivityAt(
  c: Pick<NetContact, "last_message_at" | "sent_at" | "accepted_at" | "added_at">,
): number {
  for (const iso of [c.last_message_at, c.sent_at, c.accepted_at, c.added_at]) {
    if (!iso) continue;
    const t = new Date(iso).getTime();
    if (Number.isFinite(t)) return t;
  }
  return 0;
}

/** A column's cards, most-recent activity first; ties break on name then id so
 *  the order is stable across refetches. Exported for its unit tests. */
export function sortColumn(cards: NetContact[]): NetContact[] {
  return [...cards].sort((a, b) => {
    const dt = cardActivityAt(b) - cardActivityAt(a);
    if (dt !== 0) return dt;
    const byName = (a.name || "").localeCompare(b.name || "");
    if (byName !== 0) return byName;
    return a.id.localeCompare(b.id);
  });
}

/** The i18n key for a stopped sync attempt's header note (null = nothing to
 *  say: no attempt yet, or the newest attempt swept cleanly). Exported for
 *  its unit tests. */
export function syncStoppedKey(
  outcome: { stopped: string } | null | undefined,
): string | null {
  if (!outcome?.stopped) return null;
  switch (outcome.stopped) {
    case "cap_or_backoff":
      return "networking.sync.stoppedCap";
    case "rate_limited":
      return "networking.sync.stoppedRate";
    case "auth_error":
      return "networking.sync.stoppedAuth";
    default:
      return "networking.sync.stoppedOther";
  }
}

/** The "Synced Nm ago" stamp beside the Sync button: which i18n unit key and
 *  which number. null when there has never been a successful sync (the stamp
 *  simply doesn't render — the Sync button is the affordance then). Exported
 *  for its unit tests. */
export function syncedAgo(iso: string | null): { key: string; n?: number } | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return null;
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return { key: "networking.sync.justNow" };
  if (mins < 60) return { key: "networking.sync.minutesAgo", n: mins };
  const hours = Math.floor(mins / 60);
  if (hours < 24) return { key: "networking.sync.hoursAgo", n: hours };
  return { key: "networking.sync.daysAgo", n: Math.floor(hours / 24) };
}

export function Networking() {
  const { t } = useTranslation();
  const session = useLinkedInSession();
  // Referral Outreach master toggle. The contact kanban is always reachable (it
  // carries no account risk), but the LinkedIn browser modal and the contact
  // composer's send open a real LinkedIn session, so they surface only once
  // the user has opted in (FR-SET-03 / vision ethos).
  const navigate = useNavigate();
  const linkedinBrowser = useLinkedInBrowser();
  const contactsQ = useContacts();
  const contacts = useMemo(() => contactsQ.data ?? [], [contactsQ.data]);
  const update = useUpdateContact();
  const sync = useSyncContacts();
  // Manual-only sync (maintainer decision, 2026-08-15): the Sync button below
  // is the ONE trigger. No on-open refresh, no interval, no background timer —
  // opening this tab causes zero LinkedIn traffic.
  //
  // Busy state follows the real operation, not the 202: the POST returns the
  // moment the sweep is enqueued while the paced read probes run on for a
  // while, so `syncInFlight` tracks the live `contact_sync` op off the SSE bus
  // (with a ledger seed for one already running at mount) and holds the button
  // in "Syncing…" until the op actually settles — at which point the SSE
  // terminal handler refetches the kanban and the "Synced just now" stamp.
  const syncInFlight = useContactSyncInFlight();
  const syncBusy = sync.isPending || syncInFlight;
  // Refreshing needs both the master toggle and a live session; without either
  // the sidecar refuses (403/409), so don't offer the control.
  const canSync = Boolean(session.data?.enabled && session.data.status === "valid");
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);
  const [audienceFilter, setAudienceFilter] = useState<AudienceTag | null>(null);
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [deletedOpen, setDeletedOpen] = useState(false);
  const [active, setActive] = useState<NetContact | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reachOut = useReachOut();

  // The ONE send path (useReachOut → POST /api/referrals/reach-out), owned here
  // so it outlives the modal that asked for it. A send is meant to be WATCHED
  // (vision: transparency; the live view is the point of the broker surface),
  // so starting one closes the composer and opens the LinkedIn browser modal
  // before the operation's first step — the maintainer sees the whole send,
  // not its aftermath.
  function startSend(contactId: string, message: string) {
    reachOut.mutate(
      { contacts: [{ contact_id: contactId, message }] },
      {
        onError: (err) =>
          setError(err instanceof Error ? err.message : t("networking.sendError")),
      },
    );
    setActive(null);
    linkedinBrowser.open();
  }

  // Move a contact between kanban columns by patching its connection_status to
  // the drop target (US-NW-07). The server allows every column→column
  // transition; if a future rule rejects one, surface it rather than fail
  // silently.
  function onDropContact(status: ConnectionStatus) {
    const id = dragId;
    setDragId(null);
    if (!id) return;
    const c = contacts.find((x) => x.id === id);
    if (!c || c.connection_status === status) return;
    update.mutate(
      { id, patch: { connection_status: status } },
      {
        onError: (err) =>
          setError(err instanceof Error ? err.message : t("networking.moveError")),
      },
    );
  }
  const archivedQ = useArchivedContacts();
  const archivedCount = archivedQ.data?.length ?? 0;

  const companies = useMemo(
    () => [...new Set(contacts.map((c) => c.current_company).filter(Boolean))].sort(),
    [contacts],
  );

  const scoped = useMemo(() => {
    let rows = contacts;
    if (companyFilter) rows = rows.filter((c) => c.current_company === companyFilter);
    if (audienceFilter) rows = rows.filter((c) => c.audience_tag === audienceFilter);
    const q = search.trim().toLowerCase();
    if (q)
      rows = rows.filter((c) =>
        [c.name, c.current_company, c.current_role]
          .filter(Boolean)
          .some((s) => s!.toLowerCase().includes(q)),
      );
    return rows;
  }, [contacts, companyFilter, audienceFilter, search]);

  const pill = session.data?.enabled ? linkedInStatusPill(session.data.status) : null;
  // A live/queued op wins the button's face (the recording-style pulse): the
  // user sees "in progress" whatever the session chip would otherwise say.
  // Expired/never-connected clicks land on Settings — the connect flow lives
  // there — every other state opens the browser modal.
  const laneBusy =
    opBusy(linkedinBrowser.ops.current) || linkedinBrowser.ops.queued.length > 0;
  const connState = pill
    ? laneBusy
      ? {
          cls: PILL_CLS.good,
          label: t("networking.linkedinPill.inProgress"),
          live: true,
          opensModal: true,
        }
      : {
          cls: PILL_CLS[pill.tone],
          label: t(PILL_LABEL[pill.state]),
          live: false,
          opensModal: pill.state !== "expired" && pill.state !== "disconnected",
        }
    : null;

  return (
    <>
      <header className="flex min-h-[48px] items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.networking")}</h1>
        <div className="ml-auto flex items-center gap-3">
          {/* FIXED footprint (h-[30px] w-[170px] — maintainer, 2026-08-16):
              the label changes with the state, the button's box never does,
              so the header stops shifting as statuses come and go. */}
          {connState && (
            <button
              type="button"
              data-testid="linkedin-state-pill"
              title={t(
                connState.opensModal
                  ? "networking.linkedinPill.titleOpen"
                  : "networking.linkedinPill.titleSettings",
              )}
              onClick={() => {
                if (connState.opensModal) linkedinBrowser.open();
                else void navigate("/settings");
              }}
              className={`inline-flex h-[30px] w-[170px] shrink-0 items-center gap-[6px] rounded-full border px-3 text-[11.5px] font-medium hover:opacity-85 ${connState.cls}`}
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full bg-current ${
                  connState.live ? "animate-pulse" : ""
                }`}
              />
              <span className="truncate">{connState.label}</span>
            </button>
          )}
          {/* Contact statuses refresh ONE way: this Sync button. No on-open
              refresh, no background timer (`docs/internal/linkedin-addon.md`
              section 5; manual-only per the maintainer, 2026-08-15). It only
              appears when the feature is usable, so it never reads as a dead
              control on an install that never enabled Referral Outreach. */}
          {/* ONE merged control at a FIXED footprint (h-[30px] w-[240px] —
              maintainer, 2026-08-16: the stamp and the warn pill beside the
              button kept resizing the header): the status lives INSIDE the
              button — exactly one of the running note, the stopped reason
              (amber text, full reason + stamp in its tooltip), or the
              "Synced Nm ago" stamp — truncated, never moving the box. */}
          {canSync && (() => {
            const ago = syncedAgo(session.data?.contact_sync_last_at ?? null);
            const agoText = ago
              ? t("networking.sync.lastSynced", {
                  when: t(ago.key, ago.n === undefined ? undefined : { n: ago.n }),
                })
              : null;
            const outcome = session.data?.contact_sync_last_outcome ?? null;
            const stoppedKey = syncBusy ? null : syncStoppedKey(outcome);
            const stoppedText = stoppedKey
              ? t(stoppedKey) +
                (outcome && outcome.unprobed > 0
                  ? ` · ${t("networking.sync.notChecked", { n: outcome.unprobed })}`
                  : "")
              : null;
            return (
              <button
                type="button"
                data-testid="sync-contacts-btn"
                onClick={() => sync.mutate()}
                disabled={syncBusy}
                title={t("networking.sync.title")}
                className="inline-flex h-[30px] w-[240px] shrink-0 items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Icon
                  name="refreshCw"
                  size={14}
                  strokeWidth={2}
                  className={syncBusy ? "shrink-0 animate-spin" : "shrink-0"}
                />
                <span className="shrink-0">{t("networking.sync.label")}</span>
                {syncBusy ? (
                  <span className="min-w-0 truncate text-[11px] font-normal text-ink-3">
                    {t("networking.sync.running")}
                  </span>
                ) : stoppedText ? (
                  <span
                    data-testid="sync-stopped-note"
                    title={agoText ? `${stoppedText} · ${agoText}` : stoppedText}
                    className="min-w-0 truncate text-[11px] font-normal text-warn"
                  >
                    {stoppedText}
                  </span>
                ) : agoText ? (
                  <span
                    data-testid="last-synced-stamp"
                    title={t("networking.sync.title")}
                    className="min-w-0 truncate text-[11px] font-normal text-ink-3"
                  >
                    {agoText}
                  </span>
                ) : null}
              </button>
            );
          })()}
          {/* Master Resume: shared launcher, one spot left of the Deleted+Add
              cluster — pixel-aligned with the Job Board / Applications tabs. */}
          <MasterResumeLauncher />
          <HeaderDeletedButton
            label={t("networking.deleted.title")}
            count={archivedCount}
            onClick={() => setDeletedOpen(true)}
            testid="deleted-contacts-btn"
          />
          <HeaderAddButton
            label={t("networking.addByUrl")}
            onClick={() => setAddOpen(true)}
            testid="add-contact-by-url-button"
          />
        </div>
      </header>

      {/* Row 2 — view modifiers, styled like the Job Board / Applications
          filter row: labeled filter groups + "|" separators + trailing
          Search. The old connection-count/degree chips are gone (maintainer,
          2026-08-16: the two numbers counted different populations and read
          as a mismatch). */}
      <FilterBar>
        <FilterGroup label={t("networking.filters.company")}>
          <select
            value={companyFilter ?? ""}
            onChange={(e) => setCompanyFilter(e.target.value || null)}
            className="h-7 rounded-full border border-border-2 bg-surface px-2 text-[11.5px] text-ink focus:border-accent focus:outline-none"
            data-testid="scope-company-select"
          >
            <option value="">{t("networking.filters.all")}</option>
            {companies.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </FilterGroup>
        <FilterSep />
        <FilterGroup label={t("networking.filters.audience")}>
          {(["hm", "recruiter", "leadership"] as AudienceTag[]).map((a) => {
            const n = scoped.filter((c) => c.audience_tag === a).length;
            return (
              <Chip
                key={a}
                active={audienceFilter === a}
                onClick={() => setAudienceFilter(audienceFilter === a ? null : a)}
              >
                {t(audienceTag(a).labelKey)} ({n})
              </Chip>
            );
          })}
        </FilterGroup>
        <FilterSep />
        <SearchBox
          value={search}
          onChange={setSearch}
          placeholder={t("networking.filters.search")}
          testid="networking-search"
        />
      </FilterBar>

      {/* Kanban — same column skeleton as the Applications board (maintainer
          2026-07-23 #6: one width, one header style, one card language). */}
      <main
        className="flex min-h-0 flex-1 gap-3 overflow-x-auto bg-canvas p-4 no-scrollbar"
        data-testid="networking-kanban"
      >
        {COLUMNS.map((col) => {
          const cards = sortColumn(scoped.filter((c) => c.connection_status === col.id));
          return (
            <div
              key={col.id}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => onDropContact(col.id)}
              className="flex w-[280px] shrink-0 flex-col rounded-xl bg-surface-2/60"
              data-status={col.id}
            >
              <div className="flex items-center justify-between px-3 py-2">
                <span className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2">
                  <span className={`h-1.5 w-1.5 rounded-full ${col.dot}`} />
                  {t(col.label)}
                </span>
                <span className="rounded bg-surface-3 px-1.5 text-[11px] text-ink-3">
                  {cards.length}
                </span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-3">
                {cards.length === 0 ? (
                  <p className="px-1 py-2 text-[11px] text-ink-4">{t(col.empty)}</p>
                ) : (
                  cards.map((c) => (
                    <ContactCard
                      key={c.id}
                      c={c}
                      onClick={() => setActive(c)}
                      onDragStart={() => setDragId(c.id)}
                      onDragEnd={() => setDragId(null)}
                    />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </main>

      {addOpen && <AddContactModal onClose={() => setAddOpen(false)} />}
      {deletedOpen && <DeletedContactsModal onClose={() => setDeletedOpen(false)} />}
      {active && (
        <ContactDetailModal
          contact={active}
          onClose={() => setActive(null)}
          onSend={(message) => startSend(active.id, message)}
        />
      )}

      {/* Failure toast (drag moves, send enqueues) — mutations report their
          rejections here rather than failing silently. Outside the tab switch
          on purpose: a send flips the view to Browser, and its error must not
          vanish with the Contacts render. */}
      {error ? (
        <div
          role="alert"
          data-testid="networking-error"
          className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg border border-bad/40 bg-bad-wash px-4 py-2 text-[12.5px] text-bad shadow-lg"
        >
          {error}
          <button onClick={() => setError(null)} className="ml-3 underline">
            {t("networking.dismiss")}
          </button>
        </div>
      ) : null}
    </>
  );
}

// No Delete forever here on purpose: contacts have no permanent-delete endpoint
// (the shared roster leaves that action out when it isn't passed).
function DeletedContactsModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const archivedQ = useArchivedContacts();
  const update = useUpdateContact();
  const rows = archivedQ.data ?? [];
  return (
    <RecoveryListModal
      title={t("networking.deleted.title")}
      onClose={onClose}
      bodyTestid="deleted-contacts-modal"
      rowTestid="deleted-contact-row"
      blurb={t("networking.deleted.blurb")}
      empty={t("networking.deleted.empty")}
      rows={rows.map((c) => ({
        id: c.id,
        avatarName: c.name,
        title: c.name || "—",
        subtitle: `${c.current_role}${c.current_role && c.current_company ? " · " : ""}${c.current_company}`,
      }))}
      restore={{
        label: t("networking.deleted.restore"),
        testid: "restore-contact-btn",
        onRun: (id) => update.mutate({ id, patch: { archived: false } }),
      }}
    />
  );
}

function ContactCard({
  c,
  onClick,
  onDragStart,
  onDragEnd,
}: {
  c: NetContact;
  onClick: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const { t } = useTranslation();
  const days = daysSince(c.last_message_at ?? c.sent_at);
  return (
    <button
      data-testid="contact-card"
      data-contact-id={c.id}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onClick}
      className="flex w-full flex-col gap-1.5 rounded-lg border border-border bg-surface p-3 text-left shadow-sm transition hover:border-border-2 focus:outline-none focus:ring-2 focus:ring-accent"
    >
      <div className="flex items-center gap-2">
        <Avatar name={c.name} />
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-[12.5px] font-semibold leading-tight text-ink">{c.name}</h4>
          <div className="truncate text-[11px] text-ink-3">{c.current_role}{c.current_role && c.current_company ? " · " : ""}{c.current_company}</div>
        </div>
      </div>
      {days != null && (
        <div className="text-[10.5px] text-ink-3">
          {t("networking.card.inStatus", {
            duration: days === 0 ? t("networking.card.today") : t("networking.card.days", { n: days }),
            status: c.connection_status,
          })}
        </div>
      )}
      {c.last_message && (() => {
        const attr = lastMessageAttribution(c);
        return (
          <div
            data-testid="contact-last-message"
            className="rounded-md border border-border bg-surface-3/70 px-2 py-1.5 text-[11px] leading-snug text-ink-3"
          >
            <span className="text-[10px] font-semibold text-ink-2">
              {t(attr.key, attr.name === undefined ? undefined : { name: attr.name })}
            </span>{" "}
            <span className="italic">&ldquo;{c.last_message.slice(0, 90)}{c.last_message.length > 90 ? "…" : ""}&rdquo;</span>
          </div>
        );
      })()}
    </button>
  );
}

// One deliberate "Send" and no second dialog (maintainer, 2026-08-15): this
// modal IS the per-action review surface — it names the recipient (title),
// shows the editable message, and states the real channel plus the
// irreversibility right beside the button — so the single click satisfies the
// P1 per-action-confirmation invariant without a redundant re-ask.
function ContactDetailModal({
  contact,
  onClose,
  onSend,
}: {
  contact: NetContact;
  onClose: () => void;
  onSend: (message: string) => void;
}) {
  const { t } = useTranslation();
  const linkedinBrowser = useLinkedInBrowser();
  const viewInBrowser = useViewInBrowser();
  const update = useUpdateContact();
  const session = useLinkedInSession();
  // The stage-aware composer sends through the one gated path (the parent's
  // useReachOut), so it only surfaces behind the Referral Outreach opt-in —
  // the always-on kanban itself never sends anything.
  const composeEnabled = Boolean(session.data?.enabled);

  return (
    <Modal title={contact.name} onClose={onClose} width={520}>
      <div className="flex flex-col gap-4 px-5 py-5">
        <div className="text-[13px] text-ink-2">
          {contact.current_role} · {contact.current_company}
          <button
            type="button"
            data-testid="contact-open-linkedin"
            onClick={() => {
              // Show the profile on the in-app LinkedIn surface (2026-08-16 —
              // never an external browser). The view is a QUEUED operation:
              // it waits behind whatever is driving the surface instead of
              // interrupting it, and the modal opens to watch either way. A
              // mutation so a failed enqueue surfaces (MutationErrorBanner),
              // never a silent nothing.
              viewInBrowser.mutate({
                url: contact.linkedin_url,
                surface: "linkedin",
                contactId: contact.id,
              });
              onClose();
              linkedinBrowser.open();
            }}
            className="ml-2 text-accent underline"
          >
            {t("networking.detail.linkedin")}
          </button>
        </div>
        {contact.last_message && (() => {
          // Same honest attribution as the kanban card: "Me:" or the
          // contact's first name, never a bare unattributed "Last message".
          const attr = lastMessageAttribution(contact);
          return (
            <div
              data-testid="contact-modal-last-message"
              className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-ink-2"
            >
              <div className="mb-1 text-[10.5px] font-medium text-ink-4">
                {t(attr.key, attr.name === undefined ? undefined : { name: attr.name })}
              </div>
              {contact.last_message}
            </div>
          );
        })()}
        {composeEnabled && (
          <ContactComposer contact={contact} onSubmit={(message) => onSend(message)} />
        )}
        <div className="flex justify-end gap-2">
          <button
            data-testid="contact-archive-btn"
            onClick={() => { update.mutate({ id: contact.id, patch: { archived: true } }); onClose(); }}
            className="h-[30px] rounded-md border border-border bg-surface px-3 text-[12px] text-ink-2 hover:bg-surface-2"
          >
            {t("networking.detail.archive")}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AddContactModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const add = useAddContact();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState<ConnectionStatus>("sent");

  function submit() {
    if (!url.trim()) return;
    add.mutate({
      linkedin_url: url.trim(), name, current_company: company,
      current_role: role, connection_status: status,
    });
    onClose();
  }

  return (
    <Modal title={t("networking.add.title")} onClose={onClose} width={520}>
      <form
        data-testid="add-contact-form"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        className="flex flex-col gap-3 px-5 py-5"
      >
        <p className="text-[12.5px] text-ink-3">
          {t("networking.add.blurb")}
        </p>
        <Field label={t("networking.add.urlLabel")}>
          <input data-testid="add-contact-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.linkedin.com/in/sarah-tan"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
        </Field>
        <Field label={t("networking.add.nameLabel")}>
          <input data-testid="add-contact-name" value={name} onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("networking.add.companyLabel")}>
            <input value={company} onChange={(e) => setCompany(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
          </Field>
          <Field label={t("networking.add.roleLabel")}>
            <input value={role} onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
          </Field>
        </div>
        <Field label={t("networking.add.initialColumn")}>
          <select value={status} onChange={(e) => setStatus(e.target.value as ConnectionStatus)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none">
            <option value="sent">{t("networking.add.optionSent")}</option>
            <option value="accepted">{t("networking.add.optionAccepted")}</option>
            <option value="engagement">{t("networking.add.optionEngagement")}</option>
            <option value="converted">{t("networking.add.optionConverted")}</option>
          </select>
        </Field>
        <div className="mt-1 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="h-[30px] rounded-md border border-border bg-surface px-3 text-[12.5px] text-ink-2 hover:bg-surface-2">
            {t("networking.add.cancel")}
          </button>
          <button type="submit" data-testid="add-contact-submit" className="h-[30px] rounded-md border border-accent bg-accent px-3 text-[12.5px] font-medium text-white hover:bg-accent-ink">
            {t("networking.add.submit")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="block text-[11.5px] font-medium text-ink-2">{label}</label>
      {children}
    </div>
  );
}
