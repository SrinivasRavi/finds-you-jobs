// The chat agent's drawer (audit-2026-10-14.md section 8.11, D91): closed by
// default, opened from 1 rail tile on every screen, and drawn above any modal so
// it sits beside the LinkedIn view. A send or Submit the user keeps on "confirm
// each" arrives as a card; only the user's click here runs it.

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useAnswerChatCard, useChat, useChats, useSendChatMessage } from "../api/queries";
import type { ChatMessage } from "../api/types";
import { Icon } from "./icons";
import { Markdown } from "./Markdown";

interface ChatState {
  open: boolean;
  toggle: () => void;
}

// The default lets a surface render without the provider (unit tests).
const ChatContext = createContext<ChatState>({ open: false, toggle: () => {} });

const CHAT_WIDTH_PX = 380; // the drawer's `w-[380px]` and the page's `pr-[380px]`

export function ChatProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  // A modal centres in the room the drawer leaves, so it never hides under it.
  useEffect(() => {
    document.documentElement.style.setProperty("--fyj-chat-w", open ? `${CHAT_WIDTH_PX}px` : "0px");
  }, [open]);
  return (
    <ChatContext.Provider value={{ open, toggle: () => setOpen((o) => !o) }}>
      {/* The page makes room for the drawer rather than hide under it. */}
      <div className={open ? "pr-[380px]" : ""}>{children}</div>
      {open ? <ChatDrawer onClose={() => setOpen(false)} /> : null}
    </ChatContext.Provider>
  );
}

/** The rail tile. A button, not a route: the drawer opens over any screen. */
export function ChatRailButton() {
  const { t } = useTranslation();
  const { open, toggle } = useContext(ChatContext);
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={open}
      data-testid="chat-open"
      className={
        "flex h-[52px] w-[64px] flex-col items-center justify-center gap-1 rounded-lg transition-colors " +
        (open ? "bg-accent text-white shadow-sm" : "text-ink-3 hover:bg-surface-3 hover:text-ink")
      }
    >
      <Icon name="message" size={17} strokeWidth={1.6} />
      <span className="text-center text-[10px] font-medium leading-none">{t("shell.chat.rail")}</span>
    </button>
  );
}

const ARGS_CHARS = 2000;
const toolWords = (tool: unknown) => String(tool ?? "").replace(/_/g, " ");

function ToolLine({ m }: { m: ChatMessage }) {
  const { t } = useTranslation();
  const result = (m.payload.result ?? {}) as { error?: unknown; needs_confirmation?: boolean };
  if (result.needs_confirmation) return null; // its card says it
  const failed = result.error != null;
  return (
    <div
      className={"px-1 text-[11.5px] " + (failed ? "text-bad" : "text-ink-4")}
      data-testid="chat-tool-line"
      title={failed ? String(result.error) : undefined}
    >
      {t(failed ? "shell.chat.toolFailed" : "shell.chat.toolUsed", {
        tool: toolWords(m.payload.tool),
      })}
    </div>
  );
}

function Card({ m, chatId }: { m: ChatMessage; chatId: string }) {
  const { t } = useTranslation();
  const answer = useAnswerChatCard(chatId);
  const state = String(m.payload.state ?? "pending");
  return (
    <div className="rounded-lg border border-warn/40 bg-warn-wash p-3" data-testid="chat-card">
      <div className="text-[12.5px] font-semibold text-ink">
        {t("shell.chat.card.title", { tool: toolWords(m.payload.tool) })}
      </div>
      <div className="mt-1 text-[12px] text-ink-2">{String(m.payload.reason ?? "")}</div>
      {/* What the click will do, so the OK is never given blind. */}
      <pre
        className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface px-2 py-1.5 text-[11px] text-ink-2"
        data-testid="chat-card-args"
      >
        {JSON.stringify(m.payload.args ?? {}, null, 2).slice(0, ARGS_CHARS)}
      </pre>
      {state === "pending" ? (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            className="rounded-md bg-accent px-3 py-1 text-[12px] font-medium text-white disabled:opacity-50"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ messageId: m.id, answer: "confirm" })}
            data-testid="chat-card-confirm"
          >
            {t("shell.chat.card.confirm")}
          </button>
          <button
            type="button"
            className="rounded-md border border-border-2 px-3 py-1 text-[12px] text-ink-2 disabled:opacity-50"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ messageId: m.id, answer: "dismiss" })}
            data-testid="chat-card-dismiss"
          >
            {t("shell.chat.card.dismiss")}
          </button>
        </div>
      ) : (
        <div className="mt-2 text-[12px] font-medium text-ink-3" data-testid="chat-card-state">
          {t(`shell.chat.card.state.${state}`)}
        </div>
      )}
    </div>
  );
}

