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

import {
  useAddContact,
  useArchivedContacts,
  useContacts,
  useLinkedInSession,
  useUpdateContact,
} from "../api/queries";
import type { AudienceTag, ConnectionStatus, NetContact } from "../api/types";
import { Icon } from "../shell/icons";
import { Chip, FilterBar, FilterGroup, FilterSep, SearchBox } from "../shell/FilterRow";
import { Modal } from "../shell/Modal";

const COLUMNS: { id: ConnectionStatus; label: string; dot: string; empty: string }[] = [
  { id: "sent", label: "Sent", dot: "bg-ink-3", empty: "Awaiting accepts — keep sending." },
  { id: "accepted", label: "Accepted", dot: "bg-accent", empty: "Accepted, awaiting first reply." },
  { id: "engagement", label: "Engagement", dot: "bg-warn", empty: "Active conversation — nudge as needed." },
  { id: "ghosted", label: "Ghosted", dot: "bg-bad", empty: "No activity for 7+ days." },
  { id: "converted", label: "Converted", dot: "bg-good", empty: "They referred you or intro'd." },
];

const TAG_LABEL: Record<AudienceTag, string> = {
  peer: "Peer", hm: "Hiring Team", recruiter: "Recruiter", leadership: "Top Management", other: "Other",
};

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
}
function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

