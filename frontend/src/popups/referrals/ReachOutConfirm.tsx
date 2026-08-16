// Pre-send confirmation for ONE contact (US-NW-09 / vision). Reworked
// 2026-07-30: the batch "Send {{count}} messages?" confirm went away with the
// multi-select — each row's Connect/Message button opens this for exactly one
// person, showing the message that will actually go out (per-contact confirm;
// posture doc section 5.1). Since 2026-08-16 this is also THE editor for a
// connected send: the message box is an editable textarea (the maintainer:
// edit where you confirm, not in a row box that only expands to be revealed).
// Not memoized: it only mounts while the confirm overlay is open, so there is
// no tree to shield.

import { Trans, useTranslation } from "react-i18next";

export function ReachOutConfirm({
  name,
  channel,
  message,
  sending,
  onChange,
  onCancel,
  onSend,
}: {
  name: string;
  channel: "dm" | "connection_note";
  message: string;
  sending: boolean;
  /** Edits flow into the modal's per-contact draft map, so a cancel keeps them. */
  onChange: (v: string) => void;
  onCancel: () => void;
  onSend: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-[rgba(0,0,0,0.35)]" data-testid="reach-out-confirm">
      <div className="w-[420px] rounded-[12px] border border-border bg-surface p-5 shadow-xl">
        <h3 className="text-[14px] font-semibold text-ink">
          {t("popups.referrals.sendConfirmTitle", { name })}
        </h3>
        <p className="mt-1 text-[11.5px] text-ink-3">
          {channel === "dm"
            ? t("popups.referrals.sendConfirmChannelDm")
            : t("popups.referrals.sendConfirmChannelInvite")}
        </p>
        {/* The exact text that will be sent — confirm what, not just whether,
            and edit it right here (the one place the send actually leaves from). */}
        <textarea
          data-testid="reach-out-confirm-message"
          value={message}
          onChange={(e) => onChange(e.target.value)}
          disabled={sending}
          rows={5}
          className="mt-3 max-h-[180px] w-full resize-none overflow-y-auto rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] leading-relaxed text-ink-2 focus:border-accent focus:outline-none disabled:opacity-60"
        />
        <p className="mt-3 text-[12px] text-ink-3">
          <Trans
            i18nKey="popups.referrals.sendConfirmBody"
            components={{ span: <span className="text-ink-2" /> }}
          />
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button className="h-[30px] rounded-md px-3 text-[12px] text-ink-2 hover:bg-surface-2" onClick={onCancel}>
            {t("popups.referrals.cancel")}
          </button>
          <button
            data-testid="reach-out-confirm-btn"
            disabled={sending}
            className="inline-flex h-[30px] items-center gap-1.5 rounded-md border border-accent bg-accent px-3 text-[12px] font-medium text-white hover:bg-accent-ink disabled:cursor-not-allowed disabled:opacity-60"
            onClick={onSend}
          >
            {sending ? (
              <>
                <span className="inline-block h-3 w-3 animate-spin rounded-full border border-white/60 border-t-transparent" />
                {t("popups.referrals.sendingEllipsis")}
              </>
            ) : (
              t("popups.referrals.sendNow")
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
