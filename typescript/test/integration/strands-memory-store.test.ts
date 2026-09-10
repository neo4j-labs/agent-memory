/**
 * Integration — Neo4jMemoryStore over the real RestTransport against an
 * MSW-mocked /v1 server. Covers the wire shapes the unit fakes cannot:
 * the {entities: […]} envelope, camelCased request bodies, and the
 * NotSupportedError that REST raises for add_preference.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryClient } from "../../src/client.js";
import { AuthenticationError } from "../../src/errors.js";
import { Neo4jMemoryStore } from "../../src/integrations/strands/index.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function newStore(options: Record<string, unknown> = {}) {
  const client = new MemoryClient({ endpoint: ENDPOINT, apiKey: API_KEY });
  return new Neo4jMemoryStore({ name: "graph", client, ...options } as never);
}

describe("Neo4jMemoryStore over REST", () => {
  it("unwraps the entity-search envelope into memory entries", async () => {
    server.use(
      // initialize() connects first, which health-checks via GET /conversations.
      http.get(`${ENDPOINT}/conversations`, () => HttpResponse.json({ conversations: [] })),
      http.post(`${ENDPOINT}/entities/search`, async ({ request }) => {
        const body = (await request.json()) as { query: string; limit: number };
        expect(body.query).toBe("acme");
        expect(body.limit).toBe(3);
        return HttpResponse.json({
          entities: [
            {
              id: "e1",
              name: "Acme Corp",
              type: "ORGANIZATION",
              description: "A manufacturer",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        });
      }),
    );

    const entries = await newStore().search("acme");
    expect(entries).toHaveLength(1);
    expect(entries[0]!.content).toBe("[entity] Acme Corp (ORGANIZATION) — A manufacturer");
    expect(entries[0]!.metadata).toEqual({ kind: "entity", id: "e1", type: "ORGANIZATION" });
  });

  it("creates the sink once and bulk-writes turns into it", async () => {
    const created: unknown[] = [];
    const bulk: unknown[] = [];
    let conversations: unknown[] = [];

    server.use(
      http.get(`${ENDPOINT}/conversations`, () =>
        HttpResponse.json({ conversations }),
      ),
      http.post(`${ENDPOINT}/conversations`, async ({ request }) => {
        const body = (await request.json()) as { metadata?: Record<string, unknown> };
        created.push(body);
        const conversation = { id: "conv-1", metadata: body.metadata };
        conversations = [conversation];
        return HttpResponse.json(conversation);
      }),
      http.post(`${ENDPOINT}/conversations/conv-1/messages/bulk`, async ({ request }) => {
        const body = (await request.json()) as { messages: unknown[] };
        bulk.push(body);
        return HttpResponse.json({ messages: body.messages.map((_, i) => ({ id: `m${i}` })) });
      }),
    );

    const store = newStore({ userId: "alice" });
    const batch = [
      { role: "user", content: [{ text: "I prefer dark mode" }] },
      { role: "assistant", content: [{ text: "Noted" }] },
    ] as never;

    expect(await store.addMessages(batch, { sequenceNumbers: [0, 1] })).toEqual({
      written: 2,
      skipped: 0,
    });
    expect(await store.addMessages(batch, { sequenceNumbers: [2, 3] })).toEqual({
      written: 2,
      skipped: 0,
    });

    expect(created).toHaveLength(1);
    // The transport deep-camelCases request bodies (casing.ts's snakeToCamel
    // recurses into nested objects — see test/unit/casing.test.ts's "metadata"
    // round-trip case) so the store's snake_case metadata key crosses the wire
    // camelCased. The store reads it back through the inverse camelToSnake, so
    // resolveSink()'s STORE_METADATA_KEY lookup still matches.
    expect(created[0]).toMatchObject({
      userId: "alice",
      metadata: { strandsMemoryStore: "strands-memory-store/alice/graph" },
    });
    expect(bulk).toHaveLength(2);
  });

  it("falls back to the sink when REST rejects a preference write", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const messages: unknown[] = [];
    server.use(
      http.get(`${ENDPOINT}/conversations`, () => HttpResponse.json({ conversations: [] })),
      http.post(`${ENDPOINT}/conversations`, () => HttpResponse.json({ id: "conv-1" })),
      http.post(`${ENDPOINT}/conversations/conv-1/messages`, async ({ request }) => {
        messages.push(await request.json());
        return HttpResponse.json({ id: "m1", role: "user", content: "dark mode" });
      }),
    );

    try {
      // add_preference is mapped to "unsupported" in RestTransport, so the
      // transport raises NotSupportedError without issuing a request.
      const result = await newStore().add("dark mode", { kind: "preference", category: "ui" });
      expect(result).toMatchObject({ kind: "message" });
      expect(messages).toHaveLength(1);
      expect(warn).toHaveBeenCalledOnce();
    } finally {
      warn.mockRestore();
    }
  });

  it("a second store instance finds the existing sink instead of creating another", async () => {
    const created: unknown[] = [];
    let conversations: Array<Record<string, unknown>> = [];

    server.use(
      http.get(`${ENDPOINT}/conversations`, () => HttpResponse.json({ conversations })),
      http.post(`${ENDPOINT}/conversations`, async ({ request }) => {
        const body = (await request.json()) as { metadata?: Record<string, unknown> };
        created.push(body);
        const conversation = { id: "conv-1", metadata: body.metadata };
        conversations = [conversation];
        return HttpResponse.json(conversation);
      }),
      http.post(`${ENDPOINT}/conversations/conv-1/messages/bulk`, async ({ request }) => {
        const body = (await request.json()) as { messages: unknown[] };
        return HttpResponse.json({ messages: body.messages.map((_, i) => ({ id: `m${i}` })) });
      }),
    );

    const batch = [{ role: "user", content: [{ text: "ok" }] }] as never;
    await newStore().addMessages(batch);
    // A fresh instance holds no cached sink id, so it has to find the first
    // store's sink by its metadata. That only works because the transport's
    // request-side snake->camel and response-side camel->snake conversions are
    // symmetric over nested metadata keys: the key goes out as
    // `strandsMemoryStore` and comes back as `strands_memory_store`. The unit
    // fakes echo metadata verbatim and cannot catch an asymmetry here.
    await newStore().addMessages(batch);

    expect(created).toHaveLength(1);
  });

  it("propagates an auth failure out of search", async () => {
    server.use(
      // initialize() connects first, which health-checks via GET /conversations.
      http.get(`${ENDPOINT}/conversations`, () => HttpResponse.json({ conversations: [] })),
      http.post(`${ENDPOINT}/entities/search`, () =>
        HttpResponse.json({ detail: "invalid key" }, { status: 401 }),
      ),
    );
    await expect(newStore().search("acme")).rejects.toThrow(AuthenticationError);
  });
});
