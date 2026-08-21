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
import { AuthenticationError, NotSupportedError, TransportError } from "../../src/errors.js";

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
