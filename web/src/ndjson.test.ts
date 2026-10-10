import { describe, expect, it } from "vitest";

import { readNdjson, splitLines } from "./ndjson";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("ndjson", () => {
  it("keeps the unfinished tail for the next chunk", () => {
    expect(splitLines('{"a":1}\n\n{"b":')).toEqual({ lines: ['{"a":1}'], rest: '{"b":' });
    expect(splitLines("")).toEqual({ lines: [], rest: "" });
  });

  it("delivers objects split across chunks, including a final line without a newline", async () => {
    const items: unknown[] = [];
    await readNdjson(streamOf('{"type":"ev', 'ent","index":0}\n{"type":', '"done"}'), (item) => items.push(item));
    expect(items).toEqual([{ type: "event", index: 0 }, { type: "done" }]);
  });

  it("decodes multi-byte characters split between chunks", async () => {
    const bytes = new TextEncoder().encode('{"t":"é"}\n');
    const items: unknown[] = [];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 7));
        controller.enqueue(bytes.slice(7));
        controller.close();
      },
    });
    await readNdjson(stream, (item) => items.push(item));
    expect(items).toEqual([{ t: "é" }]);
  });
});
