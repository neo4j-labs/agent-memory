import { afterEach, describe, expect, it, vi } from "vitest";

import { memory, streamChat } from "@/lib/api";

const BASE = "http://localhost:8000/api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("memory.getContext", () => {
  it("scopes the request to a thread and returns the parsed context", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        jsonResponse({
          preferences: [],
          entities: [{ id: "e1", name: "Airbnb", type: "ORGANIZATION" }],
          recent_topics: ["growth"],
          recent_messages: [],
        }),
      );

    const context = await memory.getContext({ threadId: "thread-1" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/memory/context?thread_id=thread-1`,
    );
    expect(context.entities[0].name).toBe("Airbnb");
    expect(context.recent_topics).toEqual(["growth"]);
  });

  it("omits the query string when no thread is supplied", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        preferences: [],
        entities: [],
        recent_topics: [],
        recent_messages: [],
      }),
    );

    await memory.getContext();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/memory/context`);
  });

  it("reports a caller cancellation as an abort, not a timeout", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      });
    });

    const controller = new AbortController();
    const pending = memory.getContext({
      threadId: "thread-1",
      signal: controller.signal,
    });
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("streamChat", () => {
  it("forwards the abort signal so the backend generator stops", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'data: {"type":"token","content":"hi"}\n\n',
              ),
            );
            controller.close();
          },
        }),
        { status: 200 },
      ),
    );

    const controller = new AbortController();
    const events = [];
    for await (const event of streamChat(
      "thread-1",
      "hello",
      true,
      controller.signal,
    )) {
      events.push(event);
    }

    expect(events).toEqual([{ type: "token", content: "hi" }]);
    const init = fetchMock.mock.calls[0][1];
    expect(init?.signal).toBe(controller.signal);
  });
});
