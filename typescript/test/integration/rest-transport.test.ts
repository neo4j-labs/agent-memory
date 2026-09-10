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

  it("listConversations forwards the optional userId filter as user_id", async () => {
    let observedUserId: string | null = null;
    server.use(
      http.get(`${ENDPOINT}/conversations`, ({ request }) => {
        observedUserId = new URL(request.url).searchParams.get("user_id");
        return HttpResponse.json({ conversations: [] });
      }),
    );

    const client = newClient();
    await client.shortTerm.listConversations({ limit: 10, userId: "alice" });
    await client.close();

    expect(observedUserId).toBe("alice");
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
      relationships: [{ id: "r1", type: "KNOWS", targetId: "e2", targetName: null, properties: null }],
    }));
    const client = newClient();
    const entity = await client.longTerm.addEntity("Alice Smith", "person");
    await client.close();
    expect(entity).toMatchObject({ id: entityId, name: "Alice", type: "person", confidence: 0.5 });
    expect(entity.updatedAt).toBeUndefined();
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
        ...(status === 401 || status === 403 ? {} : { statusCode: status }),
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
