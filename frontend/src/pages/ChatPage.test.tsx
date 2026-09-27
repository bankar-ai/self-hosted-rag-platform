import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import ChatPage from "./ChatPage";

// See DocumentsPage.test.tsx for why a decodable fake JWT is needed (ChatPage reads `userId`
// from the token's `sub` claim).
function fakeToken(payload: Record<string, unknown>): string {
  const base64url = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${base64url({ alg: "HS256" })}.${base64url(payload)}.fake-signature`;
}

function sseResponse(chunks: string[], delayMs = 0): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const emit = () => {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      };
      if (delayMs > 0) {
        setTimeout(emit, delayMs);
      } else {
        emit();
      }
    },
  });
  return new Response(stream);
}

function stubChatFetch(
  streamResponse: () => Response,
  extra?: (url: string) => Response | undefined
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const extraResponse = extra?.(url);
      if (extraResponse) return Promise.resolve(extraResponse);
      if (url.includes("/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ id: "u1", email: "u@example.com", role: "user", is_active: true }),
            { status: 200 }
          )
        );
      }
      if ((url.includes("/conversations?") || url.endsWith("/conversations"))) {
        return Promise.resolve(new Response(JSON.stringify({ conversations: [] }), { status: 200 }));
      }
      if ((url.includes("/documents?") || url.endsWith("/documents"))) {
        return Promise.resolve(new Response(JSON.stringify({ documents: [] }), { status: 200 }));
      }
      if (url.endsWith("/generation/warmup")) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (url.endsWith("/generation/query/stream")) {
        return Promise.resolve(streamResponse());
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    })
  );
}

describe("ChatPage", () => {
  beforeEach(() => {
    localStorage.clear();
    setTokens({ accessToken: fakeToken({ sub: "u1", role: "user" }), refreshToken: "b" });
    // jsdom doesn't implement scrollIntoView (used by ChatPage's ERP-064 auto-scroll effect).
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fires a warmup ping on mount to pre-warm the LLM backend (ERP-091)", async () => {
    stubChatFetch(() => sseResponse(['event: done\ndata: {}\n\n']));

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/generation/warmup"),
        expect.objectContaining({ method: "POST" })
      )
    );
  });

  it("shows a Load more button when has_more is true and appends the next page on click (ERP-103)", async () => {
    stubChatFetch(
      () => sseResponse(["event: done\ndata: {}\n\n"]),
      (url) => {
        if (url.includes("/conversations?") && url.includes("offset=0")) {
          return new Response(
            JSON.stringify({
              conversations: [{ conversation_id: "c1", title: "First chat", preview: null }],
              has_more: true,
            }),
            { status: 200 }
          );
        }
        if (url.includes("/conversations?") && url.includes("offset=1")) {
          return new Response(
            JSON.stringify({
              conversations: [{ conversation_id: "c2", title: "Second chat", preview: null }],
              has_more: false,
            }),
            { status: 200 }
          );
        }
        return undefined;
      }
    );

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    await screen.findByText("First chat");
    const loadMoreButton = await screen.findByRole("button", { name: /load more/i });

    await userEvent.click(loadMoreButton);

    await screen.findByText("Second chat");
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it(
    "shows a cold-start hint once an answer has been pending for a while with no tokens yet",
    async () => {
      stubChatFetch(() =>
        sseResponse(['event: token\ndata: {"text": "hi"}\n\n', "event: done\ndata: {}\n\n"], 5500)
      );

      render(
        <AuthProvider>
          <ChatPage />
        </AuthProvider>
      );

      const input = await screen.findByPlaceholderText(/ask a question/i);
      await userEvent.type(input, "What does the doc say?");
      await userEvent.click(screen.getByRole("button", { name: /send/i }));

      expect(screen.queryByText(/waking up the model/i)).not.toBeInTheDocument();

      expect(
        await screen.findByText(/waking up the model/i, {}, { timeout: 7000 })
      ).toBeInTheDocument();
    },
    10000
  );

  it("clears the cold-start hint once the first token arrives", async () => {
    stubChatFetch(() =>
      sseResponse(['event: token\ndata: {"text": "hi"}\n\n', "event: done\ndata: {}\n\n"])
    );

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "What does the doc say?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    await screen.findByText("hi");
    expect(screen.queryByText(/waking up the model/i)).not.toBeInTheDocument();
  });

  it("question's copy button copies only the question; answer's copies the Q&A pair (ERP-100)", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    stubChatFetch(() => sseResponse(['event: token\ndata: {"text": "hi"}\n\n', "event: done\ndata: {}\n\n"]));

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "What does the doc say?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await screen.findByText("hi");

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenLastCalledWith("What does the doc say?");

    await userEvent.click(screen.getByRole("button", { name: "Copy Q&A" }));
    expect(writeText).toHaveBeenLastCalledWith(
      expect.stringContaining("What does the doc say?")
    );
    expect(writeText).toHaveBeenLastCalledWith(expect.stringContaining("hi"));
  });

  it("select-all/deselect-all toggles every document's checkbox at once (ERP-101)", async () => {
    stubChatFetch(
      () => sseResponse(["event: done\ndata: {}\n\n"]),
      (url) => {
        if ((url.includes("/documents?") || url.endsWith("/documents"))) {
          return new Response(
            JSON.stringify({
              documents: [
                { document_id: "d1", filename: "one.pdf", parsing_confidence: "high" },
                { document_id: "d2", filename: "two.pdf", parsing_confidence: "high" },
              ],
            }),
            { status: 200 }
          );
        }
        return undefined;
      }
    );

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    const box1 = await screen.findByRole("checkbox", { name: /one\.pdf/i });
    const box2 = screen.getByRole("checkbox", { name: /two\.pdf/i });
    expect(box1).toBeChecked();
    expect(box2).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Deselect all" }));
    expect(box1).not.toBeChecked();
    expect(box2).not.toBeChecked();
    expect(screen.getByText(/no documents selected/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(box1).toBeChecked();
    expect(box2).toBeChecked();
    expect(screen.queryByText(/no documents selected/i)).not.toBeInTheDocument();
  });

  it("shows a jump-to-latest control once scrolled away from the bottom, and it scrolls back (ERP-102)", async () => {
    stubChatFetch(() => sseResponse(["event: done\ndata: {}\n\n"]));

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );
    await screen.findByPlaceholderText(/ask a question/i);

    expect(screen.queryByRole("button", { name: /jump to latest/i })).not.toBeInTheDocument();

    const container = document.querySelector(".h-full.overflow-y-auto") as HTMLDivElement;
    Object.defineProperty(container, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(container, "clientHeight", { value: 300, configurable: true });
    Object.defineProperty(container, "scrollTop", { value: 0, configurable: true, writable: true });
    fireEvent.scroll(container);

    const jumpButton = await screen.findByRole("button", { name: /jump to latest/i });

    (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();
    await userEvent.click(jumpButton);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("footer citation list displays the citation's own marker, not its array position (ERP-098)", async () => {
    // Only chunk 3 (of an original 3) was cited, so the citations event carries a single
    // element whose `marker` is 3 -- the footer must show "[3]", matching the inline marker
    // in the answer text, not "[1]" (its position in this 1-element array).
    stubChatFetch(() =>
      sseResponse([
        'event: token\ndata: {"text": "answer [3]"}\n\n',
        'event: citations\ndata: {"citations": [{"marker": 3, "chunk_id": "c3", "document_id": "d1", "section_path": [], "page_start": 1, "page_end": 1, "source_filename": "doc.pdf", "score": 0.9, "reranked": false}]}\n\n',
        "event: done\ndata: {}\n\n",
      ])
    );

    render(
      <AuthProvider>
        <ChatPage />
      </AuthProvider>
    );

    const input = await screen.findByPlaceholderText(/ask a question/i);
    await userEvent.type(input, "What does the doc say?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    // The footer entry's text is "[n] doc.pdf, p. 1" -- distinct from the inline marker
    // button (whose text is just "[3]" with no filename), so this specifically targets the
    // footer citation list's own numbering, not the inline marker already covered above.
    expect(await screen.findByText("[3] doc.pdf, p. 1")).toBeInTheDocument();
    expect(screen.queryByText("[1] doc.pdf, p. 1")).not.toBeInTheDocument();
  });

  describe("ERP-096: recovering an answer this browser never saw arrive live", () => {
    const CONVERSATION_ID = "c1";

    function stubRestoredConversation(historyResponses: object[]) {
      let call = 0;
      stubChatFetch(
        () => sseResponse(["event: done\ndata: {}\n\n"]),
        (url) => {
          if (url.includes(`/conversations/${CONVERSATION_ID}`)) {
            const body = historyResponses[Math.min(call, historyResponses.length - 1)];
            call += 1;
            return new Response(JSON.stringify(body), { status: 200 });
          }
          return undefined;
        }
      );
    }

    beforeEach(() => {
      localStorage.setItem("rag-active-conversation:u1", CONVERSATION_ID);
      localStorage.setItem(`rag-pending-answer:${CONVERSATION_ID}`, String(Date.now()));
    });

    it("polls and recovers the answer once it lands, instead of leaving the question looking unanswered", async () => {
      const unanswered = { messages: [{ id: "m1", role: "user", content: "question one", feedback: null, citations: [] }] };
      const answered = {
        messages: [
          { id: "m1", role: "user", content: "question one", feedback: null, citations: [] },
          { id: "m2", role: "assistant", content: "the answer", feedback: null, citations: [] },
        ],
      };
      stubRestoredConversation([unanswered, answered]);

      render(
        <AuthProvider>
          <ChatPage />
        </AuthProvider>
      );

      await screen.findByText("question one");
      expect(screen.queryByText("the answer")).not.toBeInTheDocument();

      expect(await screen.findByText("the answer", {}, { timeout: 5000 })).toBeInTheDocument();
      expect(localStorage.getItem(`rag-pending-answer:${CONVERSATION_ID}`)).toBeNull();
    });

    it("gives up after timing out rather than polling forever, and clears the marker", async () => {
      vi.useFakeTimers();
      const unanswered = { messages: [{ id: "m1", role: "user", content: "question one", feedback: null, citations: [] }] };
      stubRestoredConversation([unanswered]);

      render(
        <AuthProvider>
          <ChatPage />
        </AuthProvider>
      );

      await vi.waitFor(() => expect(screen.getByText("question one")).toBeInTheDocument());
      await vi.advanceTimersByTimeAsync(122_000);

      expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument();
      expect(localStorage.getItem(`rag-pending-answer:${CONVERSATION_ID}`)).toBeNull();
      vi.useRealTimers();
    });
  });
});
