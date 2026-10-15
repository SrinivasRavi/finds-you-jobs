// The RealApi methods for the chat agent (audit-2026-10-14.md section 8.11).
// Each surface extends the one before it, so RealApi stays 1 class,
// and `real.ts` ends the chain.

import type { Chat, ChatMessage, ChatSendAccepted, ChatSummary } from "../types";
import { NetworkingApi } from "./networking";

export class ChatApi extends NetworkingApi {
  listChats(): Promise<ChatSummary[]> {
    return this.req<ChatSummary[]>("/api/chats");
  }

  getChat(id: string): Promise<Chat> {
    return this.req<Chat>(`/api/chats/${id}`);
  }

  /** Store the message and queue 1 agent turn. No `chatId` starts a chat. */
  async sendChatMessage(text: string, chatId: string | null): Promise<ChatSendAccepted> {
    return (await this.json("POST", "/api/chats", { text, chat_id: chatId })) as ChatSendAccepted;
  }

  async answerChatCard(
    chatId: string,
    messageId: string,
    answer: "confirm" | "dismiss",
  ): Promise<ChatMessage> {
    const path = `/api/chats/${chatId}/cards/${messageId}/${answer}`;
    return (await this.json("POST", path, {})) as ChatMessage;
  }
}