export function Networking() {
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
          setError(err instanceof Error ? err.message : "Could not move contact."),
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
      ? { cls: "bg-good-wash border-good text-good", label: "LinkedIn connected" }
      : session.data.status === "connecting"
        ? { cls: "bg-warn-wash border-warn text-warn", label: "Connecting…" }
        : session.data.status === "backing_off"
          ? { cls: "bg-bad-wash border-bad text-bad", label: "Backing off" }
          : { cls: "bg-bad-wash border-bad text-bad", label: "Connect LinkedIn" }
    : null;

  return (
    <>
      <header className="flex min-h-[48px] items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">Networking</h1>
        <div className="ml-auto flex items-center gap-3">
          {connState && (
            <span
              data-testid="linkedin-state-pill"
              title="Read-only — connect/enable LinkedIn from Settings"
              className={`inline-flex h-[22px] items-center gap-[5px] rounded-full border px-2 text-[11.5px] font-medium ${connState.cls}`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {connState.label}
            </span>
          )}
          <button
            data-testid="deleted-contacts-btn"
            onClick={() => setDeletedOpen(true)}
            className="relative inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-border-2 bg-surface px-3 text-[12px] font-medium text-ink-2 hover:bg-surface-3 hover:text-ink"
          >
            <Icon name="trash" size={14} strokeWidth={2} />
            Deleted Contacts
            {archivedCount > 0 ? (
              <span className="ml-1 inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-bad px-1 font-mono text-[10px] font-bold text-white">
                {archivedCount}
              </span>
            ) : null}
          </button>
          {/* Sized identically to Job Board's "+ Add a job by URL" (icon +
              rounded-7 + same paddings) for cross-tab consistency. */}
          <button
            data-testid="add-contact-by-url-button"
            onClick={() => setAddOpen(true)}
            className="inline-flex h-[30px] items-center gap-1.5 rounded-7 border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink"
          >
            <Icon name="plus" size={14} strokeWidth={2} />
            Add contact by URL
          </button>
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
              {scoped.length} connection{scoped.length === 1 ? "" : "s"}
            </span>
            <span className="inline-flex h-[22px] items-center rounded-full border border-border bg-surface-3 px-2 font-mono text-[11px] text-ink-2">
              {firstDeg} 1st · {secondDeg} 2nd
            </span>
          </>
        }
      >
        <FilterGroup label="Company">
          <select
            value={companyFilter ?? ""}
            onChange={(e) => setCompanyFilter(e.target.value || null)}
            className="h-7 rounded-full border border-border-2 bg-surface px-2 text-[11.5px] text-ink focus:border-accent focus:outline-none"
            data-testid="scope-company-select"
          >
            <option value="">All</option>
            {companies.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </FilterGroup>
        <FilterSep />
        <FilterGroup label="Audience">
          {(["hm", "recruiter", "leadership"] as AudienceTag[]).map((a) => {
            const n = scoped.filter((c) => c.audience_tag === a).length;
            return (
              <Chip
                key={a}
                active={audienceFilter === a}
                onClick={() => setAudienceFilter(audienceFilter === a ? null : a)}
              >
                {TAG_LABEL[a]} ({n})
              </Chip>
            );
          })}
        </FilterGroup>
        <FilterSep />
        <SearchBox
          value={search}
          onChange={setSearch}
          placeholder="Search"
          testid="networking-search"
        />
      </FilterBar>

      <main className="flex flex-1 flex-col gap-3 overflow-hidden px-4 py-3">
        {/* Kanban */}
        <div className="min-h-0 flex-1">
          <div className="flex h-full gap-3 overflow-x-auto pb-2" data-testid="networking-kanban">
            {COLUMNS.map((col) => {
              const cards = scoped.filter((c) => c.connection_status === col.id);
              return (
                <div
                  key={col.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => onDropContact(col.id)}
                  className="flex w-[260px] shrink-0 flex-col gap-2 rounded-xl bg-surface-2 p-2.5"
                  data-status={col.id}
                >
                  <div className="flex items-center gap-2 px-1">
                    <h5 className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wider text-ink-2">
                      <span className={`h-1.5 w-1.5 rounded-full ${col.dot}`} />
                      {col.label}
                    </h5>
                    <span className="ml-auto rounded bg-surface-3 px-1.5 py-px font-mono text-[11px] text-ink-3">{cards.length}</span>
                  </div>
                  <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                    {cards.length === 0 ? (
                      <div className="rounded-lg border-2 border-dashed border-border-2 px-3 py-3.5 text-center font-mono text-[11.5px] text-ink-4">
                        {col.empty}
                      </div>
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
          </div>
        </div>
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
            dismiss
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
  const archivedQ = useArchivedContacts();
  const update = useUpdateContact();
  const rows = archivedQ.data ?? [];
  return (
    <Modal title="Deleted Contacts" onClose={onClose} width={520}>
      <div data-testid="deleted-contacts-modal" className="px-5 py-4">
        <p className="mb-3 text-[11.5px] text-ink-3">
          Deleted contacts are hidden from the kanban but keep their outreach history. Restore one
          to bring it back, or re-add it by URL — either way it returns to where it was.
        </p>
        {rows.length === 0 ? (
          <p className="text-[13px] text-ink-3">No deleted contacts.</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((c) => (
              <li
                key={c.id}
                data-testid="deleted-contact-row"
                className="flex items-center gap-3 rounded-md border border-border px-3 py-2"
              >
                <span className="inline-grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-2 font-mono text-[11px] font-semibold text-ink-2">
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
                  Restore
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
  const days = daysSince(c.last_message_at ?? c.sent_at);
  return (
    <button
      data-testid="contact-card"
      data-contact-id={c.id}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onClick}
      className="flex w-full flex-col gap-1.5 rounded-lg border border-border bg-surface p-2.5 text-left transition hover:border-border-2 focus:outline-none focus:ring-2 focus:ring-accent"
    >
      <div className="flex items-center gap-2">
        <span className="inline-grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-3 font-mono text-[11px] font-semibold text-ink-2">
          {initials(c.name)}
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="truncate text-[12.5px] font-semibold leading-tight text-ink">{c.name}</h4>
          <div className="truncate text-[11px] text-ink-3">{c.current_role}{c.current_role && c.current_company ? " · " : ""}{c.current_company}</div>
        </div>
      </div>
      {days != null && (
        <div className="font-mono text-[10.5px] text-ink-3">
          {days === 0 ? "today" : `${days}d`} in {c.connection_status}
        </div>
      )}
      {c.last_message && (
        <div className="rounded-md border border-border bg-surface-3/70 px-2 py-1.5 text-[11px] leading-snug text-ink-3">
          <span className="text-[10px] font-semibold text-ink-2">You:</span>{" "}
          <span className="italic">&ldquo;{c.last_message.slice(0, 90)}{c.last_message.length > 90 ? "…" : ""}&rdquo;</span>
        </div>
      )}
    </button>
  );
}

function ContactDetailModal({ contact, onClose }: { contact: NetContact; onClose: () => void }) {
  const update = useUpdateContact();
  return (
    <Modal title={contact.name} onClose={onClose} width={520}>
      <div className="flex flex-col gap-4 px-5 py-5">
        <div className="text-[13px] text-ink-2">
          {contact.current_role} · {contact.current_company}
          <a href={contact.linkedin_url} target="_blank" rel="noreferrer" className="ml-2 text-accent underline">
            LinkedIn
          </a>
        </div>
        {contact.last_message && (
          <div className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-ink-2">
            <div className="mb-1 font-mono text-[10px] uppercase text-ink-4">Last message</div>
            {contact.last_message}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            data-testid="contact-archive-btn"
            onClick={() => { update.mutate({ id: contact.id, patch: { archived: true } }); onClose(); }}
            className="h-[30px] rounded-md border border-border bg-surface px-3 text-[12px] text-ink-2 hover:bg-surface-2"
          >
            Archive
          </button>
        </div>
      </div>
    </Modal>
  );
}

function AddContactModal({ onClose }: { onClose: () => void }) {
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
    <Modal title="Add a contact" onClose={onClose} width={520}>
      <form
        data-testid="add-contact-form"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        className="flex flex-col gap-3 px-5 py-5"
      >
        <p className="text-[12.5px] text-ink-3">
          Add anyone by their LinkedIn URL — always available regardless of LinkedIn state (rank, don't gate).
        </p>
        <Field label="LinkedIn profile URL">
          <input data-testid="add-contact-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.linkedin.com/in/sarah-tan"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
        </Field>
        <Field label="Name">
          <input data-testid="add-contact-name" value={name} onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Company">
            <input value={company} onChange={(e) => setCompany(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
          </Field>
          <Field label="Role">
            <input value={role} onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none" />
          </Field>
        </div>
        <Field label="Initial column">
          <select value={status} onChange={(e) => setStatus(e.target.value as ConnectionStatus)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none">
            <option value="sent">Sent — invite is out</option>
            <option value="accepted">Accepted — already connected</option>
            <option value="engagement">Engagement — actively chatting</option>
            <option value="converted">Converted — referring me</option>
          </select>
        </Field>
        <div className="mt-1 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="h-[30px] rounded-md border border-border bg-surface px-3 text-[12.5px] text-ink-2 hover:bg-surface-2">
            Cancel
          </button>
          <button type="submit" data-testid="add-contact-submit" className="h-[30px] rounded-md border border-accent bg-accent px-3 text-[12.5px] font-medium text-white hover:bg-accent-ink">
            Add contact
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
