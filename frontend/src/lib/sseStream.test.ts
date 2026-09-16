import { describe, expect, it } from "vitest";
import { parseSseStream } from "./sseStream";

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream);
}

describe("parseSseStream", () => {
  it("parses citations, token, and done events in order", async () => {
    const response = responseFromChunks([
      'event: citations\ndata: {"citations": []}\n\n',
      'event: token\ndata: {"text": "Hello"}\n\n',
      'event: token\ndata: {"text": " world"}\n\n',
      'event: done\ndata: {"conversation_id": "abc-123"}\n\n',
    ]);

    const events = [];
    for await (const event of parseSseStream(response)) {
      events.push(event);
    }

    expect(events).toEqual([
      { event: "citations", data: { citations: [] } },
      { event: "token", data: { text: "Hello" } },
      { event: "token", data: { text: " world" } },
      { event: "done", data: { conversation_id: "abc-123" } },
    ]);
  });

  it("handles an event split across two chunks", async () => {
    const response = responseFromChunks([
      'event: token\ndata: {"te',
      'xt": "split"}\n\n',
    ]);

    const events = [];
    for await (const event of parseSseStream(response)) {
      events.push(event);
    }

    expect(events).toEqual([{ event: "token", data: { text: "split" } }]);
  });

  it("parses a terminal error event", async () => {
    const response = responseFromChunks(['event: error\ndata: {"detail": "boom"}\n\n']);

    const events = [];
    for await (const event of parseSseStream(response)) {
      events.push(event);
    }

    expect(events).toEqual([{ event: "error", data: { detail: "boom" } }]);
  });
});
