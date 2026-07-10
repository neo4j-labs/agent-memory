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

  it("addRelationship POSTs /relationships and maps the response", async () => {
    let observedBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/relationships`, async ({ request }) => {
        observedBody = await request.json();
        return HttpResponse.json(
          {
            id: "rel-1",
            sourceId: "e1",
            targetId: "e2",
            relationshipType: "WORKS_AT",
            confidence: 1,
            created: true,
          },
          { status: 201 },
        );
      }),
    );

    const client = newClient();
    const rel = await client.longTerm.addRelationship("e1", "e2", "WORKS_AT", {
      properties: { since: "2023" },
    });
    await client.close();

    expect(observedBody).toEqual({
      sourceId: "e1",
      targetId: "e2",
      relationshipType: "WORKS_AT",
      properties: { since: "2023" },
    });
    expect(rel.id).toBe("rel-1");
    expect(rel.sourceId).toBe("e1");
    expect(rel.targetId).toBe("e2");
    expect(rel.relationshipType).toBe("WORKS_AT");
    expect(rel.properties).toEqual({ since: "2023" });
  });

  it("addRelationship passes through the server's collapsed type", async () => {
    // A type outside the workspace allowlist collapses to RELATED_TO server-side
    // (original name kept as `predicate`). The SDK reports the WRITTEN type.
    server.use(
      http.post(`${ENDPOINT}/relationships`, () =>
        HttpResponse.json({
          id: "rel-2",
          sourceId: "e1",
          targetId: "e2",
          relationshipType: "RELATED_TO",
          predicate: "COLLABORATES_WITH",
          confidence: 0.8,
          created: false,
        }),
      ),
    );

    const client = newClient();
    const rel = await client.longTerm.addRelationship("e1", "e2", "COLLABORATES_WITH");
    await client.close();

    expect(rel.relationshipType).toBe("RELATED_TO");
    expect(rel.properties).toEqual({});
  });

  it("getEntityByName GETs /entities/by-name and returns the first match", async () => {
    let observedName: string | null = null;
    server.use(
      http.get(`${ENDPOINT}/entities/by-name`, ({ request }) => {
        observedName = new URL(request.url).searchParams.get("name");
        return HttpResponse.json({
          entities: [
            {
              id: "e1",
              name: "Acme Corp",
              type: "organization",
              description: "The best match",
              confidence: 0.9,
              sourceStage: "extraction",
              createdAt: "2026-05-07T00:00:00Z",
              updatedAt: "2026-05-08T00:00:00Z",
              matchKind: "alias",
              resolvedFrom: "ACME",
            },
            { id: "e2", name: "Acme Corp", type: "person" },
          ],
        });
      }),
    );

    const client = newClient();
    const entity = await client.longTerm.getEntityByName("acme");
    await client.close();

    expect(observedName).toBe("acme");
    expect(entity).not.toBeNull();
    expect(entity!.id).toBe("e1");
    expect(entity!.name).toBe("Acme Corp");
    expect(entity!.type).toBe("organization");
    expect(entity!.description).toBe("The best match");
    expect(entity!.confidence).toBe(0.9);
    expect(entity!.sourceStage).toBe("extraction");
    expect(entity!.createdAt).toBe("2026-05-07T00:00:00Z");
    expect(entity!.updatedAt).toBe("2026-05-08T00:00:00Z");
  });

  it("getEntityByName returns null when the list is empty", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/by-name`, () => HttpResponse.json({ entities: [] })),
    );

    const client = newClient();
    const entity = await client.longTerm.getEntityByName("nobody");
    await client.close();

    expect(entity).toBeNull();
  });

  it("getRelatedEntities maps inline relationships to entities", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/e1`, () =>
        HttpResponse.json({
          id: "e1",
          name: "Alice",
          type: "person",
          relationships: [
            { relType: "WORKS_AT", targetId: "e2", targetName: "Acme", targetType: "organization" },
            { relType: "KNOWS", targetId: "e3", targetName: "Bob", targetType: "person" },
          ],
        }),
      ),
    );

    const client = newClient();
    const related = await client.longTerm.getRelatedEntities("e1");
    await client.close();

    expect(related).toHaveLength(2);
    expect(related[0]).toMatchObject({ id: "e2", name: "Acme", type: "organization" });
    expect(related[1]).toMatchObject({ id: "e3", name: "Bob", type: "person" });
  });

  it("getRelatedEntities filters by relationshipType", async () => {
    server.use(
      http.get(`${ENDPOINT}/entities/e1`, () =>
        HttpResponse.json({
          id: "e1",
          name: "Alice",
          type: "person",
          relationships: [
            { relType: "WORKS_AT", targetId: "e2", targetName: "Acme", targetType: "organization" },
            { relType: "KNOWS", targetId: "e3", targetName: "Bob", targetType: "person" },
          ],
        }),
      ),
    );

    const client = newClient();
    const related = await client.longTerm.getRelatedEntities("e1", {
      relationshipType: "KNOWS",
    });
    await client.close();

    expect(related).toHaveLength(1);
    expect(related[0]).toMatchObject({ id: "e3", name: "Bob", type: "person" });
  });

  it("getRelatedEntities throws NotSupportedError for depth > 1 without fetching", async () => {
    // No handler registered — an HTTP call would trip onUnhandledRequest: "error".
    const client = newClient();
    await expect(
      client.longTerm.getRelatedEntities("e1", { depth: 2 }),
    ).rejects.toBeInstanceOf(NotSupportedError);
    await client.close();
  });

  it("mergeDuplicateEntities composes merge + fetch using the canonical target", async () => {
    const calls: string[] = [];
    let mergeBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/entities/e-src/merge`, async ({ request }) => {
        mergeBody = await request.json();
        calls.push("merge");
        // The service resolves the requested target to its canonical entity.
        return HttpResponse.json({ status: "merged", sourceId: "e-src", targetId: "e-canon" });
      }),
      http.get(`${ENDPOINT}/entities/e-canon`, () => {
        calls.push("get");
        return HttpResponse.json({
          id: "e-canon",
          name: "Acme Corp",
          type: "organization",
          createdAt: "2026-05-07T00:00:00Z",
        });
      }),
    );

    const client = newClient();
    const entity = await client.longTerm.mergeDuplicateEntities("e-src", "e-tgt");
    await client.close();

    expect(mergeBody).toEqual({ targetId: "e-tgt" });
    expect(calls).toEqual(["merge", "get"]);
    expect(entity.id).toBe("e-canon");
    expect(entity.name).toBe("Acme Corp");
  });

  it("mergeDuplicateEntities renames the canonical target when canonicalName is given", async () => {
    const calls: string[] = [];
    let putBody: unknown = null;
    server.use(
      http.post(`${ENDPOINT}/entities/e-src/merge`, () => {
        calls.push("merge");
        return HttpResponse.json({ status: "merged", sourceId: "e-src", targetId: "e-canon" });
      }),
      http.put(`${ENDPOINT}/entities/e-canon`, async ({ request }) => {
        putBody = await request.json();
        calls.push("rename");
        return HttpResponse.json({ status: "updated" });
      }),
      http.get(`${ENDPOINT}/entities/e-canon`, () => {
        calls.push("get");
        return HttpResponse.json({
          id: "e-canon",
          name: "ACME Corporation",
          type: "organization",
        });
      }),
    );

    const client = newClient();
    const entity = await client.longTerm.mergeDuplicateEntities("e-src", "e-tgt", {
      canonicalName: "ACME Corporation",
    });
    await client.close();

    expect(calls).toEqual(["merge", "rename", "get"]);
    expect(putBody).toEqual({ name: "ACME Corporation" });
    expect(entity.name).toBe("ACME Corporation");
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
