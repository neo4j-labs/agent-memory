/**
 * Integration tests — RestTransport against an in-process MSW mock server.
 *
 * Validates that bridge-style method calls hit the right REST endpoints,
 * send camelCase bodies, attach Bearer auth, and parse responses back into
 * the canonical snake_case-shaped types.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryClient } from "../../src/client.js";
import { AuthenticationError, ConnectionError, NotSupportedError, TransportError, ValidationError } from "../../src/errors.js";

const ENDPOINT = "https://memory.test/v1";
const API_KEY = "nams_test_key";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function newClient(opts?: Partial<{ apiKey: string }>): MemoryClient {
  return new MemoryClient({ endpoint: ENDPOINT, apiKey: opts?.apiKey ?? API_KEY });
}

describe("RestTransport — short-term", () => {
  it("createConversation POSTs camelCase body and parses response", async () => {
    let observedAuth: string | null = null;
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/conversations`, async ({ request }) => {
        observedAuth = request.headers.get("authorization");
        observedBody = await request.json();
        return HttpResponse.json({
          id: "conv-1",
          userId: "alice",
          workspaceId: "ws",
          createdAt: "2026-05-07T00:00:00Z",
        });
      }),
    );

    const client = newClient();
    const conv = await client.shortTerm.createConversation({
      userId: "alice",
      metadata: { source: "test" },
    });
    await client.close();

    expect(observedAuth).toBe(`Bearer ${API_KEY}`);
    expect(observedBody).toMatchObject({ userId: "alice", metadata: { source: "test" } });
    expect(conv.id).toBe("conv-1");
    expect(conv.userId).toBe("alice");
    expect(conv.workspaceId).toBe("ws");
  });

  it("getContext substitutes path param and returns 3-tier context", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations/conv-42/context`, () =>
        HttpResponse.json({
          reflections: [
            { id: "r1", conversationId: "conv-42", content: "user values clarity", createdAt: "x" },
          ],
          observations: [
            { id: "o1", conversationId: "conv-42", content: "long-form messages", createdAt: "x" },
          ],
          recentMessages: [{ id: "m1", role: "user", content: "hello" }],
        }),
      ),
    );

    const client = newClient();
    const ctx = await client.shortTerm.getContext("conv-42");
    await client.close();

    expect(ctx.reflections).toHaveLength(1);
    expect(ctx.observations).toHaveLength(1);
    expect(ctx.recentMessages).toHaveLength(1);
    expect(ctx.reflections[0]!.conversationId).toBe("conv-42");
  });

  it("listConversations unwraps the {conversations: [...]} envelope", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations`, () =>
        HttpResponse.json({
          conversations: [
            { id: "c1", userId: "alice", createdAt: "x" },
            { id: "c2", userId: "bob", createdAt: "y" },
          ],
        }),
      ),
    );

    const client = newClient();
    const convs = await client.shortTerm.listConversations({ limit: 10 });
    await client.close();

    expect(convs).toHaveLength(2);
    expect(convs[0]!.id).toBe("c1");
    expect(convs[1]!.userId).toBe("bob");
  });

  it("listConversations forwards the optional userId filter as userId", async () => {
    // The service's filter param is `userId` (OpenAPI spec); `user_id` is ignored.
    let observed: URLSearchParams | null = null;
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) => {
        observed = new URL(request.url).searchParams;
        return HttpResponse.json({ conversations: [] });
      }),
    );

    const client = newClient();
    await client.shortTerm.listConversations({ limit: 10, userId: "alice" });
    await client.close();

    expect(observed!.get("userId")).toBe("alice");
    expect(observed!.get("user_id")).toBeNull();
    expect(observed!.get("limit")).toBe("10");
  });

  const conversations = (start: number, count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: `c${start + i}`,
      userId: "alice",
      messageCount: start + i,
      createdAt: "t",
      updatedAt: "t",
    }));

  it("listConversations follows next_cursor past the 200-per-page cap", async () => {
    // A limit above 200 is a 400 invalid_limit; the Strands memory store scans 1000.
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push(params);
        return params.get("cursor") === "c1"
          ? HttpResponse.json({ conversations: conversations(200, 30), next_cursor: "" })
          : HttpResponse.json({ conversations: conversations(0, 200), next_cursor: "c1" });
      }),
    );

    const client = newClient();
    const convs = await client.shortTerm.listConversations({ limit: 1000, userId: "alice" });
    await client.close();

    expect(convs).toHaveLength(230);
    expect(convs[229]!.id).toBe("c229");
    expect(seen.map((p) => [p.get("limit"), p.get("cursor"), p.get("userId")])).toEqual([
      ["200", null, "alice"],
      ["200", "c1", "alice"],
    ]);
  });

  it("listConversations stops at the limit and defaults to 100", async () => {
    const limits: (string | null)[] = [];
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) => {
        limits.push(new URL(request.url).searchParams.get("limit"));
        return HttpResponse.json({ conversations: conversations(0, 100), next_cursor: "more" });
      }),
    );

    const client = newClient();
    const convs = await client.shortTerm.listConversations();
    await client.close();

    expect(convs).toHaveLength(100);
    expect(limits).toEqual(["100"]);
  });

  it("listSessions keeps each conversation's fields across pages", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) =>
        new URL(request.url).searchParams.get("cursor")
          ? HttpResponse.json({ conversations: conversations(200, 1), next_cursor: "" })
          : HttpResponse.json({ conversations: conversations(0, 200), next_cursor: "c1" }),
      ),
    );

    const client = newClient();
    const sessions = await client.shortTerm.listSessions({ limit: 300 });
    await client.close();

    expect(sessions).toHaveLength(201);
    expect(sessions[200]!.sessionId).toBe("c200");
    expect(sessions[200]!.messageCount).toBe(200);
  });

  it("getConversation returns messages oldest first and asks for the 200 cap", async () => {
    // The service lists newest first (default 50, max 200).
    let observedLimit: string | null = null;
    server.use(
      http.get(`${ENDPOINT}/conversations/conv-7/messages`, ({ request }) => {
        observedLimit = new URL(request.url).searchParams.get("limit");
        return HttpResponse.json({
          messages: [4, 3, 2, 1, 0].map((i) => ({
            id: `m${i}`,
            role: "user",
            content: `Message ${i}`,
            createdAt: "t",
          })),
        });
      }),
    );

    const client = newClient();
    const conv = await client.shortTerm.getConversation("conv-7");
    await client.close();

    expect(conv.messages.map((m) => m.content)).toEqual([0, 1, 2, 3, 4].map((i) => `Message ${i}`));
    expect(observedLimit).toBe("200");
  });

  it("getConversation clamps a limit above 200", async () => {
    let observedLimit: string | null = null;
    server.use(
      http.get(`${ENDPOINT}/conversations/conv-7/messages`, ({ request }) => {
        observedLimit = new URL(request.url).searchParams.get("limit");
        return HttpResponse.json({ messages: [] });
      }),
    );

    const client = newClient();
    await client.shortTerm.getConversation("conv-7", { limit: 1000 });
    await client.close();

    expect(observedLimit).toBe("200");
  });
});

describe("RestTransport — long-term", () => {
  it("searchEntities unwraps envelope and returns Entity[]", async () => {
    server.use(
      http.post(`${ENDPOINT}/entities/search`, () =>
        HttpResponse.json({
          entities: [
            { id: "e1", name: "Alice", type: "person", createdAt: "x" },
          ],
        }),
      ),
    );

    const client = newClient();
    const entities = await client.longTerm.searchEntities("alice");
    await client.close();

    expect(entities).toHaveLength(1);
    expect(entities[0]!.name).toBe("Alice");
  });

  it("searchEntities sends the userId filter and reads each entity's userId", async () => {
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/entities/search`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json({
          entities: [
            { id: "e1", name: "Alice", type: "person", createdAt: "x", userId: "u-1" },
            { id: "e2", name: "Acme", type: "organization", createdAt: "x", userId: "" },
          ],
        });
      }),
    );

    const client = newClient();
    const entities = await client.longTerm.searchEntities("alice", { userId: "u-1", limit: 5 });
    await client.close();

    expect(observedBody).toEqual({ query: "alice", limit: 5, userId: "u-1" });
    expect(entities[0]!.userId).toBe("u-1");
    expect(entities[1]!.userId).toBeUndefined();
  });

  it("addEntity sends userId with the fields hosted NAMS accepts", async () => {
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/entities`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json(
          { id: "e1", name: "Alice", type: "person", userId: "u-1", resolution: "created" },
          { status: 201 },
        );
      }),
    );

    const client = newClient();
    const entity = await client.longTerm.addEntity("Alice", "person", { description: "A user", userId: "u-1" });
    await client.close();

    expect(observedBody).toEqual({ name: "Alice", type: "person", description: "A user", userId: "u-1" });
    expect(entity.userId).toBe("u-1");
  });

  it("getEntity sends the userId filter as a query param", async () => {
    const searches: string[] = [];
    server.use(
      http.get(`${ENDPOINT}/entities/e1`, ({ request }) => {
        searches.push(new URL(request.url).search);
        return HttpResponse.json({ id: "e1", name: "Alice", type: "person", userId: "u-1", relationships: [] });
      }),
    );

    const client = newClient();
    await client.longTerm.getEntity("e1", { userId: "u-1" });
    await client.longTerm.getEntity("e1");
    await client.close();

    expect(searches).toEqual(["?userId=u-1", ""]);
  });

  it("expandGraph posts nodeId at the top level of the body", async () => {
    // Regression: the route lacked `snakeBody`, so `expandGraph`'s `body`
    // object was nested under a literal `body` key and the live service
    // answered "nodeId is required".
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/graph/expand`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json({ nodes: [], edges: [] });
      }),
    );

    const client = newClient();
    await client.longTerm.expandGraph("n1", ["n0"]);
    await client.close();

    expect(observedBody).toEqual({ nodeId: "n1", loadedIds: ["n0"] });
    expect(observedBody).not.toHaveProperty("body");
  });

  it("setEntityFeedback PUTs to /entities/{id}/feedback", async () => {
    let observedBody: unknown = null;
    server.use(
      http.put(`${ENDPOINT}/entities/e1/feedback`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json({ id: "e1", updated: true });
      }),
    );

    const client = newClient();
    const result = await client.longTerm.setEntityFeedback("e1", {
      userScore: 0.95,
      confirmed: true,
    });
    await client.close();

    expect(observedBody).toEqual({ userScore: 0.95, confirmed: true });
    expect(result).toEqual({ id: "e1", updated: true });
  });

  it("getEntityGraph returns nodes and edges", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/graph`, () =>
        HttpResponse.json({
          nodes: [{ id: "n1", name: "Alice", type: "person" }],
          edges: [{ id: "edge1", source: "n1", target: "n1", type: "SELF" }],
        }),
      ),
    );

    const client = newClient();
    const graph = await client.longTerm.getEntityGraph();
    await client.close();

    expect(graph.nodes).toHaveLength(1);
    expect(graph.edges).toHaveLength(1);
  });

  it("getEntity reads the hosted relationship shape", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/e1`, () =>
        HttpResponse.json({
          id: "e1", name: "Acme", type: "organization", createdAt: "x",
          relationships: [
            {
              id: "r1", relationshipType: "WORKS_AT", relType: "WORKS_AT", direction: "in",
              targetId: "e2", targetName: "Alice", targetType: "person", confidence: 0.9, predicate: "",
            },
            {
              id: "r2", relationshipType: "RELATED_TO", relType: "RELATED_TO", direction: "out",
              targetId: "e3", targetName: "Bob", targetType: "person", predicate: "MENTORS",
            },
          ],
        }),
      ),
    );

    const client = newClient();
    const entity = await client.longTerm.getEntity("e1");
    await client.close();

    expect(entity.relationships).toEqual([
      {
        id: "r1", type: "WORKS_AT", direction: "in", targetId: "e2", targetName: "Alice",
        targetType: "person", confidence: 0.9,
      },
      {
        id: "r2", type: "RELATED_TO", direction: "out", targetId: "e3", targetName: "Bob",
        targetType: "person", predicate: "MENTORS",
      },
    ]);
    expect(entity.relationships?.[0]).not.toHaveProperty("predicate", "");
  });

  it("getEntityByName sends name, type and userId and returns the best match", async () => {
    const searches: string[] = [];
    let matches: unknown[] = [
      { id: "e1", name: "Acme Corp", type: "organization", userId: "u-1", matchKind: "name", createdAt: "x" },
      { id: "e2", name: "ACME", type: "organization", userId: "u-1", matchKind: "alias", createdAt: "x" },
    ];
    server.use(
      http.get(`${ENDPOINT}/entities/by-name`, ({ request }) => {
        searches.push(new URL(request.url).search);
        return HttpResponse.json({ entities: matches });
      }),
    );

    const client = newClient();
    const entity = await client.longTerm.getEntityByName("Acme Corporation", {
      type: "organization", userId: "u-1",
    });
    matches = [];
    const missing = await client.longTerm.getEntityByName("Nobody");
    await client.close();

    expect(searches).toEqual([
      "?name=Acme%20Corporation&type=organization&userId=u-1",
      "?name=Nobody",
    ]);
    expect(entity).toMatchObject({ id: "e1", name: "Acme Corp", userId: "u-1" });
    expect(missing).toBeNull();
  });

  it("addRelationship posts the fields hosted NAMS accepts", async () => {
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/relationships`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json(
          {
            id: "r1", sourceId: "e1", targetId: "e2", relationshipType: "RELATED_TO",
            predicate: "MENTORS", confidence: 1, created: true,
          },
          { status: 201 },
        );
      }),
    );

    const client = newClient();
    const relationship = await client.longTerm.addRelationship("e1", "e2", "MENTORS", {
      properties: { since_year: 2024 },
      userId: "u-1",
    });
    await client.close();

    expect(observedBody).toEqual({
      sourceId: "e1", targetId: "e2", relationshipType: "MENTORS",
      properties: { since_year: 2024 }, userId: "u-1",
    });
    expect(relationship).toEqual({
      id: "r1", sourceId: "e1", targetId: "e2", relationshipType: "RELATED_TO",
      properties: {}, predicate: "MENTORS",
    });
  });
});

describe("RestTransport — message search and metadata", () => {
  it("searchMessages sends no threshold and applies one to the scores", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(`${ENDPOINT}/conversations/c1/search`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          messages: [
            { id: "m1", role: "user", content: "close", createdAt: "x", score: 0.683 },
            { id: "m2", role: "user", content: "far", createdAt: "x", score: 0.2 },
            { id: "m3", role: "user", content: "text match", createdAt: "x" },
          ],
        });
      }),
    );

    const client = newClient();
    const all = await client.shortTerm.searchMessages("editor", { sessionId: "c1", limit: 5 });
    const filtered = await client.shortTerm.searchMessages("editor", { sessionId: "c1", threshold: 0.5 });
    await client.close();

    expect(bodies).toEqual([{ query: "editor", limit: 5 }, { query: "editor", limit: 10 }]);
    expect(all.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
    expect(all[0]!.score).toBe(0.683);
    expect(filtered.map((m) => m.id)).toEqual(["m1", "m3"]);
  });

  it("sends and reads message metadata keys as written", async () => {
    const metadata = { namsKind: "tool-call", source_app: "web", nested: { innerKey: 1 } };
    let observedBody: { metadata?: unknown } = {};
    server.use(
      http.post(`${ENDPOINT}/conversations/c1/messages`, async ({ request }) => {
        observedBody = (await request.json()) as { metadata?: unknown };
        return HttpResponse.json({
          id: "m1", role: "assistant", content: "x", createdAt: "x", metadata: observedBody.metadata,
        });
      }),
      http.get(`${ENDPOINT}/conversations/c1/messages`, () =>
        HttpResponse.json({
          messages: [{ id: "m1", role: "assistant", content: "x", createdAt: "x", metadata }],
        }),
      ),
    );

    const client = newClient();
    const added = await client.shortTerm.addMessage("c1", "assistant", "x", { metadata });
    const conversation = await client.shortTerm.getConversation("c1");
    await client.close();

    expect(observedBody.metadata).toEqual(metadata);
    expect(added.metadata).toEqual(metadata);
    expect(conversation.messages[0]!.metadata).toEqual(metadata);
  });
});

describe("RestTransport — merged entity creation", () => {
  const entityId = "e1";
  const mergedResponse = { id: entityId, resolution: "merged", mergedInto: entityId, confidence: 0.93 };

  function mockMergedRead(response: () => Response) {
    const created = vi.fn(() => HttpResponse.json(mergedResponse));
    const fetched = vi.fn(response);
    server.use(
      http.post(`${ENDPOINT}/entities`, created),
      http.get(`${ENDPOINT}/entities/${entityId}`, fetched),
    );
    return { created, fetched };
  }

  it("fetches and validates the camelCase canonical response without replacing entity confidence", async () => {
    const calls = mockMergedRead(() => HttpResponse.json({
      id: entityId, name: "Alice", type: "person", confidence: 0.5,
      createdAt: "2026-09-10T12:00:00Z", updatedAt: null,
      metadata: { source: "original", nested: { retained: null } },
      relationships: [{
        id: "r1", relationshipType: "KNOWS", relType: "KNOWS", direction: "in",
        targetId: "e2", targetName: null, targetType: "person",
      }],
    }));
    const client = newClient();
    const entity = await client.longTerm.addEntity("Alice Smith", "person");
    await client.close();
    expect(entity).toMatchObject({ id: entityId, name: "Alice", type: "person", confidence: 0.5 });
    expect(entity.updatedAt).toBeUndefined();
    expect(entity.relationships?.[0]).toMatchObject({ type: "KNOWS", direction: "in", targetType: "person" });
    expect(entity.relationships?.[0]?.targetName).toBeUndefined();
    expect(entity.relationships?.[0]?.properties).toBeUndefined();
    expect(entity.metadata).toEqual({
      source: "original", nested: { retained: null },
      nams_resolution: { resolution: "merged", merged_into: entityId, merge_confidence: 0.93, fallback: false },
    });
    expect(calls.created).toHaveBeenCalledTimes(1);
    expect(calls.fetched).toHaveBeenCalledTimes(1);
  });

  it("falls back after the actual REST transport maps HTTP 404 to TransportError", async () => {
    const calls = mockMergedRead(() => HttpResponse.json({ error: "not found" }, { status: 404 }));
    const client = newClient();
    const entity = await client.longTerm.addEntity("Alice Smith", "PERSON");
    await client.close();
    expect(entity).toMatchObject({ id: entityId, name: "Alice Smith", type: "person" });
    expect(entity.confidence).toBeUndefined();
    expect(Number.isFinite(Date.parse(entity.createdAt))).toBe(true);
    expect(entity.metadata?.nams_resolution).toEqual({
      resolution: "merged", merged_into: entityId, merge_confidence: 0.93, fallback: true,
    });
    expect(calls.created).toHaveBeenCalledTimes(1);
    expect(calls.fetched).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["204", () => new HttpResponse(null, { status: 204 })],
    ["empty 200", () => new HttpResponse(null, { status: 200 })],
    ["null 200", () => HttpResponse.json(null)],
    ["empty object", () => HttpResponse.json({})],
    ["missing name", () => HttpResponse.json({ id: entityId, metadata: { retained: null } })],
  ] as const)("falls back for a %s canonical response", async (_, response) => {
    mockMergedRead(response);
    const client = newClient();
    const entity = await client.longTerm.addEntity("Alice Smith", "person");
    await client.close();
    expect(entity).toMatchObject({ id: entityId, name: "Alice Smith", type: "person" });
    expect(entity.metadata?.nams_resolution).toHaveProperty("fallback", true);
    if (_ === "missing name") expect(entity.metadata).toHaveProperty("retained", null);
  });

  it.each([401, 403, 429, 500, 503])("propagates HTTP %s from the canonical read with requestId", async (status) => {
    mockMergedRead(() => HttpResponse.json({ error: "canonical read failed" }, {
      status, headers: { "x-request-id": "canonical-request" },
    }));
    const client = newClient();
    try {
      await expect(client.longTerm.addEntity("Alice Smith", "person")).rejects.toMatchObject({
        name: status === 401 || status === 403 ? "AuthenticationError" : "TransportError",
        requestId: "canonical-request",
        statusCode: status,
      });
    } finally {
      await client.close();
    }
  });

  it("propagates network failures from the canonical read", async () => {
    mockMergedRead(() => HttpResponse.error());
    const client = newClient();
    await expect(client.longTerm.addEntity("Alice Smith", "person")).rejects.toBeInstanceOf(ConnectionError);
    await client.close();
  });

  it("propagates invalid JSON from the canonical read", async () => {
    mockMergedRead(() => new HttpResponse("{", { headers: { "Content-Type": "application/json" } }));
    const client = newClient();
    await expect(client.longTerm.addEntity("Alice Smith", "person")).rejects.toBeInstanceOf(SyntaxError);
    await client.close();
  });

  it("rejects a populated malformed canonical payload", async () => {
    mockMergedRead(() => HttpResponse.json({ id: entityId, name: "Alice", type: "person", createdAt: null }));
    const client = newClient();
    await expect(client.longTerm.addEntity("Alice Smith", "person")).rejects.toBeInstanceOf(ValidationError);
    await client.close();
  });
});

describe("RestTransport — error handling", () => {
  it("returns AuthenticationError on 401", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations`, () =>
        HttpResponse.json({ error: "bad token" }, { status: 401 }),
      ),
    );

    const client = newClient();
    await expect(client.shortTerm.listConversations()).rejects.toBeInstanceOf(
      AuthenticationError,
    );
    await client.close();
  });

  it("returns TransportError on 500 with status code", async () => {
    server.use(
      http.post(`${ENDPOINT}/conversations`, () =>
        HttpResponse.json({ error: "boom" }, { status: 500 }),
      ),
    );

    const client = newClient();
    try {
      await client.shortTerm.createConversation({ userId: "x" });
      throw new Error("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(TransportError);
      expect((err as TransportError).statusCode).toBe(500);
    }
    await client.close();
  });

  it("returns NotFoundError on 404", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/:id`, () =>
        HttpResponse.json({ error: "entity not found" }, { status: 404 }),
      ),
    );

    const client = newClient();
    await expect(
      client.longTerm.getEntity("ent-00000000-0000-0000-0000-000000000000"),
    ).rejects.toBeInstanceOf(NotFoundError);
    await client.close();
  });

  it("legacy bridge-only methods throw NotSupportedError", async () => {
    const client = newClient();
    await expect(
      client.longTerm.addPreference("style", "concise"),
    ).rejects.toBeInstanceOf(NotSupportedError);
    await client.close();
  });
});

describe("RestTransport — auth flows", () => {
  it("calls tokenProvider per request", async () => {
    let observed: string[] = [];
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) => {
        observed.push(request.headers.get("authorization") ?? "");
        return HttpResponse.json({ conversations: [] });
      }),
    );

    let n = 0;
    const provider = vi.fn(async () => `nams_token_${++n}`);
    const client = new MemoryClient({ endpoint: ENDPOINT, tokenProvider: provider });

    await client.shortTerm.listConversations();
    await client.shortTerm.listConversations();
    await client.close();

    expect(provider).toHaveBeenCalledTimes(2);
    expect(observed).toEqual(["Bearer nams_token_1", "Bearer nams_token_2"]);
  });
});
