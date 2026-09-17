import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { getConversationsStore, type RecentConversation } from "../lib/conversationsStore";
import { getDocumentsStore, type RecentDocument } from "../lib/documentsStore";
import { parseSseStream } from "../lib/sseStream";
import type { Citation } from "../lib/types";

interface ChatMessage {
  role: "user" | "assistant" | "error";
  content: string;
  citations?: Citation[];
}

function newConversationId(): string {
  return crypto.randomUUID();
}

/** "document.pdf, p. 3" or "document.pdf, p. 3-4" when the citation spans multiple pages. */
function formatCitation(citation: Citation): string {
  const pages =
    citation.page_start === citation.page_end
      ? `p. ${citation.page_start}`
      : `p. ${citation.page_start}-${citation.page_end}`;
  return `${citation.source_filename}, ${pages}`;
}

const DOCUMENT_STATUS_DOT: Record<RecentDocument["status"], string> = {
  pending: "bg-amber-400",
  processing: "bg-amber-400",
  done: "bg-emerald-500",
  failed: "bg-red-500",
};

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
  const { user } = useAuth();
  const [conversationId, setConversationId] = useState<string>(() => newConversationId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [recentConversations, setRecentConversations] = useState<RecentConversation[]>(() =>
    user ? getConversationsStore(user.id).list() : []
  );
  const [recentDocuments] = useState<RecentDocument[]>(() =>
    user ? getDocumentsStore(user.id).list() : []
  );

  if (!user) {
    return <p className="p-6 text-sm text-slate-400">Loading...</p>;
  }

  function startNewConversation(): void {
    setConversationId(newConversationId());
    setMessages([]);
  }

  async function sendMessage(): Promise<void> {
    const query = input.trim();
    if (!query || isStreaming || !user) return;

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
        } else if (sseEvent.event === "error") {
          setMessages((prev) => [
            ...prev.slice(0, -1),
            { role: "assistant", content: assistantText, citations },
            { role: "error", content: (sseEvent.data as { detail: string }).detail },
          ]);
        }
      }

      if (isFirstMessage) {
        const store = getConversationsStore(user.id);
        store.upsert({ id: conversationId, title: query.slice(0, 60), lastUpdated: Date.now() });
        setRecentConversations(store.list());
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
              <li key={conv.id}>
                <button
                  className={`w-full truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                    conv.id === conversationId ? "bg-slate-200 font-medium" : "text-slate-700"
                  }`}
                  onClick={() => setConversationId(conv.id)}
                >
                  {conv.title}
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Your documents
        </p>
        {recentDocuments.length === 0 ? (
          <p className="px-1 text-sm text-slate-400">
            No documents uploaded yet — visit Documents to add one.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {recentDocuments.map((doc) => (
              <li key={doc.id} className="flex items-center gap-2 px-2 py-1 text-sm text-slate-600">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOCUMENT_STATUS_DOT[doc.status]}`}
                />
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
                    <p className="whitespace-pre-wrap text-sm">{message.content}</p>
                  )}
                  {message.citations && message.citations.length > 0 && (
                    <ul className="mt-2 flex flex-col gap-0.5 border-t border-slate-200 pt-2 text-xs text-slate-500">
                      {message.citations.map((citation, citationIndex) => (
                        <li key={citation.chunk_id}>
                          [{citationIndex + 1}] {formatCitation(citation)}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
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
