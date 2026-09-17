import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "../lib/apiClient";
import { conversationsStore, type RecentConversation } from "../lib/conversationsStore";
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

export default function ChatPage() {
  const [conversationId, setConversationId] = useState<string>(() => newConversationId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [recentConversations, setRecentConversations] = useState<RecentConversation[]>(() =>
    conversationsStore.list()
  );

  function startNewConversation(): void {
    setConversationId(newConversationId());
    setMessages([]);
  }

  async function sendMessage(): Promise<void> {
    const query = input.trim();
    if (!query || isStreaming) return;

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
        conversationsStore.upsert({
          id: conversationId,
          title: query.slice(0, 60),
          lastUpdated: Date.now(),
        });
        setRecentConversations(conversationsStore.list());
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
      <aside className="flex w-64 flex-col border-r border-slate-200 bg-slate-50 p-4">
        <Button className="mb-4 w-full" onClick={startNewConversation}>
          New chat
        </Button>
        <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Recent conversations
        </p>
        {recentConversations.length === 0 ? (
          <p className="px-1 text-sm text-slate-400">No conversations yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
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
      </aside>
      <main className="flex flex-1 flex-col bg-white">
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 && (
            <p className="mt-12 text-center text-sm text-slate-400">
              Ask a question about one of your uploaded documents to get started.
            </p>
          )}
          <div className="mx-auto flex max-w-2xl flex-col gap-3">
            {messages.map((message, index) => (
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
                <p className="whitespace-pre-wrap text-sm">{message.content}</p>
                {message.citations && message.citations.length > 0 && (
                  <p className="mt-1 text-xs text-slate-500">
                    Sources: {message.citations.map((c) => c.source_filename).join(", ")}
                  </p>
                )}
              </div>
            ))}
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
