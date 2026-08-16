// Compose-and-send block inside the contact detail modal (maintainer spec,
// 2026-08-14): the contact's kanban stage picks the template candidate set, a
// dropdown picks one (first option prefilled on open), the box stays fully
// editable, and the send routes through the ONE gated path the parent owns
// (useReachOut). No second send path. The single "Send" here IS the per-action
// confirmation (maintainer, 2026-08-15): recipient, editable message, and the
// channel + irreversibility line all sit on this surface, so no second dialog
// re-asks.
//
// Messageability is stated honestly, mirroring the server's routing
// (`networker_ops.send_entrypoint`): a 1st-degree contact gets a DM; anyone
// else gets a connection request with the text as its note — except a contact
// whose invite is still pending (`sent`, not 1st-degree), where a DM would
// certainly fail and a second invite isn't a thing we send, so the composer
// says why instead of offering a dead button.

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { NetContact } from "../api/types";
import { stageTemplateOptions } from "./stageTemplates";

export type ComposeChannel = "dm" | "connection_note";

export function ContactComposer({
  contact,
  onSubmit,
}: {
  contact: NetContact;
  onSubmit: (message: string, channel: ComposeChannel) => void;
}) {
  const { t } = useTranslation();
  const options = useMemo(
    () => stageTemplateOptions(contact.connection_status, contact, t),
    [contact, t],
  );
  // First option prefills the box on open (addendum 2026-08-14); switching
  // options refills it; "Write my own" clears it. Always editable.
  const [selected, setSelected] = useState(options[0]?.id ?? "custom");
  const [message, setMessage] = useState(options[0]?.body ?? "");

  const channel: ComposeChannel = contact.is_first_degree ? "dm" : "connection_note";
  const invitePending = !contact.is_first_degree && contact.connection_status === "sent";

  if (invitePending) {
    return (
      <div
        className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] text-ink-3"
        data-testid="contact-compose-blocked"
      >
        {t("networking.compose.blockedInvitePending")}
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-2"
      data-testid="contact-compose"
      onSubmit={(e) => {
        e.preventDefault();
        if (message.trim()) onSubmit(message, channel);
      }}
    >
      <div className="text-[11.5px] font-medium text-ink-2">{t("networking.compose.title")}</div>
      {options.length > 0 && (
        <select
          data-testid="contact-compose-template"
          value={selected}
          onChange={(e) => {
            const id = e.target.value;
            setSelected(id);
            setMessage(options.find((o) => o.id === id)?.body ?? "");
          }}
          className="h-8 w-full rounded-md border border-border bg-surface px-2 text-[12px] text-ink focus:border-accent focus:outline-none"
        >
          {options.map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
          <option value="custom">{t("networking.compose.custom")}</option>
        </select>
      )}
      <textarea
        data-testid="contact-compose-message"
        aria-label={t("networking.compose.messageLabel")}
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={5}
        className="w-full resize-none rounded-md border border-border bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink focus:border-accent focus:outline-none"
      />
      <div className="flex items-end justify-between gap-3">
        {/* The channel + irreversibility line sits WITH the one button that
            acts (maintainer, 2026-08-15): this composer is the per-action
            review surface, so the single Send click stays informed — no second
            dialog re-asks. */}
        <p className="text-[11px] leading-snug text-ink-4" data-testid="contact-compose-channel">
          {channel === "dm"
            ? t("networking.compose.channelDm")
            : t("networking.compose.channelInvite")}
        </p>
        <button
          type="submit"
          data-testid="contact-compose-send"
          disabled={!message.trim()}
          className="h-[30px] shrink-0 rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-60"
        >
          {t("networking.compose.send")}
        </button>
      </div>
    </form>
  );
}
