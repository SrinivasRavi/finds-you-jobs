// Networking kanban — Track N3 (US-NW-01/02/03/07/11), restored 2026-07-16 from
// the prior repo (the referral-outreach backend now exists on this sidecar): a
// contact lifecycle kanban (Sent / Accepted → Engagement → Converted | Ghosted),
// cards with a last-message snippet + days-in-column, a company/audience scope
// row, and a manual add-contact-by-URL modal (the rank-don't-gate escape hatch).
// Always reachable (2026-07-09 always-on decision): the CRM carries no account
// risk; the LinkedIn risk toggle gates only automated actions (FR-SET-03).
//
// The LinkedIn status pill below is read-only (`useLinkedInSession`) — the
// connect/enable controls live in Settings, which hasn't landed on this repo
// yet (its own commit); there is no button here to trigger them.

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useAddContact,
  useArchivedContacts,
  useContacts,
  useLinkedInSession,
  useUpdateContact,
} from "../api/queries";
import type { AudienceTag, ConnectionStatus, NetContact } from "../api/types";
import { HeaderAddButton, HeaderDeletedButton } from "../shell/HeaderAddButton";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { Modal } from "../shell/Modal";

// label/empty hold i18n keys — wrapped with t(...) at render.
const COLUMNS: { id: ConnectionStatus; label: string; dot: string; empty: string }[] = [
  { id: "sent", label: "networking.columns.sent", dot: "bg-ink-3", empty: "networking.columnEmpty.sent" },
  { id: "accepted", label: "networking.columns.accepted", dot: "bg-accent", empty: "networking.columnEmpty.accepted" },
  { id: "engagement", label: "networking.columns.engagement", dot: "bg-warn", empty: "networking.columnEmpty.engagement" },
  { id: "ghosted", label: "networking.columns.ghosted", dot: "bg-bad", empty: "networking.columnEmpty.ghosted" },
  { id: "converted", label: "networking.columns.converted", dot: "bg-good", empty: "networking.columnEmpty.converted" },
];

const TAG_LABEL: Record<AudienceTag, string> = {
  peer: "networking.audience.peer", hm: "networking.audience.hm", recruiter: "networking.audience.recruiter",
  leadership: "networking.audience.leadership", other: "networking.audience.other",
};

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}
function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

export function Networking() {
  const { t } = useTranslation();
  const session = useLinkedInSession();
  const contactsQ = useContacts();
  const contacts = useMemo(() => contactsQ.data ?? [], [contactsQ.data]);
  const update = useUpdateContact();
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);
  const [audienceFilter, setAudienceFilter] = useState<AudienceTag | null>(null);
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [deletedOpen, setDeletedOpen] = useState(false);
  const [active, setActive] = useState<NetContact | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const firstDeg = scoped.filter((c) => c.connection_degree === 1).length;
  const secondDeg = scoped.filter((c) => c.connection_degree === 2).length;

  const connState = session.data?.enabled
    ? session.data.status === "valid"
      ? { cls: "bg-good-wash border-good text-good", label: t("networking.linkedinPill.connected") }
      : session.data.status === "connecting"
        ? { cls: "bg-warn-wash border-warn text-warn", label: t("networking.linkedinPill.connecting") }
        : session.data.status === "backing_off"
          ? { cls: "bg-bad-wash border-bad text-bad", label: t("networking.linkedinPill.backingOff") }
          : { cls: "bg-bad-wash border-bad text-bad", label: t("networking.linkedinPill.connect") }
    : null;

  return (
    <>
      <header className="flex min-h-[48px] items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">{t("nav.networking")}</h1>
        <div className="ml-auto flex items-center gap-3">
          {connState && (
            <span
              data-testid="linkedin-state-pill"
              title={t("networking.linkedinPill.title")}
              className={`inline-flex h-[22px] items-center gap-[5px] rounded-full border px-2 text-[11.5px] font-medium ${connState.cls}`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {connState.label}
            </span>
          )}
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
          filter row: connection-count context on the left, labeled filter
          groups + "|" separators + trailing Search on the right. */}
      <FilterBar
        left={
          <>
            <span className="inline-flex h-[22px] items-center gap-[5px] rounded-full border border-good bg-good-wash px-2 text-[11.5px] font-medium text-good">
              <span className="h-1.5 w-1.5 rounded-full bg-good" />
              {t("networking.connectionCount", { count: scoped.length })}
            </span>
            <span className="inline-flex h-[22px] items-center rounded-full border border-border bg-surface-3 px-2 font-mono text-[11px] text-ink-2">
              {t("networking.degreeSummary", { first: firstDeg, second: secondDeg })}
            </span>
          </>
        }
      >
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
                {t(TAG_LABEL[a])} ({n})
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
          const cards = scoped.filter((c) => c.connection_status === col.id);
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
                <span className="rounded bg-surface-3 px-1.5 font-mono text-[11px] text-ink-3">
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

      {/* Drag-move failure toast — the update mutation reports rejections here
          rather than failing silently. */}
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

      {addOpen && <AddContactModal onClose={() => setAddOpen(false)} />}
      {deletedOpen && <DeletedContactsModal onClose={() => setDeletedOpen(false)} />}
      {active && <ContactDetailModal contact={active} onClose={() => setActive(null)} />}
    </>
  );
}

function DeletedContactsModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const archivedQ = useArchivedContacts();
  const update = useUpdateContact();
  const rows = archivedQ.data ?? [];
  return (
    <Modal title={t("networking.deleted.title")} onClose={onClose} width={520}>
      <div data-testid="deleted-contacts-modal" className="px-5 py-4">
        <p className="mb-3 text-[11.5px] text-ink-3">
          {t("networking.deleted.blurb")}
        </p>
        {rows.length === 0 ? (
          <p className="text-[13px] text-ink-3">{t("networking.deleted.empty")}</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((c) => (
              <li
                key={c.id}
                data-testid="deleted-contact-row"
                className="flex items-center gap-3 rounded-md border border-border px-3 py-2"
              >
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
                  {initials(c.name)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-ink">{c.name || "—"}</div>
                  <div className="truncate text-[11px] text-ink-3">
                    {c.current_role}
                    {c.current_role && c.current_company ? " · " : ""}
                    {c.current_company}
                  </div>
                </div>
                <button
                  data-testid="restore-contact-btn"
                  onClick={() => update.mutate({ id: c.id, patch: { archived: false } })}
                  className="rounded-md border border-border-2 px-2 py-1 text-[11.5px] text-ink-2 hover:bg-surface-3"
                >
                  {t("networking.deleted.restore")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
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
        {/* Square initials block — matches the Applications card avatar. */}
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded bg-surface-2 text-[11px] font-semibold text-ink-2">
          {initials(c.name)}
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-[12.5px] font-semibold leading-tight text-ink">{c.name}</h4>
          <div className="truncate text-[11px] text-ink-3">{c.current_role}{c.current_role && c.current_company ? " · " : ""}{c.current_company}</div>
        </div>
      </div>
      {days != null && (
        <div className="font-mono text-[10.5px] text-ink-3">
          {t("networking.card.inStatus", {
            duration: days === 0 ? t("networking.card.today") : t("networking.card.days", { n: days }),
            status: c.connection_status,
          })}
        </div>
      )}
      {c.last_message && (
        <div className="rounded-md border border-border bg-surface-3/70 px-2 py-1.5 text-[11px] leading-snug text-ink-3">
          <span className="text-[10px] font-semibold text-ink-2">{t("networking.card.you")}</span>{" "}
          <span className="italic">&ldquo;{c.last_message.slice(0, 90)}{c.last_message.length > 90 ? "…" : ""}&rdquo;</span>
        </div>
      )}
    </button>
  );
}

function ContactDetailModal({ contact, onClose }: { contact: NetContact; onClose: () => void }) {
  const { t } = useTranslation();
  const update = useUpdateContact();
  return (
    <Modal title={contact.name} onClose={onClose} width={520}>
      <div className="flex flex-col gap-4 px-5 py-5">
        <div className="text-[13px] text-ink-2">
          {contact.current_role} · {contact.current_company}
          <a href={contact.linkedin_url} target="_blank" rel="noreferrer" className="ml-2 text-accent underline">
            {t("networking.detail.linkedin")}
          </a>
        </div>
        {contact.last_message && (
          <div className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-ink-2">
            <div className="mb-1 text-[10.5px] font-medium text-ink-4">{t("networking.detail.lastMessage")}</div>
            {contact.last_message}
          </div>
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
