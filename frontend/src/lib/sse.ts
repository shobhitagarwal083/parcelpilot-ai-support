/* A server-sent-events reader over fetch.
 *
 * `EventSource` cannot be used here: it only issues GET requests, and the chat
 * endpoint is a POST carrying the message and recent history. So the stream is
 * read by hand.
 *
 * The subtlety worth isolating in its own file: a chunk boundary lands wherever
 * the network puts it, not on a frame boundary. A reader that parses each chunk
 * independently silently drops any event split across two of them -- which,
 * under load, is exactly the tool_call events the interface exists to show.
 * Everything here is buffer-until-terminator for that reason.
 */

export interface ServerEvent {
  kind: string;
  data: Record<string, unknown>;
}

export async function* readEventStream(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<ServerEvent> {
  if (!response.body) throw new Error("the response carried no body to stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Frames are terminated by a blank line. Anything after the last one is
      // an incomplete frame and stays in the buffer for the next chunk.
      let split = buffer.indexOf("\n\n");
      while (split !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const event = parseFrame(frame);
        if (event) yield event;
        split = buffer.indexOf("\n\n");
      }
    }

    const trailing = parseFrame(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): ServerEvent | null {
  let kind = "message";
  const dataLines: string[] = [];

  for (const raw of frame.split("\n")) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) kind = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }

  if (!dataLines.length) return null;

  try {
    return { kind, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    // A frame we cannot parse is worth surfacing rather than swallowing: it
    // means the client and server disagree about the protocol.
    return { kind: "error", data: { message: "unreadable event from the server" } };
  }
}
