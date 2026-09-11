/**
 * A fake hosted Neo4j Agent Memory Service, implemented at the `fetch` layer.
 *
 * This is deliberately *not* a fake `Transport`. The whole claim of this example
 * is that the SDK's REST transport works on the edge, so the tests have to drive
 * that transport for real — URLs, HTTP verbs, `Authorization` header, camelCase
 * request bodies, the snake_case normalisation on the way back. Swapping the
 * transport out would mock away the thing under test.
 *
 * So instead we stub `globalThis.fetch` inside the `workerd` isolate (the test
 * runner and the Worker share one isolate under `@cloudflare/vitest-pool-workers`,
 * so the Worker sees the stub) and answer the hosted REST API's own routes.
 *
 * Only the routes this Worker calls are implemented. Anything else answers 501,
 * so an example that drifts onto a new endpoint fails loudly instead of silently
 * returning `undefined`.
 */

/** One recorded call, for assertions about what the Worker actually did. */
export interface RecordedCall {
  method: string;
  path: string;
  body?: unknown;
  authorization?: string;
}

interface StoredMessage {
  id: string;
  role: string;
  content: string;
  createdAt: string;
}

interface StoredConversation {
  id: string;
  userId: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  messages: StoredMessage[];
}

interface StoredStep {
  id: string;
  conversationId: string;
  reasoning: string;
  actionTaken: string;
  result: string;
  createdAt: string;
}

interface StoredToolCall {
  id: string;
  stepId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result: unknown;
  status: string;
  durationMs?: number;
}

export const FAKE_ENDPOINT = "https://nams.test/v1";

export class FakeNams {
  readonly calls: RecordedCall[] = [];
  readonly conversations = new Map<string, StoredConversation>();
  readonly steps: StoredStep[] = [];
  readonly toolCalls: StoredToolCall[] = [];

  /** What the fake extraction pipeline has "found" so far. */
  entities: Array<{ id: string; name: string; type: string }> = [
    { id: "ent-kyoto", name: "Kyoto", type: "LOCATION" },
    { id: "ent-ryokan", name: "Ryokan Tawaraya", type: "ORGANIZATION" },
  ];

  private counter = 0;

