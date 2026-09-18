import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { renderMarkdownLite } from "../lib/markdownLite";
import { parseSseStream } from "../lib/sseStream";
import type {
  Citation,
  ConversationHistoryResponse,
  ConversationListResponse,
  DocumentListResponse,
} from "../lib/types";

interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "error";
  content: string;
  citations?: Citation[];
  feedback?: "up" | "down" | null;
}

interface SidebarConversation {
  id: string;
  title: string;
}

interface SidebarDocument {
  id: string;
  title: string;
}

function newConversationId(): string {
  return crypto.randomUUID();
}

const ACTIVE_CONVERSATION_KEY_PREFIX = "rag-active-conversation:";

/** Reads/writes are best-effort -- a private-browsing/storage-blocked browser just falls back
 * to always starting a new conversation, same as before this feature existed (ERP-060). */
function getStoredConversationId(userId: string): string | null {
  try {
    return localStorage.getItem(ACTIVE_CONVERSATION_KEY_PREFIX + userId);
  } catch {
    return null;
  }
}

function setStoredConversationId(userId: string, id: string): void {
  try {
    localStorage.setItem(ACTIVE_CONVERSATION_KEY_PREFIX + userId, id);
  } catch {
    // best-effort only
  }
}

/** "document.pdf, p. 3" or "document.pdf, p. 3-4" when the citation spans multiple pages. */
function formatCitation(citation: Citation): string {
  const pages =
    citation.page_start === citation.page_end
      ? `p. ${citation.page_start}`
      : `p. ${citation.page_start}-${citation.page_end}`;
  return `${citation.source_filename}, ${pages}`;
}

function TypingIndicator() {
  return (
    <div className="flex gap-1 px-1 py-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" />
    </div>
  );
}

