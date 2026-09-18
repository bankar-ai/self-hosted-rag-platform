export interface SseEvent {
  event: string;
  data: unknown;
}

/**
 * Parse a `text/event-stream` response body into `{event, data}` records, matching the
 * backend's `_format_sse` shape (`app/generation/router.py`): `event: <name>\ndata: <json>\n\n`.
 * Handles events split across chunk boundaries by buffering until a full `\n\n`-terminated
 * record is available.
 */
export async function* parseSseStream(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const record = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);

      const eventLine = record.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = record.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      yield {
        event: eventLine.slice("event: ".length),
        data: JSON.parse(dataLine.slice("data: ".length)),
      };
    }
  }
}