  private nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter}`;
  }

  /** Roles stored on a conversation, in write order. */
  rolesOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.role);
  }

  /** Contents stored on a conversation, in write order. */
  contentsOf(conversationId: string): string[] {
    return (this.conversations.get(conversationId)?.messages ?? []).map((m) => m.content);
  }

  /** Paths hit, as `"POST /conversations"` strings. */
  trace(): string[] {
    return this.calls.map((c) => `${c.method} ${c.path}`);
  }

  /** Drop-in for `globalThis.fetch`. */
  readonly fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);

    if (!request.url.startsWith(FAKE_ENDPOINT)) {
      throw new Error(
        `FakeNams: unexpected outbound request to ${request.url}. ` +
          `The Worker should only talk to ${FAKE_ENDPOINT} (the model is mocked).`,
      );
    }

    const path = url.pathname.replace(/^\/v1/, "");
    const bodyText = request.method === "GET" || request.method === "DELETE" ? "" : await request.text();
    const body: unknown = bodyText ? JSON.parse(bodyText) : undefined;
    this.calls.push({
      method: request.method,
      path,
      body,
      authorization: request.headers.get("Authorization") ?? undefined,
    });

    const payload = this.route(request.method, path, body);
    if (payload === undefined) {
      return new Response(JSON.stringify({ error: `FakeNams: no route for ${request.method} ${path}` }), {
        status: 501,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Request-Id": this.nextId("req") },
    });
  };

  /**
   * The hosted REST API speaks camelCase on the wire in both directions; the
   * SDK's transport normalises responses to the bridge's snake_case. So
   * everything returned from here is camelCase, exactly like the real service.
   */
  private route(method: string, path: string, body: unknown): unknown {
    const b = (body ?? {}) as Record<string, unknown>;

    // ---- Short-term ------------------------------------------------------
    if (method === "POST" && path === "/conversations") {
      const conv: StoredConversation = {
        id: this.nextId("conv"),
        userId: String(b["userId"] ?? "anonymous"),
        metadata: (b["metadata"] as Record<string, unknown>) ?? {},
        createdAt: new Date().toISOString(),
        messages: [],
      };
      this.conversations.set(conv.id, conv);
      return {
        id: conv.id,
        userId: conv.userId,
        metadata: conv.metadata,
        createdAt: conv.createdAt,
        messageCount: 0,
      };
    }

    if (method === "GET" && path === "/conversations") {
      return {
        conversations: [...this.conversations.values()].map((c) => ({
          id: c.id,
          userId: c.userId,
          metadata: c.metadata,
          createdAt: c.createdAt,
          messageCount: c.messages.length,
        })),
      };
    }

    const messages = /^\/conversations\/([^/]+)\/messages$/.exec(path);
    if (method === "POST" && messages) {
      const conv = this.conversation(messages[1]!);
      const stored: StoredMessage = {
        id: this.nextId("msg"),
        role: String(b["role"]),
        content: String(b["content"]),
        createdAt: new Date().toISOString(),
      };
      conv.messages.push(stored);
      return stored;
    }
    if (method === "GET" && messages) {
      return { messages: this.conversation(messages[1]!).messages };
    }

    const context = /^\/conversations\/([^/]+)\/context$/.exec(path);
    if (method === "GET" && context) {
      const conv = this.conversation(context[1]!);
      return {
        reflections: [],
        // NAMS only produces an observation once a conversation has a few
        // turns, so mirror that: it keeps the "second turn is richer" assertion
        // meaningful instead of constant.
        observations:
          conv.messages.length >= 2
            ? [
                {
                  id: "obs-1",
                  conversationId: conv.id,
                  content: "Traveller is planning a trip to Kyoto.",
                  createdAt: new Date().toISOString(),
                },
              ]
            : [],
        recentMessages: conv.messages,
      };
    }

    // ---- Reasoning -------------------------------------------------------
    if (method === "POST" && path === "/reasoning/steps") {
      const step: StoredStep = {
        id: this.nextId("step"),
        conversationId: String(b["conversationId"]),
        reasoning: String(b["reasoning"] ?? ""),
        actionTaken: String(b["actionTaken"] ?? ""),
        result: String(b["result"] ?? ""),
        createdAt: new Date().toISOString(),
      };
      this.steps.push(step);
      return step;
    }

    if (method === "POST" && path === "/reasoning/tool-calls") {
      const call: StoredToolCall = {
        id: this.nextId("tc"),
        stepId: String(b["stepId"]),
        toolName: String(b["toolName"]),
        arguments: (b["arguments"] as Record<string, unknown>) ?? {},
        result: b["result"],
        status: String(b["status"] ?? "success"),
        durationMs: typeof b["durationMs"] === "number" ? b["durationMs"] : undefined,
      };
      this.toolCalls.push(call);
      return call;
    }

    const trace = /^\/reasoning\/trace\/([^/]+)$/.exec(path);
    if (method === "GET" && trace) {
      const conversationId = trace[1]!;
      const steps = this.steps.filter((s) => s.conversationId === conversationId);
      const ids = new Set(steps.map((s) => s.id));
      return {
        conversationId,
        steps,
        toolCalls: this.toolCalls.filter((t) => ids.has(t.stepId)),
      };
    }

    // ---- Long-term / graph -----------------------------------------------
    if (method === "POST" && path === "/entities/search") {
      const query = String(b["query"] ?? "").toLowerCase();
      const limit = typeof b["limit"] === "number" ? b["limit"] : 10;
      const matches = this.entities.filter((e) => e.name.toLowerCase().includes(query));
      return { entities: (matches.length > 0 ? matches : this.entities).slice(0, limit) };
    }

    if (method === "GET" && path === "/entities/graph") {
      return {
        nodes: this.entities,
        edges: [{ id: "rel-1", source: "ent-ryokan", target: "ent-kyoto", type: "LOCATED_IN" }],
      };
    }

    if (method === "POST" && path === "/graph/expand") {
      const nodeId = String(b["nodeId"] ?? "");
      const loaded = new Set((b["loadedIds"] as string[] | undefined) ?? []);
      const neighbours = this.entities.filter((e) => e.id !== nodeId && !loaded.has(e.id));
      return {
        nodes: neighbours.map((e) => ({
          id: e.id,
          labels: ["Entity", e.type],
          properties: { name: e.name },
        })),
        edges: neighbours.map((e) => ({ id: `rel-${e.id}`, source: nodeId, target: e.id })),
      };
    }

    return undefined;
  }

  private conversation(id: string): StoredConversation {
    const conv = this.conversations.get(id);
    if (!conv) throw new Error(`FakeNams: unknown conversation "${id}"`);
    return conv;
  }
}