export default function ChatPage() {
  const { userId } = useAuth();
  const [conversationId, setConversationId] = useState<string>(
    () => (userId && getStoredConversationId(userId)) || newConversationId()
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [recentConversations, setRecentConversations] = useState<SidebarConversation[]>([]);
  const [documents, setDocuments] = useState<SidebarDocument[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Both lists are hydrated from the backend (not localStorage) so chat history and the
  // document list survive a login from a new browser/device -- the data was always
  // persisted server-side, there was previously just no "list mine" endpoint to read it back.
  useEffect(() => {
    if (!userId) return;
    void (async () => {
      const response = await apiFetch("/conversations");
      if (!response.ok) return;
      const body = (await response.json()) as ConversationListResponse;
      setRecentConversations(
        body.conversations.map((conversation) => ({
          id: conversation.conversation_id,
          title:
            conversation.title ??
            (conversation.preview ? conversation.preview.slice(0, 60) : "New conversation"),
        }))
      );
    })();
  }, [userId]);

  // Restores the conversation that was open before a refresh (ERP-060) -- without this, every
  // page load silently started a brand new blank chat regardless of what was open before,
  // which read as "refreshing cleared my history" even though nothing was actually deleted.
  useEffect(() => {
    if (!userId) return;
    const stored = getStoredConversationId(userId);
    if (stored) void loadConversationHistory(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  // Persists the active conversation on every change so a refresh can restore it above.
  useEffect(() => {
    if (!userId) return;
    setStoredConversationId(userId, conversationId);
  }, [userId, conversationId]);

  useEffect(() => {
    if (!userId) return;
    void (async () => {
      const response = await apiFetch("/documents");
      if (!response.ok) return;
      const body = (await response.json()) as DocumentListResponse;
      setDocuments(
        body.documents.map((document) => ({ id: document.document_id, title: document.filename }))
      );
    })();
  }, [userId]);

  // ERP-064: keep the latest message in view as the conversation grows or streams in.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function startNewConversation(): void {
    setConversationId(newConversationId());
    setMessages([]);
  }

  /**
   * Loads a conversation's full history from the backend. Previously, clicking a sidebar
   * entry only switched `conversationId` and left the chat pane empty -- the conversation
   * had no way to render its own history, which is what made a "recent conversation" look
   * broken (visible in the list, but never actually openable). Also reused on mount to
   * restore whatever conversation was active before a refresh (ERP-060) -- a 404 (e.g. a
   * "new chat" that was never actually sent) is treated as an empty conversation, not an
   * error, since restoring it should feel identical to starting fresh.
   */
  async function loadConversationHistory(id: string): Promise<void> {
    setMessages([]);
    const response = await apiFetch(`/conversations/${id}`);
    if (!response.ok) return;
    const body = (await response.json()) as ConversationHistoryResponse;
    setMessages(
      body.messages.map((message) => ({
        id: message.id,
        role: message.role === "user" ? "user" : "assistant",
        content: message.content,
        feedback: message.feedback,
      }))
    );
  }

  async function selectConversation(id: string): Promise<void> {
    setConversationId(id);
    await loadConversationHistory(id);
  }

  async function renameConversation(id: string, title: string): Promise<void> {
    const response = await apiFetch(`/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (response.status === 409) {
      window.alert("You already have a conversation with that name. Please choose another.");
      return;
    }
    if (!response.ok) return;
    setRecentConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
  }

  function handleRename(conv: SidebarConversation): void {
    const title = window.prompt("Rename conversation", conv.title);
    if (!title || !title.trim()) return;
    void renameConversation(conv.id, title.trim());
  }

  async function setMessageFeedback(messageId: string, rating: "up" | "down"): Promise<void> {
    const isRemovingRating = messages.find((m) => m.id === messageId)?.feedback === rating;
    const response = await apiFetch(`/conversations/messages/${messageId}/feedback`, {
      method: isRemovingRating ? "DELETE" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: isRemovingRating ? undefined : JSON.stringify({ rating }),
    });
    if (!response.ok) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId ? { ...m, feedback: isRemovingRating ? null : rating } : m
      )
    );
  }

  async function sendMessage(): Promise<void> {
    const query = input.trim();
    if (!query || isStreaming || !userId) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: query }]);
    setIsStreaming(true);

    const isFirstMessage = messages.length === 0;

    try {
      const response = await apiFetch("/generation/query/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, conversation_id: conversationId }),
      });

      if (!response.ok) {
        setMessages((prev) => [
          ...prev,
          { role: "error", content: `Request failed (${response.status}). Please try again.` },
        ]);
        return;
      }

      let assistantText = "";
      let citations: Citation[] = [];
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      for await (const sseEvent of parseSseStream(response)) {
        if (sseEvent.event === "citations") {
          citations = (sseEvent.data as { citations: Citation[] }).citations;
        } else if (sseEvent.event === "token") {
          assistantText += (sseEvent.data as { text: string }).text;
          setMessages((prev) => [
            ...prev.slice(0, -1),
            { role: "assistant", content: assistantText, citations },
          ]);
        } else if (sseEvent.event === "done") {
          const assistantMessageId = (sseEvent.data as { assistant_message_id?: string })
            .assistant_message_id;
          if (assistantMessageId) {
            setMessages((prev) => [
              ...prev.slice(0, -1),
              { role: "assistant", content: assistantText, citations, id: assistantMessageId },
            ]);
          }
        } else if (sseEvent.event === "error") {
          setMessages((prev) => [
            ...prev.slice(0, -1),
            { role: "assistant", content: assistantText, citations },
            { role: "error", content: (sseEvent.data as { detail: string }).detail },
          ]);
        }
      }

      if (isFirstMessage) {
        setRecentConversations((prev) => [
          { id: conversationId, title: query.slice(0, 60) },
          ...prev.filter((conversation) => conversation.id !== conversationId),
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "error", content: "Something went wrong while streaming the answer." },
      ]);
    } finally {
      setIsStreaming(false);
    }
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-64 flex-col overflow-y-auto border-r border-slate-200 bg-slate-50 p-4">
        <Button className="mb-4 w-full" onClick={startNewConversation}>
          New chat
        </Button>
        <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Recent conversations
        </p>
        {recentConversations.length === 0 ? (
          <p className="px-1 text-sm text-slate-400">No conversations yet.</p>
        ) : (
          <ul className="mb-6 flex flex-col gap-1">
            {recentConversations.map((conv) => (
              <li key={conv.id} className="flex items-center gap-1">
                <button
                  className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                    conv.id === conversationId ? "bg-slate-200 font-medium" : "text-slate-700"
                  }`}
                  onClick={() => void selectConversation(conv.id)}
                >
                  {conv.title}
                </button>
                <button
                  className="shrink-0 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                  title="Rename conversation"
                  onClick={() => handleRename(conv)}
                >
                  ✎
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Your documents
        </p>
        {documents.length === 0 ? (
          <p className="px-1 text-sm text-slate-400">
            No documents uploaded yet — visit Documents to add one.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {documents.map((doc) => (
              <li key={doc.id} className="flex items-center gap-2 px-2 py-1 text-sm text-slate-600">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                <span className="truncate">{doc.title}</span>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <main className="flex flex-1 flex-col bg-white">
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 && (
            <p className="mt-12 text-center text-sm text-slate-400">
              Ask a question about one of your uploaded documents to get started.
            </p>
          )}
          <div className="mx-auto flex max-w-2xl flex-col gap-3">
            {messages.map((message, index) => {
              const isLast = index === messages.length - 1;
              const isPendingAssistant =
                isLast && isStreaming && message.role === "assistant" && message.content === "";
              return (
                <div
                  key={index}
                  data-role={message.role}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[80%] rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2 text-white"
                      : message.role === "error"
                        ? "max-w-[80%] rounded-2xl border border-red-200 bg-red-50 px-4 py-2 text-red-700"
                        : "max-w-[80%] rounded-2xl rounded-bl-sm border border-slate-200 bg-slate-50 px-4 py-2 text-slate-900"
                  }
                >
                  {isPendingAssistant ? (
                    <TypingIndicator />
                  ) : (
                    <div className="text-sm">{renderMarkdownLite(message.content)}</div>
                  )}
                  {message.citations && message.citations.length > 0 && (
                    <ul className="mt-2 flex flex-col gap-0.5 border-t border-slate-200 pt-2 text-xs text-slate-500">
                      {message.citations.map((citation, citationIndex) => (
                        <li key={citation.chunk_id}>
                          <details>
                            <summary className="cursor-pointer">
                              [{citationIndex + 1}] {formatCitation(citation)}
                            </summary>
                            <p className="mt-0.5 pl-3 text-slate-400">
                              Relevance score: {citation.score.toFixed(3)}
                              {citation.reranked ? " (reranked)" : " (retrieval fusion score)"}
                            </p>
                          </details>
                        </li>
                      ))}
                    </ul>
                  )}
                  {message.role === "assistant" && message.id && !isPendingAssistant && (
                    <div className="mt-2 flex items-center gap-1 border-t border-slate-200 pt-2">
                      <button
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          message.feedback === "up"
                            ? "bg-emerald-100 text-emerald-700"
                            : "text-slate-400 hover:bg-slate-200"
                        }`}
                        title="Good answer"
                        onClick={() => void setMessageFeedback(message.id!, "up")}
                      >
                        👍
                      </button>
                      <button
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          message.feedback === "down"
                            ? "bg-red-100 text-red-700"
                            : "text-slate-400 hover:bg-slate-200"
                        }`}
                        title="Bad answer"
                        onClick={() => void setMessageFeedback(message.id!, "down")}
                      >
                        👎
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={bottomRef} />
          </div>
        </div>
        <div className="border-t border-slate-200 p-4">
          <div className="mx-auto flex max-w-2xl gap-2">
            <Input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder="Ask a question..."
              disabled={isStreaming}
            />
            <Button onClick={() => void sendMessage()} disabled={isStreaming}>
              {isStreaming ? "Sending..." : "Send"}
            </Button>
          </div>
        </div>
      </main>
    </div>
  );
}
