/** Newline-delimited JSON over a fetch body: one object per line, delivered as it arrives. */

/** Split a buffer into complete lines and the unfinished tail. Blank lines are dropped. */
export function splitLines(buffer: string): { lines: string[]; rest: string } {
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  return { lines: parts.filter((line) => line.trim() !== ""), rest };
}

export async function readNdjson<T>(stream: ReadableStream<Uint8Array>, onItem: (item: T) => void): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { lines, rest } = splitLines(buffer);
    buffer = rest;
    for (const line of lines) onItem(JSON.parse(line) as T);
  }
  buffer += decoder.decode();
  if (buffer.trim() !== "") onItem(JSON.parse(buffer) as T);
}
