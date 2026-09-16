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

    const response = await apiFetch("/generation/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, conversation_id: conversationId }),
    });

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

    setIsStreaming(false);
  }

  return (
    <div className="flex h-screen">
      <aside className="w-64 border-r p-4">
        <Button className="mb-4 w-full" onClick={startNewConversation}>
          New chat
        </Button>
        <ul className="flex flex-col gap-2">
          {recentConversations.map((conv) => (
            <li key={conv.id}>
              <button
                className="w-full truncate rounded p-2 text-left text-sm hover:bg-gray-100"
                onClick={() => setConversationId(conv.id)}
              >
                {conv.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="flex flex-1 flex-col p-4">
        <div className="flex-1 space-y-4 overflow-y-auto">
          {messages.map((message, index) => (
            <div key={index} data-role={message.role}>
              <p className={message.role === "error" ? "text-red-600" : ""}>{message.content}</p>
              {message.citations && message.citations.length > 0 && (
                <p className="text-xs text-gray-500">
                  Sources: {message.citations.map((c) => c.source_filename).join(", ")}
                </p>
              )}
            </div>
          ))}
        </div>
        <div className="mt-4 flex gap-2">
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
            Send
          </Button>
        </div>
      </main>
    </div>
  );
}
