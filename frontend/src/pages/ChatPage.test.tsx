import { render, screen } from "@testing-library/react";
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

function stubChatFetch(streamResponse: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ id: "u1", email: "u@example.com", role: "user", is_active: true }),
            { status: 200 }
          )
        );
      }
      if (url.endsWith("/conversations")) {
        return Promise.resolve(new Response(JSON.stringify({ conversations: [] }), { status: 200 }));
      }
      if (url.endsWith("/documents")) {
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
});