function Message({ m, chatId }: { m: ChatMessage; chatId: string }) {
  if (m.role === "tool") return <ToolLine m={m} />;
  if (m.role === "confirm") return <Card m={m} chatId={chatId} />;
  const text = String(m.payload.text ?? "");
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-accent px-3 py-2 text-[12.5px] text-white"
          data-testid="chat-user"
        >
          {text}
        </div>
      </div>
    );
  }
  return (
    <div className="max-w-[92%] rounded-lg bg-surface-3 px-3 py-2 text-[12.5px] text-ink" data-testid="chat-agent">
      <Markdown md={text} />
    </div>
  );
}

function ChatDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [chatId, setChatId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const chats = useChats(true);
  const chat = useChat(chatId);
  const send = useSendChatMessage();
  const end = useRef<HTMLDivElement>(null);
  const messages = chat.data?.messages ?? [];
  const busy = (chat.data?.busy ?? false) || send.isPending;

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages.length, busy]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    send.mutate({ text, chatId }, { onSuccess: (r) => setChatId(r.chat_id) });
    setDraft("");
  };

  return (
    <aside
      className="fixed right-0 top-0 z-[60] flex h-screen w-[380px] flex-col border-l border-border bg-surface shadow-xl"
      aria-label={t("shell.chat.title")}
      data-testid="chat-drawer"
    >
      <header className="flex min-h-[48px] items-center gap-2 border-b border-border px-3">
        <h2 className="flex-1 text-[14px] font-semibold text-ink">{t("shell.chat.title")}</h2>
        {(chats.data?.length ?? 0) > 0 ? (
          <select
            className="max-w-[150px] rounded-md border border-border bg-surface px-1.5 py-1 text-[11.5px] text-ink-2"
            value={chatId ?? ""}
            onChange={(e) => setChatId(e.target.value || null)}
            aria-label={t("shell.chat.history")}
            data-testid="chat-history"
          >
            <option value="">{t("shell.chat.newChat")}</option>
            {chats.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-ink-3 hover:bg-surface-3"
          aria-label={t("shell.chat.close")}
          data-testid="chat-close"
        >
          <Icon name="x" size={16} />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3" data-testid="chat-messages">
        {messages.length === 0 && !busy ? (
          <p className="px-1 text-[12.5px] text-ink-3">{t("shell.chat.empty")}</p>
        ) : null}
        {messages.map((m) => (
          <Message key={m.id} m={m} chatId={chatId as string} />
        ))}
        {busy ? (
          <div className="px-1 text-[11.5px] text-ink-4" data-testid="chat-working">
            {t("shell.chat.working")}
          </div>
        ) : null}
        {!busy && chat.data?.error ? (
          <div className="px-1 text-[12px] text-bad" data-testid="chat-error">
            {t("shell.chat.turnFailed", { reason: chat.data.error })}
          </div>
        ) : null}
        <div ref={end} />
      </div>

      <form
        className="flex items-end gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder={t("shell.chat.placeholder")}
          className="min-h-[40px] flex-1 resize-none rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink"
          data-testid="chat-input"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="rounded-md bg-accent px-3 py-2 text-[12.5px] font-medium text-white disabled:opacity-50"
          data-testid="chat-send"
        >
          {t("shell.chat.send")}
        </button>
      </form>
    </aside>
  );
}
